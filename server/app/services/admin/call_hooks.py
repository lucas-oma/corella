import json
import logging
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models.meeting import Channel, CopilotInsight, Meeting, TranscriptSegment
from app.services.copilot.report import ReportResult

logger = logging.getLogger(__name__)

_LABELS = {Channel.ME: "Me", Channel.THEM: "Them"}


def _mandatory_headers(meeting_id: UUID, owner_id: UUID) -> dict[str, str]:
    """The three headers every pre/post call-type request carries,
    non-negotiable — applied *after* the admin's own decrypted custom
    headers are merged in (see dispatch_pre_call/dispatch_post_call), so
    a custom header that happens to reuse one of these names still can't
    shadow it.
    """
    return {
        "X-Corella-App-Url": get_settings().public_app_url,
        "X-Corella-Meeting-Id": str(meeting_id),
        "X-Corella-User-Id": str(owner_id),
    }


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


async def _build_transcript_text(db: AsyncSession, meeting_id: UUID) -> str:
    segments = list(
        await db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == meeting_id)
            .order_by(TranscriptSegment.start_ms)
        )
    )
    return "\n".join(f"{_LABELS.get(s.channel, 'Speaker')}: {s.text}" for s in segments)


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
    and also what {{full_payload}} expands to inside a hand-written body
    template. "revisions" from the original ask is interpreted as action
    items' open/done status (the closest tracked concept — there's no
    edit-history on a report itself), included here per item.
    """
    return {
        "meeting_id": str(meeting.id),
        "owner_id": str(meeting.owner_id),
        "owner_name": meeting.owner_name,
        "title": report.title,
        "call_type": meeting.call_type.name if meeting.call_type else None,
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
        "transcript": await _build_transcript_text(db, meeting.id),
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
        "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
        "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
        "duration_seconds": meeting.duration_seconds,
    }


async def render_template(db: AsyncSession, template: str, meeting: Meeting, report: ReportResult) -> str:
    """Substitutes {{placeholder}} tokens in an admin-authored call-hook
    body template with real meeting/report data. Supported placeholders:
    meeting_id, owner_id, owner_name, title, call_type, status, summary,
    key_topics, sentiment, notable_quotes, coach_score, estimated_cost_usd,
    talk_ratio, action_items (now [{text, status}]), copilot_insights,
    transcript, created_at, started_at, ended_at, duration_seconds, and
    full_payload — the entire build_full_payload() dict, for a template
    that just wants everything without ticking the separate
    post_call_send_full_payload flag.
    """
    payload = await build_full_payload(db, meeting, report)
    values = {**payload, "full_payload": payload}

    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", _json_value(value))
    return rendered


def render_pre_call_template(template: str, meeting: Meeting) -> str:
    """Substitutes {{placeholder}} tokens in an admin-authored pre-call
    body template — a much smaller placeholder set than render_template's
    (post-call) one, since a pre-call fires before any transcript/report
    exists: meeting_id, owner_id, owner_name, title, call_type, status,
    created_at only. No DB access needed (unlike render_template), so
    this isn't async.
    """
    values = {
        "meeting_id": str(meeting.id),
        "owner_id": str(meeting.owner_id),
        "owner_name": meeting.owner_name,
        "title": meeting.title,
        "call_type": meeting.call_type.name if meeting.call_type else None,
        "status": meeting.status.value,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", _json_value(value))
    return rendered


async def dispatch_pre_call(db: AsyncSession, meeting: Meeting) -> str | None:
    """Fires the admin-configured pre-call for this meeting's call type,
    if one is enabled — a no-op (returns None, not an error) when there's
    no type, it has no pre-call enabled, or no URL. Called synchronously
    from create_meeting (app/api/meetings.py) — "before the call starts"
    means the context needs to exist before the conversation begins, so a
    short bounded wait (settings.pre_call_timeout_seconds) is the correct
    semantic here, not something to route around.

    pre_call_body_template (when set) is substituted via
    render_pre_call_template before sending — a real case for a POST
    pre-call, e.g. {"lookup": "{{owner_name}}"} for a CRM query.

    Any failure — bad URL, malformed headers, timeout, connection error,
    non-2xx — is logged and swallowed, never raised: meeting creation
    must never fail because an external pre-call is broken or slow, same
    graceful-degradation discipline as dispatch_post_call below and every
    other best-effort side path in this codebase (Deepgram fallback,
    diarization skip). On success, returns the response body text,
    capped at settings.pre_call_context_max_chars.
    """
    call_type = meeting.call_type
    if call_type is None or not call_type.pre_call_enabled or not call_type.pre_call_url:
        return None

    settings = get_settings()
    headers: dict[str, str] = {}
    if call_type.pre_call_headers_encrypted:
        try:
            headers.update(json.loads(decrypt_secret(call_type.pre_call_headers_encrypted)))
        except Exception:
            logger.exception("Pre-call for meeting %s: failed to decrypt/parse headers", meeting.id)
            return None
    headers.update(_mandatory_headers(meeting.id, meeting.owner_id))

    body: bytes | None = None
    if call_type.pre_call_body_template:
        try:
            body = render_pre_call_template(call_type.pre_call_body_template, meeting).encode("utf-8")
        except Exception:
            logger.exception("Pre-call for meeting %s: failed to render body template", meeting.id)
            return None

    try:
        async with httpx.AsyncClient(timeout=settings.pre_call_timeout_seconds) as client:
            response = await client.request(
                call_type.pre_call_method or "GET", call_type.pre_call_url, content=body, headers=headers
            )
        if response.status_code >= 400:
            logger.warning(
                "Pre-call for meeting %s returned %s: %s",
                meeting.id,
                response.status_code,
                response.text[:500],
            )
            return None
        logger.info("Pre-call for meeting %s dispatched successfully (%s)", meeting.id, response.status_code)
        return response.text[: settings.pre_call_context_max_chars]
    except httpx.RequestError:
        logger.exception("Pre-call for meeting %s: request failed", meeting.id)
        return None


async def dispatch_post_call(db: AsyncSession, meeting: Meeting, report: ReportResult) -> None:
    """Fires the admin-configured post-call for this meeting's call type,
    if one is configured — a no-op (not an error) when there's no type,
    it has no post-call enabled, or no URL. Called once, right after a
    *successful* automatic report generation (app/workers/tasks.py);
    never from the manual "Regenerate report" route — regenerating isn't
    "a conversation ending" a second time.

    Any failure here — a bad URL, a malformed body template, a connection
    error, a non-2xx response — is logged and swallowed, never raised: a
    broken hook must never affect the meeting/report's own success, same
    graceful-degradation discipline as every other best-effort side path
    in this codebase (Deepgram fallback, diarization skip,
    index_meeting_search dispatch).
    """
    call_type = meeting.call_type
    if call_type is None or not call_type.post_call_enabled or not call_type.post_call_url:
        return

    settings = get_settings()
    try:
        if call_type.post_call_send_full_payload:
            body = json.dumps(await build_full_payload(db, meeting, report))
        else:
            body = await render_template(db, call_type.post_call_body_template or "{}", meeting, report)
    except Exception:
        logger.exception("Post-call for meeting %s: failed to build body", meeting.id)
        return

    headers = {"Content-Type": "application/json"}
    if call_type.post_call_headers_encrypted:
        try:
            headers.update(json.loads(decrypt_secret(call_type.post_call_headers_encrypted)))
        except Exception:
            logger.exception("Post-call for meeting %s: failed to decrypt/parse headers", meeting.id)
            return
    headers.update(_mandatory_headers(meeting.id, meeting.owner_id))

    try:
        async with httpx.AsyncClient(timeout=settings.post_call_timeout_seconds) as client:
            response = await client.request(
                call_type.post_call_method or "POST",
                call_type.post_call_url,
                content=body.encode("utf-8"),
                headers=headers,
            )
        if response.status_code >= 400:
            logger.warning(
                "Post-call for meeting %s returned %s: %s",
                meeting.id,
                response.status_code,
                response.text[:500],
            )
        else:
            logger.info(
                "Post-call for meeting %s dispatched successfully (%s)", meeting.id, response.status_code
            )
    except httpx.RequestError:
        logger.exception("Post-call for meeting %s: request failed", meeting.id)
