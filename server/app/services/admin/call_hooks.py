import json
import logging
import re
import time
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models.app_secret import AppSecret
from app.models.hook_log import HookLog
from app.models.meeting import CaptureMode, CopilotInsight, Meeting, TranscriptSegment
from app.services.copilot.report import ReportResult
from app.services.transcript_format import format_transcript

logger = logging.getLogger(__name__)

# Matches {{secret.NAME}} — NAME is the same character class
# app/schemas/app_secret.py:SECRET_NAME_RE accepts.
_SECRET_REF = re.compile(r"\{\{secret\.([A-Za-z][A-Za-z0-9_]*)\}\}")

_REDACTED = "••••"
_MAX_LOG_CHARS = 20_000
# Classic credential-ish header names — values never land in hook_logs.
_SENSITIVE_HEADER = re.compile(
    r"^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|"
    r"x-.*(?:secret|token|key|password)|.*(?:secret|token|password|authorization))$",
    re.I,
)
_BEARER = re.compile(r"(?i)(bearer\s+)\S+")


def request_error_message(exc: BaseException, url: str) -> str:
    """Admin-facing reason for an httpx transport failure.

    The raw errno string ("Name or service not known") is what we used to
    dump in logs; it does not say *which* hostname failed or that the
    lookup ran inside the api/worker container.
    """
    raw = str(exc) or exc.__class__.__name__
    lowered = raw.lower()
    host = urlparse(url).hostname
    target = f"{host!r}" if host else url
    if (
        "name or service not known" in lowered
        or "nodename nor servname" in lowered
        or "getaddrinfo failed" in lowered
    ):
        return (
            f"Cannot resolve hostname {target}. The Corella container's DNS "
            "does not know this name. localhost is this container, not the host. "
            "On Linux Docker, host.docker.internal only works with extra_hosts "
            "host-gateway. In production use a public URL the container can resolve."
        )
    if "connection refused" in lowered:
        return f"Connection refused for {url}."
    if "timed out" in lowered or "timeout" in lowered:
        return f"Timed out calling {url}."
    return raw


def _contains_secret(text: str, secret_values: list[str]) -> bool:
    return any(secret and secret in text for secret in secret_values)


def redact_headers(headers: dict[str, str], secret_values: list[str]) -> str:
    """JSON object of headers with secrets / credential-named keys masked."""
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if _SENSITIVE_HEADER.match(key) or _contains_secret(value, secret_values):
            redacted[key] = _REDACTED
        else:
            redacted[key] = value
    return json.dumps(redacted)


def redact_text(text: str | None, secret_values: list[str]) -> str | None:
    """Strip known secret values and Bearer tokens from a request/response body."""
    if text is None:
        return None
    redacted = text
    for secret in sorted((value for value in secret_values if value), key=len, reverse=True):
        redacted = redacted.replace(secret, _REDACTED)
    redacted = _BEARER.sub(rf"\1{_REDACTED}", redacted)
    return redacted[:_MAX_LOG_CHARS]


async def _secret_values_for_headers(
    db: AsyncSession, encrypted: str | None, organization_id: UUID
) -> list[str]:
    """Decrypt just the AppSecret values referenced by a header template,
    so dispatch can redact them wherever they appear in the persisted log.
    """
    if not encrypted:
        return []
    try:
        parsed = json.loads(decrypt_secret(encrypted))
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []
    names: set[str] = set()
    for value in parsed.values():
        if isinstance(value, str):
            names.update(_SECRET_REF.findall(value))
    if not names:
        return []
    rows = await db.scalars(
        select(AppSecret).where(
            AppSecret.name.in_(names),
            AppSecret.organization_id == organization_id,
        )
    )
    values: list[str] = []
    for secret in rows:
        try:
            values.append(decrypt_secret(secret.value_encrypted))
        except Exception:
            continue
    return values


