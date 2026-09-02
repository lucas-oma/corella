from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import ActionItem, ActionItemStatus, CallType, Channel, Meeting, TranscriptSegment
from app.services.copilot.action_items import persist_new_action_items
from app.services.copilot.cost import add_meeting_cost
from app.services.copilot.json_parse import as_str_list, parse_json_response
from app.services.copilot.talk_ratio import talk_ratio
from app.services.llm.base import LLMError, LLMMessage, complete
from app.services.llm.pricing import estimate_cost_usd
from app.services.llm.resolve import ResolvedProvider

_SYSTEM_PROMPT = """You are summarizing a completed call transcript. Respond with ONLY a single JSON object, no other text, in exactly this shape:

{
  "title": "<a short, specific title for this call, 3-8 words>",
  "summary": "<a few sentences summarizing what was discussed and any conclusions reached>",
  "key_topics": ["<a distinct topic or theme discussed, 2-5 items>"],
  "sentiment": "<one or two words describing the overall tone, e.g. Positive, Neutral, Tense, Mixed>",
  "notable_quotes": ["<a directly-quoted, noteworthy line from the transcript, verbatim, 0-4 items>"],
  "coach_score": <integer 0-100 rating how well this call went for Me overall, considering engagement and whether Them's questions or concerns were addressed>,
  "action_items": ["<a commitment or follow-up task mentioned anywhere in the call>"]
}"""

# Appended to _SYSTEM_PROMPT based on Meeting.call_type — same JSON shape for
# every type, just different guidance on what to emphasize within it.
_CALL_TYPE_GUIDANCE: dict[CallType, str] = {
    CallType.SALES: (
        "This is a sales call. Focus the summary and key_topics on the prospect's pain points, "
        "objections raised, budget/timeline signals, and next steps or deal stage. sentiment should "
        "reflect how receptive the prospect seemed. Prioritize quotes about pricing, timeline, or "
        "objections for notable_quotes."
    ),
    CallType.SUPPORT: (
        "This is a customer support call. Focus the summary and key_topics on the issue reported, "
        "whether it was resolved, and any escalation risk. sentiment should reflect the customer's "
        "frustration or satisfaction level. Prioritize quotes describing the problem or the "
        "resolution for notable_quotes."
    ),
    CallType.INTERVIEW: (
        "This is a job interview. Focus the summary and key_topics on the candidate's strengths, "
        "gaps, and fit signals relative to what was asked. sentiment should reflect how the "
        "conversation went overall. Prioritize quotes that reveal candidate strengths or concerns "
        "for notable_quotes."
    ),
    CallType.ONE_ON_ONE: (
        "This is a one-on-one check-in. Focus the summary and key_topics on blockers raised, growth "
        "or career topics, and commitments made by either person. sentiment should reflect the "
        "overall tone of the conversation. Prioritize quotes about blockers or commitments for "
        "notable_quotes."
    ),
    CallType.MEETING: (
        "This is a general meeting. Focus the summary and key_topics on decisions made and open "
        "questions left unresolved."
    ),
}

_LABELS = {Channel.ME: "Me", Channel.THEM: "Them"}


class ReportError(Exception):
    """Raised when a report can't be generated — no transcript yet, or the
    LLM call/parse failed. Callers surface .args[0] as the error detail."""


@dataclass
class ReportResult:
    title: str
    summary: str
    key_topics: list[str]
    sentiment: str | None
    notable_quotes: list[str]
    coach_score: int | None
    estimated_cost_usd: float | None
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

    system_prompt = _SYSTEM_PROMPT
    guidance = _CALL_TYPE_GUIDANCE.get(meeting.call_type)
    if guidance:
        system_prompt += f"\n\n{guidance}"

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_content),
    ]

    try:
        response = await complete(
            provider.provider,
            provider.model,
            messages,
            provider.api_key,
            provider.base_url,
            max_tokens=2048,
        )
    except LLMError as e:
        raise ReportError(f"Report generation failed: {e}") from e

    # The call itself cost money regardless of whether the JSON below parses
    # cleanly, so track and commit it before parsing can fail.
    cost = estimate_cost_usd(
        provider.provider, provider.model, response.input_tokens, response.output_tokens
    )
    if cost is not None:
        await add_meeting_cost(db, meeting.id, cost)
        await db.commit()

    try:
        parsed = parse_json_response(response.text)
    except ValueError as e:
        raise ReportError(f"Report generation failed: {e}") from e

    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        raise ReportError("The model returned an empty summary")

    title = str(parsed.get("title") or "").strip() or meeting.title
    key_topics = as_str_list(parsed.get("key_topics"))
    sentiment = str(parsed.get("sentiment") or "").strip() or None
    notable_quotes = as_str_list(parsed.get("notable_quotes"))
    raw_score = parsed.get("coach_score")
    coach_score = int(raw_score) if isinstance(raw_score, (int, float)) else None

    new_items = as_str_list(parsed.get("action_items"))
    if new_items:
        await persist_new_action_items(db, meeting.id, new_items)

    meeting.title = title
    meeting.summary = summary
    meeting.key_topics = key_topics
    meeting.sentiment = sentiment
    meeting.notable_quotes = notable_quotes
    meeting.coach_score = coach_score
    await db.commit()
    # add_meeting_cost above updated estimated_cost_usd via a raw UPDATE,
    # which bypasses this in-memory object — refresh to pick up the true
    # post-increment total for the response.
    await db.refresh(meeting, ["estimated_cost_usd"])

    open_items = list(
        await db.scalars(
            select(ActionItem).where(
                ActionItem.meeting_id == meeting.id, ActionItem.status == ActionItemStatus.OPEN
            )
        )
    )

    return ReportResult(
        title=title,
        summary=summary,
        key_topics=key_topics,
        sentiment=sentiment,
        notable_quotes=notable_quotes,
        coach_score=coach_score,
        estimated_cost_usd=meeting.estimated_cost_usd,
        action_items=open_items,
        talk_ratio=ratio if has_channel_data else None,
    )
