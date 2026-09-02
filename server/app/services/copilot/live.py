import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.meeting import ActionItem, ActionItemStatus, Channel, TranscriptSegment
from app.services.copilot.action_items import persist_new_action_items
from app.services.copilot.cost import add_meeting_cost
from app.services.copilot.json_parse import as_str_list, parse_json_response
from app.services.access import searchable_owner_ids
from app.services.copilot.talk_ratio import talk_ratio
from app.services.embeddings.qdrant_store import search_kb
from app.services.embeddings.query import embed_query
from app.services.llm.base import LLMError, LLMMessage, complete
from app.services.llm.pricing import estimate_cost_usd
from app.services.llm.resolve import ResolvedProvider

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a live call copilot, silently observing a conversation and helping "Me" (the user) in real time. Given the recent transcript and optional reference material, respond with ONLY a single JSON object, no other text, in exactly this shape:

{
  "suggestion": "<one short, concrete talking point or answer Me could use next, grounded in the reference material if relevant, or null if there's nothing useful to add right now>",
  "blockers": ["<an unresolved question or objection from Them that hasn't been addressed yet>"],
  "action_items": ["<a new commitment or follow-up task mentioned in this exchange>"],
  "coach_score": <integer 0-100 rating how well this call is going for Me, considering engagement and whether Them's questions are being answered>
}

Use empty arrays / null when there's nothing to report in a field. Keep the suggestion under 2 sentences."""


@dataclass
class CopilotResult:
    suggestion: str | None
    blockers: list[str]
    action_items: list[str]  # all currently-open items for the meeting, not just new ones this cycle
    coach_score: int | None


async def run_cycle(
    db: AsyncSession, meeting_id: UUID, owner_id: UUID, provider: ResolvedProvider
) -> CopilotResult | None:
    """One copilot cycle: recent transcript + optional KB context -> one LLM
    call -> parsed suggestion/blockers/action-items/score. Returns None on
    any failure (LLM error, parse error, no transcript yet) — a skipped
    cycle, not a crash of the live session.
    """
    settings = get_settings()

    all_segments = list(
        await db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == meeting_id)
            .order_by(TranscriptSegment.start_ms)
        )
    )
    if not all_segments:
        return None

    context_segments = all_segments[-settings.copilot_context_window_segments :]
    transcript_text = _format_transcript(context_segments)
    ratio = talk_ratio(all_segments)  # whole call, not just the recent window — an honest metric

    kb_context = await _retrieve_kb_context(db, owner_id, transcript_text)

    user_content = (
        f"Recent transcript:\n{transcript_text}\n\n"
        f"Talk ratio so far — Me: {ratio['me']}%, Them: {ratio['them']}%"
    )
    if kb_context:
        user_content += "\n\nReference material:\n" + "\n---\n".join(kb_context)

    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_content),
    ]

    try:
        response = await complete(
            provider.provider,
            provider.model,
            messages,
            provider.api_key,
            provider.base_url,
            max_tokens=220,
        )
    except LLMError as e:
        logger.info("Copilot cycle skipped for meeting %s: %s", meeting_id, e)
        return None

    # The call itself cost money regardless of whether the JSON below parses
    # cleanly, so track it before parsing can fail.
    cost = estimate_cost_usd(
        provider.provider, provider.model, response.input_tokens, response.output_tokens
    )
    if cost is not None:
        await add_meeting_cost(db, meeting_id, cost)
        await db.commit()

    try:
        parsed = parse_json_response(response.text)
    except ValueError as e:
        logger.info("Copilot cycle skipped for meeting %s: %s", meeting_id, e)
        return None

    new_action_items = as_str_list(parsed.get("action_items"))
    if new_action_items:
        await persist_new_action_items(db, meeting_id, new_action_items)
        await db.commit()

    open_items = list(
        await db.scalars(
            select(ActionItem.text).where(
                ActionItem.meeting_id == meeting_id, ActionItem.status == ActionItemStatus.OPEN
            )
        )
    )

    coach_score = parsed.get("coach_score")
    return CopilotResult(
        suggestion=(parsed.get("suggestion") or None),
        blockers=as_str_list(parsed.get("blockers")),
        action_items=open_items,
        coach_score=int(coach_score) if isinstance(coach_score, (int, float)) else None,
    )


def _format_transcript(segments: list[TranscriptSegment]) -> str:
    labels = {Channel.ME: "Me", Channel.THEM: "Them"}
    return "\n".join(f"{labels.get(s.channel, 'Unknown')}: {s.text}" for s in segments)


async def _retrieve_kb_context(db: AsyncSession, owner_id: UUID, query_text: str) -> list[str]:
    # Group-aware: a grouped user's copilot can draw on *any* group
    # member's uploaded documents, not just their own (app/services/access.py)
    # — so the "does this searcher have any KB at all" check has to look
    # across the same searchable set, or a grouped user with no docs of
    # their own would short-circuit here and never see a groupmate's.
    owner_ids = await searchable_owner_ids(db, owner_id)
    has_kb = await db.scalar(
        select(KBDocument.id)
        .where(KBDocument.owner_id.in_(owner_ids), KBDocument.status == KBDocumentStatus.READY)
        .limit(1)
    )
    if has_kb is None:
        return []

    settings = get_settings()
    try:
        embedding = await embed_query(query_text)
        return search_kb(owner_ids, embedding, top_k=settings.copilot_kb_top_k)
    except Exception:
        logger.exception("KB retrieval failed for owner %s", owner_id)
        return []