async def persist_hook_log(
    db: AsyncSession,
    meeting: Meeting,
    phase: str,
    *,
    outcome: str,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    request_body: str | None = None,
    response_status: int | None = None,
    response_body: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    secret_values: list[str] | None = None,
) -> HookLog:
    """Append a redacted hook_logs row. Caller commits."""
    call_type = meeting.call_type
    ran_async = False
    if call_type is not None:
        ran_async = bool(call_type.pre_call_async if phase == "pre" else call_type.post_call_async)
    secrets = secret_values or []
    log = HookLog(
        meeting_id=meeting.id,
        phase=phase,
        outcome=outcome,
        method=method,
        url=url,
        request_headers=redact_headers(headers, secrets) if headers is not None else None,
        request_body=redact_text(request_body, secrets),
        response_status=response_status,
        response_body=redact_text(response_body, secrets),
        error=(error[:_MAX_LOG_CHARS] if error else None),
        duration_ms=duration_ms,
        ran_async=ran_async,
    )
    db.add(log)
    await db.flush()
    return log


async def record_hook_queue_failure(db: AsyncSession, meeting: Meeting, phase: str) -> None:
    """When celery_app.send_task can't even hand the job to Redis."""
    call_type = meeting.call_type
    method = "GET"
    url = ""
    if call_type is not None:
        if phase == "pre":
            method = call_type.pre_call_method or "GET"
            url = call_type.pre_call_url or ""
        else:
            method = call_type.post_call_method or "POST"
            url = call_type.post_call_url or ""
    await persist_hook_log(
        db,
        meeting,
        phase,
        outcome="error",
        method=method,
        url=url,
        error="Could not queue this hook — the background worker is unreachable.",
    )


async def resolve_custom_headers(
    db: AsyncSession, encrypted: str | None, organization_id: UUID
) -> dict[str, str] | None:
    """Decrypt stored header JSON and replace {{secret.NAME}} tokens with
    the matching AppSecret value. Returns an empty dict when nothing is
    configured. Returns None (caller should abort the hook) when the blob
    is unreadable or a referenced secret is missing — sending a request
    without the auth header would look like success and leak a 401 to
    the far side.
    """
    if not encrypted:
        return {}
    try:
        parsed = json.loads(decrypt_secret(encrypted))
    except Exception:
        logger.exception("Failed to decrypt/parse call-type headers")
        return None
    if not isinstance(parsed, dict):
        logger.warning("Call-type headers were not a JSON object")
        return None

    names: set[str] = set()
    for value in parsed.values():
        if isinstance(value, str):
            names.update(_SECRET_REF.findall(value))

    values: dict[str, str] = {}
    if names:
        rows = await db.scalars(
        select(AppSecret).where(
            AppSecret.name.in_(names),
            AppSecret.organization_id == organization_id,
        )
    )
        for secret in rows:
            try:
                values[secret.name] = decrypt_secret(secret.value_encrypted)
            except Exception:
                logger.exception("Failed to decrypt app secret %s", secret.name)
                return None
        missing = names - values.keys()
        if missing:
            logger.warning("Call-type headers reference unknown secrets: %s", ", ".join(sorted(missing)))
            return None

    resolved: dict[str, str] = {}
    for key, value in parsed.items():
        text = value if isinstance(value, str) else str(value)
        resolved[str(key)] = _SECRET_REF.sub(lambda match: values[match.group(1)], text)
    return resolved


def _mandatory_headers(meeting_id: UUID, owner_id: UUID, organization_id: UUID | None = None) -> dict[str, str]:
    """The headers every pre/post call-type request carries, non-negotiable
    — applied *after* the admin's own decrypted custom headers are merged
    in, so a custom header that happens to reuse one of these names still
    can't shadow it.
    """
    headers = {
        "X-Corella-App-Url": get_settings().public_app_url,
        "X-Corella-Meeting-Id": str(meeting_id),
        "X-Corella-User-Id": str(owner_id),
    }
    if organization_id is not None:
        headers["X-Corella-Org-Id"] = str(organization_id)
    return headers


def _json_value(value) -> str:
    """json.dumps(value), with the outer quotes stripped for a plain
    string — so a {{placeholder}} sitting inside "..." in the admin's own
    template substitutes a properly escaped value (quotes/newlines in a
    summary can't break the surrounding JSON) without doubling up quotes.
    Non-string values (arrays, numbers, null, and full_payload's own
    object) are inserted as-is, valid JSON on their own — the admin's
    template should place those placeholders *outside* a quoted string.
    """
    dumped = json.dumps(value)
    if isinstance(value, str):
        return dumped[1:-1]
    return dumped


