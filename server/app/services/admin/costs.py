from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost import LLMUsageEvent
from app.models.user import User

# How much daily history to pull for the "historic costs" chart — also the
# pool the next-7-days projection draws its trailing average from.
_DAILY_HISTORY_DAYS = 30
_PROJECTION_DAYS = 7


@dataclass
class UserCostBreakdown:
    owner_id: UUID | None  # None -> a deleted account, still counted (see LLMUsageEvent's SET NULL)
    owner_name: str
    total_usd: float
    call_count: int


@dataclass
class DailyCost:
    day: date
    total_usd: float


@dataclass
class CostSummary:
    total_usd: float
    priced_call_count: int  # calls that contributed a real number to total_usd
    total_call_count: int  # every logged call, priced or not
    avg_cost_per_call: float | None  # None if no call has ever been priced
    total_input_tokens: int
    total_output_tokens: int
    by_user: list[UserCostBreakdown]
    daily: list[DailyCost]  # oldest first, last _DAILY_HISTORY_DAYS days
    projected_next_7_days_usd: float | None  # trailing-average(daily[-7:]) * 7


async def get_cost_summary(db: AsyncSession) -> CostSummary:
    """One aggregate read for the whole Admin Costs section — a handful of
    grouped SQL aggregations against the LLMUsageEvent ledger, not
    application-level looping over rows.
    """
    total_usd, priced_call_count, total_call_count, total_input_tokens, total_output_tokens = (
        await db.execute(
            select(
                func.coalesce(func.sum(LLMUsageEvent.cost_usd), 0.0),
                func.count(LLMUsageEvent.cost_usd),  # COUNT(col) skips NULLs
                func.count(LLMUsageEvent.id),
                func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0),
                func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0),
            )
        )
    ).one()
    avg_cost_per_call = (total_usd / priced_call_count) if priced_call_count else None

    by_user_totals = func.coalesce(func.sum(LLMUsageEvent.cost_usd), 0.0)
    by_user_rows = (
        await db.execute(
            select(
                LLMUsageEvent.owner_id,
                User.full_name,
                by_user_totals,
                func.count(LLMUsageEvent.id),
            )
            .outerjoin(User, User.id == LLMUsageEvent.owner_id)
            .group_by(LLMUsageEvent.owner_id, User.full_name)
            .order_by(by_user_totals.desc())
        )
    ).all()
    by_user = [
        UserCostBreakdown(
            owner_id=owner_id,
            owner_name=full_name or "Deleted user",
            total_usd=total,
            call_count=count,
        )
        for owner_id, full_name, total, count in by_user_rows
    ]

    since = datetime.now(UTC) - timedelta(days=_DAILY_HISTORY_DAYS)
    day_col = func.date(LLMUsageEvent.created_at)
    daily_totals = func.coalesce(func.sum(LLMUsageEvent.cost_usd), 0.0)
    daily_rows = (
        await db.execute(
            select(day_col, daily_totals)
            .where(LLMUsageEvent.created_at >= since)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()
    daily = [DailyCost(day=d, total_usd=total) for d, total in daily_rows]

    if daily:
        recent = daily[-_PROJECTION_DAYS:]
        avg_daily = sum(d.total_usd for d in recent) / len(recent)
        projected_next_7_days_usd = avg_daily * _PROJECTION_DAYS
    else:
        projected_next_7_days_usd = None

    return CostSummary(
        total_usd=total_usd,
        priced_call_count=priced_call_count,
        total_call_count=total_call_count,
        avg_cost_per_call=avg_cost_per_call,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        by_user=by_user,
        daily=daily,
        projected_next_7_days_usd=projected_next_7_days_usd,
    )
