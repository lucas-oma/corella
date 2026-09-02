import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import UUIDPrimaryKeyMixin
from app.models.provider_credential import LLMProvider


class UsageKind(str, enum.Enum):
    """Which call path an LLMUsageEvent came from — the live copilot's
    recurring per-cycle nudge, or a one-shot post-call report generation.
    """

    LIVE_CYCLE = "live_cycle"
    REPORT = "report"


class LLMUsageEvent(UUIDPrimaryKeyMixin, Base):
    """One row per LLM completion call, for admin cost analytics
    (app/services/admin/costs.py) — an append-only ledger underneath
    Meeting.estimated_cost_usd's running total (app/services/copilot/cost.py),
    which only that one cumulative float can't support (no per-event
    timestamps to build daily/historic figures from).

    meeting_id/owner_id are SET NULL, not CASCADE, on purpose: deleting a
    meeting or account shouldn't erase money already spent from the
    historical/financial record — the ledger row just loses its reference.
    """

    __tablename__ = "llm_usage_events"

    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="SET NULL"), index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[LLMProvider] = mapped_column(pg_enum(LLMProvider, "usage_event_provider"))
    model: Mapped[str] = mapped_column(String(255))
    kind: Mapped[UsageKind] = mapped_column(pg_enum(UsageKind, "usage_event_kind"))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