def _apply_placeholders(template: str, values: dict) -> str:
    """Replace {{corella.KEY}} tokens only. Unprefixed {{KEY}} is left
    as-is — body templates must use the corella namespace.
    """
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{corella." + key + "}}", _json_value(value))
    return rendered


async def _build_transcript_text(db: AsyncSession, meeting: Meeting) -> str:
    segments = list(
        await db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == meeting.id)
            .order_by(TranscriptSegment.start_ms)
        )
    )
    return format_transcript(
        segments,
        owner_id=meeting.owner_id,
        capture_mode=meeting.capture_mode or CaptureMode.OPEN_MIC,
    )


async def _build_copilot_insights(db: AsyncSession, meeting_id: UUID) -> list[dict]:
    """The persisted live-copilot timeline (app/models/meeting.py:
    CopilotInsight) — same rows GET /{meeting_id}/insights returns,
    timestamp-ordered, for a receiving system to reconstruct the
    suggestion/blocker/score-over-time view this app itself shows on
    MeetingDetail.
    """
    insights = await db.scalars(
        select(CopilotInsight).where(CopilotInsight.meeting_id == meeting_id).order_by(CopilotInsight.at_ms)
    )
    return [
        {
            "at_ms": i.at_ms,
            "suggestion": i.suggestion,
            "blockers": i.blockers,
            "coach_score": i.coach_score,
        }
        for i in insights
    ]


async def build_full_payload(db: AsyncSession, meeting: Meeting, report: ReportResult) -> dict:
    """Everything this app knows about a finished call, as a plain dict —
    used directly when a call type's post_call_send_full_payload is on,
    and also what {{corella.full_payload}} expands to inside a
    hand-written body template. "revisions" from the original
    ask is interpreted as action items' open/done status (the closest
    tracked concept — there's no edit-history on a report itself),
    included here per item.
    """
    return {
        "meeting_id": str(meeting.id),
        "owner_id": str(meeting.owner_id),
        "owner_name": meeting.owner_name,
        "title": report.title,
        "call_type": meeting.call_type.name if meeting.call_type else None,
        "capture_mode": (meeting.capture_mode or CaptureMode.OPEN_MIC).value,
        "capture_app": meeting.capture_app.value if meeting.capture_app else None,
        "status": meeting.status.value,
        "summary": report.summary,
        "key_topics": report.key_topics,
        "sentiment": report.sentiment,
        "notable_quotes": report.notable_quotes,
        "coach_score": report.coach_score,
        "estimated_cost_usd": report.estimated_cost_usd,
        "talk_ratio": report.talk_ratio,
        "action_items": [{"text": item.text, "status": item.status.value} for item in report.action_items],
        "copilot_insights": await _build_copilot_insights(db, meeting.id),
        "transcript": await _build_transcript_text(db, meeting),
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
        "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
        "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
        "duration_seconds": meeting.duration_seconds,
    }


async def render_template(db: AsyncSession, template: str, meeting: Meeting, report: ReportResult) -> str:
    """Substitutes {{corella.KEY}} tokens in an admin-authored call-hook
    body template with real meeting/report data. Keys:
    meeting_id, owner_id, owner_name, title, call_type, capture_mode,
    capture_app, status, summary,
    key_topics, sentiment, notable_quotes, coach_score, estimated_cost_usd,
    talk_ratio, action_items (now [{text, status}]), copilot_insights,
    transcript, created_at, started_at, ended_at, duration_seconds, and
    full_payload — the entire build_full_payload() dict, for a template
    that just wants everything without ticking the separate
    post_call_send_full_payload flag.
    """
    payload = await build_full_payload(db, meeting, report)
    return _apply_placeholders(template, {**payload, "full_payload": payload})


