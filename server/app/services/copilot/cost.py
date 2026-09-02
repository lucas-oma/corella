from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost import LLMUsageEvent, UsageKind
from app.models.meeting import Meeting
from app.models.provider_credential import LLMProvider


async def add_meeting_cost(
    db: AsyncSession,
    meeting_id: UUID,
    owner_id: UUID,
    provider: LLMProvider,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float | None,
    kind: UsageKind,
) -> None:
    """Records one LLM call in two places: the atomic running total on the
    meeting itself (Meeting.estimated_cost_usd, backing the per-meeting
    report badge — unchanged from Phase L, a single UPDATE not a
    read-modify-write, since live cycles and report generation are
    independent call paths with no shared in-memory state to coordinate
    through), and a full LLMUsageEvent row (app/models/cost.py) — the
    append-only ledger admin cost analytics (app/services/admin/costs.py)
    is built on, which the running total alone can't support (no per-event
    timestamps for daily/historic figures).

    cost_usd may be None (unpriced/unknown model) — the meeting's running
    total is only bumped when it's a real number, but the ledger row is
    still written either way, so "N calls made, M had a known cost" stays
    honest in the analytics rather than silently dropping the unpriced ones.
    """
    if cost_usd is not None:
        await db.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id)
            .values(estimated_cost_usd=func.coalesce(Meeting.estimated_cost_usd, 0) + cost_usd)
        )
    db.add(
        LLMUsageEvent(
            meeting_id=meeting_id,
            owner_id=owner_id,
            provider=provider,
            model=model,
            kind=kind,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
    )
