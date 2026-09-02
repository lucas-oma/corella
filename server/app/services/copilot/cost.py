from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import Meeting


async def add_meeting_cost(db: AsyncSession, meeting_id: UUID, delta_usd: float) -> None:
    """Accumulates onto Meeting.estimated_cost_usd — the one report field
    that's cumulative rather than overwritten wholesale on regenerate, since
    every LLM call for a meeting (each live copilot cycle, each report
    generation/regeneration) adds its own real cost to the same running
    total. Done as a single atomic UPDATE, not a read-modify-write, since
    live cycles and report generation are two independent call paths with
    no shared in-memory state to coordinate through.
    """
    await db.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id)
        .values(estimated_cost_usd=func.coalesce(Meeting.estimated_cost_usd, 0) + delta_usd)
    )