def render_pre_call_template(template: str, meeting: Meeting) -> str:
    """Substitutes {{corella.KEY}} tokens in an admin-authored pre-call
    body template — a much smaller placeholder set than render_template's
    (post-call) one, since a pre-call fires before any transcript/report
    exists: meeting_id, owner_id, owner_name, title, call_type,
    capture_mode, capture_app, status, created_at only. No DB access
    needed (unlike render_template), so this isn't async.
    """
    values = {
        "meeting_id": str(meeting.id),
        "owner_id": str(meeting.owner_id),
        "owner_name": meeting.owner_name,
        "title": meeting.title,
        "call_type": meeting.call_type.name if meeting.call_type else None,
        "capture_mode": (meeting.capture_mode or CaptureMode.OPEN_MIC).value,
        "capture_app": meeting.capture_app.value if meeting.capture_app else None,
        "status": meeting.status.value,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
    }
    return _apply_placeholders(template, values)


async def dispatch_pre_call(db: AsyncSession, meeting: Meeting) -> str | None:
    """Fires the admin-configured pre-call for this meeting's call type,
    if one is enabled — a no-op (returns None, not an error) when there's
    no type, it has no pre-call enabled, or no URL.

    Sync path: awaited from create_meeting (bounded by
    settings.pre_call_timeout_seconds). Async path: the same function,
    run from the corella.dispatch_pre_call Celery task so create can
    return while the lookup is still in flight. Live copilot re-reads
    Meeting.pre_call_context each cycle, so a late response still
    becomes context if pre_call_use_as_context is on.

    pre_call_body_template (when set) is substituted via
    render_pre_call_template before sending — a real case for a POST
    pre-call, e.g. {"lookup": "{{corella.owner_name}}"} for a CRM query.

    Any failure — bad URL, malformed headers, timeout, connection error,
    non-2xx — is logged (hook_logs + logger) and swallowed, never raised:
    meeting creation must never fail because an external pre-call is
    broken or slow. On success, returns the response body text, capped
    at settings.pre_call_context_max_chars. Caller commits so the log
    (and optional pre_call_context) persist.
    """
    call_type = meeting.call_type
    if call_type is None or not call_type.pre_call_enabled or not call_type.pre_call_url:
        return None

    settings = get_settings()
    method = call_type.pre_call_method or "GET"
    url = call_type.pre_call_url
    secrets = await _secret_values_for_headers(
        db, call_type.pre_call_headers_encrypted, meeting.organization_id
    )

    custom = await resolve_custom_headers(
        db, call_type.pre_call_headers_encrypted, meeting.organization_id
    )
    if custom is None:
        logger.warning("Pre-call for meeting %s: headers could not be resolved", meeting.id)
        await persist_hook_log(
            db,
            meeting,
            "pre",
            outcome="error",
            method=method,
            url=url,
            error="Headers could not be resolved (missing or unknown secret).",
            secret_values=secrets,
        )
        return None
    headers = {**custom, **_mandatory_headers(meeting.id, meeting.owner_id, meeting.organization_id)}

    body: bytes | None = None
    body_text: str | None = None
    if call_type.pre_call_body_template:
        try:
            body_text = render_pre_call_template(call_type.pre_call_body_template, meeting)
            body = body_text.encode("utf-8")
        except Exception:
            logger.exception("Pre-call for meeting %s: failed to render body template", meeting.id)
            await persist_hook_log(
                db,
                meeting,
                "pre",
                outcome="error",
                method=method,
                url=url,
                headers=headers,
                error="Failed to render body template.",
                secret_values=secrets,
            )
            return None

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.pre_call_timeout_seconds) as client:
            response = await client.request(method, url, content=body, headers=headers)
    except httpx.RequestError as exc:
        logger.exception("Pre-call for meeting %s: request failed", meeting.id)
        await persist_hook_log(
            db,
            meeting,
            "pre",
            outcome="error",
            method=method,
            url=url,
            headers=headers,
            request_body=body_text,
            error=request_error_message(exc, url),
            duration_ms=int((time.monotonic() - started) * 1000),
            secret_values=secrets,
        )
        return None

    duration_ms = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        logger.warning(
            "Pre-call for meeting %s returned %s: %s",
            meeting.id,
            response.status_code,
            response.text[:500],
        )
        await persist_hook_log(
            db,
            meeting,
            "pre",
            outcome="error",
            method=method,
            url=url,
            headers=headers,
            request_body=body_text,
            response_status=response.status_code,
            response_body=response.text,
            error=f"HTTP {response.status_code}",
            duration_ms=duration_ms,
            secret_values=secrets,
        )
        return None

    logger.info("Pre-call for meeting %s dispatched successfully (%s)", meeting.id, response.status_code)
    context = response.text[: settings.pre_call_context_max_chars]
    await persist_hook_log(
        db,
        meeting,
        "pre",
        outcome="success",
        method=method,
        url=url,
        headers=headers,
        request_body=body_text,
        response_status=response.status_code,
        response_body=context,
        duration_ms=duration_ms,
        secret_values=secrets,
    )
    return context


