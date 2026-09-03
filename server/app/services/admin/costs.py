from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost import LLMUsageEvent
from app.models.user import User

CostPeriod = Literal["7d", "30d", "month", "year"]

# Trailing window the next-7-days projection draws its average from —
# independent of the chart period so toggling the chart doesn't change
# the projection card.
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
    daily: list[DailyCost]  # oldest first; every calendar day in the period, zeros filled
    projected_next_7_days_usd: float | None  # trailing-average(last 7 calendar days) * 7
    period: CostPeriod


def period_window(period: CostPeriod, today: date | None = None) -> tuple[date, date]:
    """Inclusive [start, end] calendar-day window for a chart period."""
    end = today or datetime.now(UTC).date()
    if period == "7d":
        return end - timedelta(days=6), end
    if period == "30d":
        return end - timedelta(days=29), end
    if period == "month":
        return end.replace(day=1), end
    if period == "year":
        return end - timedelta(days=364), end
    raise ValueError(f"unknown cost period: {period}")


def _dense_daily(start: date, end: date, totals_by_day: dict[date, float]) -> list[DailyCost]:
    """One DailyCost per calendar day in [start, end], zero-filled."""
    out: list[DailyCost] = []
    day = start
    while day <= end:
        out.append(DailyCost(day=day, total_usd=float(totals_by_day.get(day, 0.0))))
        day += timedelta(days=1)
    return out


async def get_cost_summary(
    db: AsyncSession,
    period: CostPeriod = "30d",
) -> CostSummary:
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

    start, end = period_window(period)
    since = datetime(start.year, start.month, start.day, tzinfo=UTC)
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
    totals_by_day: dict[date, float] = {}
    for d, total in daily_rows:
        # Postgres may return date or datetime depending on dialect/version.
        key = d.date() if isinstance(d, datetime) else d
        totals_by_day[key] = float(total)
    daily = _dense_daily(start, end, totals_by_day)

    # Projection always uses the last 7 calendar days, independent of the
    # chart period (so early-month views don't under-weight the estimate).
    proj_start = end - timedelta(days=_PROJECTION_DAYS - 1)
    if proj_start >= start:
        recent = [d for d in daily if d.day >= proj_start]
    else:
        proj_rows = (
            await db.execute(
                select(day_col, daily_totals)
                .where(
                    LLMUsageEvent.created_at
                    >= datetime(proj_start.year, proj_start.month, proj_start.day, tzinfo=UTC)
                )
                .group_by(day_col)
                .order_by(day_col)
            )
        ).all()
        proj_totals = {
            (d.date() if isinstance(d, datetime) else d): float(total) for d, total in proj_rows
        }
        recent = _dense_daily(proj_start, end, proj_totals)

    if any(d.total_usd > 0 for d in recent):
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
        period=period,
    )
