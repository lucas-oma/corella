import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
from app.models.meeting import Channel, Meeting, TranscriptSegment
from app.services.copilot.report import ReportResult

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30.0
_LABELS = {Channel.ME: "Me", Channel.THEM: "Them"}


def _json_value(value) -> str:
    """json.dumps(value), with the outer quotes stripped for a plain
    string — so a {{placeholder}} sitting inside "..." in the admin's own
    template substitutes a properly escaped value (quotes/newlines in a
    summary can't break the surrounding JSON) without doubling up quotes.
    Non-string values (arrays, numbers, null) are inserted as-is, valid
    JSON on their own — the admin's template should place those
    placeholders *outside* a quoted string.
    """
    dumped = json.dumps(value)
    if isinstance(value, str):
        return dumped[1:-1]
    return dumped


async def _build_transcript_text(db: AsyncSession, meeting_id) -> str:
    segments = list(
        await db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == meeting_id)
            .order_by(TranscriptSegment.start_ms)
        )
    )
    return "\n".join(f"{_LABELS.get(s.channel, 'Speaker')}: {s.text}" for s in segments)


async def render_template(db: AsyncSession, template: str, meeting: Meeting, report: ReportResult) -> str:
    """Substitutes {{placeholder}} tokens in an admin-authored webhook body
    template with real meeting/report data. Supported placeholders:
    meeting_id, owner_id, owner_name, title, call_type, status, summary,
    key_topics, sentiment, coach_score, action_items, transcript,
    created_at, duration_seconds.
    """
    values = {
        "meeting_id": str(meeting.id),
        "owner_id": str(meeting.owner_id),
        "owner_name": meeting.owner_name,
        "title": report.title,
        "call_type": meeting.call_type.name if meeting.call_type else None,
        "status": meeting.status.value,
        "summary": report.summary,
        "key_topics": report.key_topics,
        "sentiment": report.sentiment,
        "coach_score": report.coach_score,
        "action_items": [item.text for item in report.action_items],
        "transcript": await _build_transcript_text(db, meeting.id),
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
        "duration_seconds": meeting.duration_seconds,
    }

    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", _json_value(value))
    return rendered


async def dispatch_call_type_webhook(db: AsyncSession, meeting: Meeting, report: ReportResult) -> None:
    """Fires the admin-configured post-call webhook for this meeting's call
    type, if one is configured — a no-op (not an error) when there's no
    type, it has no webhook enabled, or no URL. Called once, right after a
    *successful* automatic report generation (app/workers/tasks.py); never
    from the manual "Regenerate report" route — regenerating isn't "a
    conversation ending" a second time.

    Any failure here — a bad URL, a malformed body template, a connection
    error, a non-2xx response — is logged and swallowed, never raised: a
    broken webhook must never affect the meeting/report's own success,
    same graceful-degradation discipline as every other best-effort side
    path in this codebase (Deepgram fallback, diarization skip,
    index_meeting_search dispatch).
    """
    call_type = meeting.call_type
    if call_type is None or not call_type.webhook_enabled or not call_type.webhook_url:
        return

    try:
        body = await render_template(db, call_type.webhook_body_template or "{}", meeting, report)
    except Exception:
        logger.exception("Webhook for meeting %s: failed to render body template", meeting.id)
        return

    headers = {"Content-Type": "application/json"}
    if call_type.webhook_headers_encrypted:
        try:
            headers.update(json.loads(decrypt_secret(call_type.webhook_headers_encrypted)))
        except Exception:
            logger.exception("Webhook for meeting %s: failed to decrypt/parse headers", meeting.id)
            return

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.request(
                call_type.webhook_method or "POST",
                call_type.webhook_url,
                content=body.encode("utf-8"),
                headers=headers,
            )
        if response.status_code >= 400:
            logger.warning(
                "Webhook for meeting %s returned %s: %s",
                meeting.id,
                response.status_code,
                response.text[:500],
            )
        else:
            logger.info("Webhook for meeting %s dispatched successfully (%s)", meeting.id, response.status_code)
    except httpx.RequestError:
        logger.exception("Webhook for meeting %s: request failed", meeting.id)