async def dispatch_post_call(db: AsyncSession, meeting: Meeting, report: ReportResult) -> None:
    """Fires the admin-configured post-call for this meeting's call type,
    if one is configured — a no-op (not an error) when there's no type,
    it has no post-call enabled, or no URL. Called once, right after a
    *successful* automatic report generation (app/workers/tasks.py);
    never from the manual "Regenerate report" route — regenerating isn't
    "a conversation ending" a second time.

    Sync (default): awaited in the generate_report worker after the
    report is persisted. Async: the same function from
    corella.dispatch_post_call so report completion doesn't wait.

    Any failure here — a bad URL, a malformed body template, a connection
    error, a non-2xx response — is logged (hook_logs + logger) and
    swallowed, never raised. Caller commits so the log persists.
    """
    call_type = meeting.call_type
    if call_type is None or not call_type.post_call_enabled or not call_type.post_call_url:
        return

    settings = get_settings()
    method = call_type.post_call_method or "POST"
    url = call_type.post_call_url
    secrets = await _secret_values_for_headers(
        db, call_type.post_call_headers_encrypted, meeting.organization_id
    )

    try:
        if call_type.post_call_send_full_payload:
            body = json.dumps(await build_full_payload(db, meeting, report))
        else:
            body = await render_template(db, call_type.post_call_body_template or "{}", meeting, report)
    except Exception:
        logger.exception("Post-call for meeting %s: failed to build body", meeting.id)
        await persist_hook_log(
            db,
            meeting,
            "post",
            outcome="error",
            method=method,
            url=url,
            error="Failed to build request body.",
            secret_values=secrets,
        )
        return

    custom = await resolve_custom_headers(
        db, call_type.post_call_headers_encrypted, meeting.organization_id
    )
    if custom is None:
        logger.warning("Post-call for meeting %s: headers could not be resolved", meeting.id)
        await persist_hook_log(
            db,
            meeting,
            "post",
            outcome="error",
            method=method,
            url=url,
            request_body=body,
            error="Headers could not be resolved (missing or unknown secret).",
            secret_values=secrets,
        )
        return
    headers = {"Content-Type": "application/json", **custom, **_mandatory_headers(meeting.id, meeting.owner_id, meeting.organization_id)}

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.post_call_timeout_seconds) as client:
            response = await client.request(method, url, content=body.encode("utf-8"), headers=headers)
    except httpx.RequestError as exc:
        logger.exception("Post-call for meeting %s: request failed", meeting.id)
        await persist_hook_log(
            db,
            meeting,
            "post",
            outcome="error",
            method=method,
            url=url,
            headers=headers,
            request_body=body,
            error=request_error_message(exc, url),
            duration_ms=int((time.monotonic() - started) * 1000),
            secret_values=secrets,
        )
        return

    duration_ms = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        logger.warning(
            "Post-call for meeting %s returned %s: %s",
            meeting.id,
            response.status_code,
            response.text[:500],
        )
        await persist_hook_log(
            db,
            meeting,
            "post",
            outcome="error",
            method=method,
            url=url,
            headers=headers,
            request_body=body,
            response_status=response.status_code,
            response_body=response.text,
            error=f"HTTP {response.status_code}",
            duration_ms=duration_ms,
            secret_values=secrets,
        )
        return

    logger.info("Post-call for meeting %s dispatched successfully (%s)", meeting.id, response.status_code)
    await persist_hook_log(
        db,
        meeting,
        "post",
        outcome="success",
        method=method,
        url=url,
        headers=headers,
        request_body=body,
        response_status=response.status_code,
        response_body=response.text,
        duration_ms=duration_ms,
        secret_values=secrets,
    )
