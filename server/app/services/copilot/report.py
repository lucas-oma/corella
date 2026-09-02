from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import ActionItem, ActionItemStatus, Channel, Meeting, TranscriptSegment
from app.services.copilot.action_items import persist_new_action_items
from app.services.copilot.json_parse import as_str_list, parse_json_response
from app.services.copilot.talk_ratio import talk_ratio
from app.services.llm.base import LLMError, LLMMessage, complete
from app.services.llm.resolve import ResolvedProvider

_SYSTEM_PROMPT = """You are summarizing a completed call transcript. Respond with ONLY a single JSON object, no other text, in exactly this shape:

{
  "summary": "<a few sentences summarizing what was discussed and any conclusions reached>",
  "action_items": ["<a commitment or follow-up task mentioned anywhere in the call>"]
}"""

_LABELS = {Channel.ME: "Me", Channel.THEM: "Them"}


class ReportError(Exception):
    """Raised when a report can't be generated — no transcript yet, or the
    LLM call/parse failed. Callers surface .args[0] as the error detail."""


@dataclass
class ReportResult:
    summary: str
    action_items: list[ActionItem]  # all open items for the meeting, after persisting new ones
    talk_ratio: dict[str, int] | None  # None if this meeting has no Me/Them channel data


async def generate_report(db: AsyncSession, meeting: Meeting, provider: ResolvedProvider) -> ReportResult:
    segments = list(
        await db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == meeting.id)
            .order_by(TranscriptSegment.start_ms)
        )
    )
    if not segments:
        raise ReportError("This meeting has no transcript yet")

    transcript_text = "\n".join(f"{_LABELS.get(s.channel, 'Speaker')}: {s.text}" for s in segments)
    ratio = talk_ratio(segments)
    has_channel_data = ratio["me"] > 0 or ratio["them"] > 0

    user_content = f"Full transcript:\n{transcript_text}"
    if has_channel_data:
        user_content += f"\n\nTalk ratio — Me: {ratio['me']}%, Them: {ratio['them']}%"

    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_content),
    ]

    try:
        raw = await complete(
            provider.provider,
            provider.model,
            messages,
            provider.api_key,
            provider.base_url,
            max_tokens=2048,
        )
        parsed = parse_json_response(raw)
    except (LLMError, ValueError) as e:
        raise ReportError(f"Report generation failed: {e}") from e

    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        raise ReportError("The model returned an empty summary")

    new_items = as_str_list(parsed.get("action_items"))
    if new_items:
        await persist_new_action_items(db, meeting.id, new_items)

    meeting.summary = summary
    await db.commit()

    open_items = list(
        await db.scalars(
            select(ActionItem).where(
                ActionItem.meeting_id == meeting.id, ActionItem.status == ActionItemStatus.OPEN
            )
        )
    )

    return ReportResult(
        summary=summary, action_items=open_items, talk_ratio=ratio if has_channel_data else None
    )
