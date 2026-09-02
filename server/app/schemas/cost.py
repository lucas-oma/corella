from datetime import date
from uuid import UUID

from pydantic import BaseModel


class UserCostBreakdownRead(BaseModel):
    owner_id: UUID | None
    owner_name: str
    total_usd: float
    call_count: int


class DailyCostRead(BaseModel):
    day: date
    total_usd: float


class CostSummaryRead(BaseModel):
    total_usd: float
    priced_call_count: int
    total_call_count: int
    avg_cost_per_call: float | None
    total_input_tokens: int
    total_output_tokens: int
    by_user: list[UserCostBreakdownRead]
    daily: list[DailyCostRead]
    projected_next_7_days_usd: float | None
