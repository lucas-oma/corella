import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.cost import UsageKind
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.meeting import (
    ActionItem,
    ActionItemSource,
    ActionItemStatus,
    CaptureMode,
    CopilotInsight,
    Meeting,
    TranscriptSegment,
)
from app.models.user import User
from app.services.access import kb_visible_clause, searchable_owner_ids
from app.services.copilot.action_items import persist_new_action_items
from app.services.copilot.cost import add_meeting_cost
from app.services.copilot.json_parse import as_str_list, parse_json_response
from app.services.copilot.talk_ratio import talk_ratio
from app.services.embeddings.qdrant_store import search_kb
from app.services.embeddings.query import embed_query
from app.services.llm.base import LLMError, LLMMessage, complete
from app.services.llm.pricing import estimate_cost_usd
from app.services.llm.resolve import ResolvedProvider
from app.services.transcript_format import format_transcript

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
    action_items: list[str]  # currently-open live captures, not the report digest
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
    ratio = talk_ratio(all_segments)  # whole call, not just the recent window — an honest metric

    capture_row = (
        await db.execute(select(Meeting.pre_call_context, Meeting.capture_mode).where(Meeting.id == meeting_id))
    ).first()
    pre_call_context = capture_row[0] if capture_row else None
    capture_mode = capture_row[1] if capture_row else CaptureMode.OPEN_MIC
    transcript_text = format_transcript(
        context_segments, owner_id=owner_id, capture_mode=capture_mode
    )

    kb_context = await _retrieve_kb_context(db, owner_id, meeting_id, transcript_text)

    user_content = f"Recent transcript:\n{transcript_text}"
    if capture_mode == CaptureMode.MEETING_TAB and ratio["them"] > 0:
        user_content += f"\n\nTalk ratio so far — Me: {ratio['me']}%, Them: {ratio['them']}%"
    if pre_call_context:
        # Alongside, not instead of, the knowledge base below — an
        # external system's own pre-fetched data (e.g. a CRM lookup) is a
        # different kind of context than the group's uploaded documents.
        user_content += "\n\nExternal context:\n" + pre_call_context
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
    # cleanly, so track it before parsing can fail. Logged even when cost is
    # None (unpriced model) — add_meeting_cost still records the ledger row,
    # just skips the meeting's running-total bump for that case.
    cost = estimate_cost_usd(
        provider.provider, provider.model, response.input_tokens, response.output_tokens
    )
    await add_meeting_cost(
        db,
        meeting_id,
        owner_id,
        provider.provider,
        provider.model,
        response.input_tokens,
        response.output_tokens,
        cost,
        UsageKind.LIVE_CYCLE,
    )
    await db.commit()

    try:
        parsed = parse_json_response(response.text)
    except ValueError as e:
        logger.info("Copilot cycle skipped for meeting %s: %s", meeting_id, e)
        return None

    suggestion = parsed.get("suggestion") or None
    blockers = as_str_list(parsed.get("blockers"))
    raw_coach_score = parsed.get("coach_score")
    coach_score = int(raw_coach_score) if isinstance(raw_coach_score, int | float) else None

    new_action_items = as_str_list(parsed.get("action_items"))
    if new_action_items:
        await persist_new_action_items(db, meeting_id, new_action_items)

    # Every successfully-parsed cycle gets a row, even one with no
    # suggestion/blockers — coach_score is required by _SYSTEM_PROMPT on
    # every response (unlike suggestion), so gating this on
    # suggestion/blockers being non-empty would leave a score-over-time
    # view sparse for no reason. Anchored to the transcript's own clock
    # (the most recent segment this cycle actually saw), not session-
    # elapsed wall time, so it lines up with TranscriptSegment.start_ms/
    # end_ms for display (MeetingDetail's insights column).
    db.add(
        CopilotInsight(
            meeting_id=meeting_id,
            at_ms=all_segments[-1].end_ms,
            suggestion=suggestion,
            blockers=blockers,
            coach_score=coach_score,
        )
    )
    await db.commit()

    open_items = list(
        await db.scalars(
            select(ActionItem.text).where(
                ActionItem.meeting_id == meeting_id,
                ActionItem.source == ActionItemSource.LIVE,
                ActionItem.status == ActionItemStatus.OPEN,
            )
        )
    )

    return CopilotResult(
        suggestion=suggestion,
        blockers=blockers,
        action_items=open_items,
        coach_score=coach_score,
    )


async def _retrieve_kb_context(
    db: AsyncSession, owner_id: UUID, meeting_id: UUID, query_text: str
) -> list[str]:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        return []
    organization_id = meeting.organization_id
    user = await db.get(User, owner_id)
    if user is None:
        return []
    from app.models.organization import OrganizationMembership, OrgRole
    from app.services.organizations import group_ids_for_user

    group_ids = await group_ids_for_user(db, owner_id, organization_id)
    membership = await db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == owner_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )
    org_admin = membership is not None and membership.role in (OrgRole.OWNER, OrgRole.ADMIN)
    has_kb = await db.scalar(
        select(KBDocument.id)
        .where(
            kb_visible_clause(
                user, organization_id, group_ids=group_ids, org_admin=org_admin
            ),
            KBDocument.status == KBDocumentStatus.READY,
        )
        .limit(1)
    )
    if has_kb is None:
        return []

    owner_ids = await searchable_owner_ids(db, owner_id, organization_id)
    settings = get_settings()
    try:
        embedding = await embed_query(query_text)
        return search_kb(
            owner_ids,
            embedding,
            top_k=settings.copilot_kb_top_k,
            group_ids=group_ids,
            organization_id=organization_id,
        )
    except Exception:
        logger.exception("KB retrieval failed for owner %s", owner_id)
        return []
