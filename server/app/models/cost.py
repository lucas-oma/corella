import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import UUIDPrimaryKeyMixin


class UsageKind(str, enum.Enum):
    """Which call path an LLMUsageEvent came from — the live copilot's
    recurring per-cycle nudge, a one-shot post-call report generation, or
    (Phase W5) Deepgram STT usage from either the upload or live path.
    """

    LIVE_CYCLE = "live_cycle"
    REPORT = "report"
    STT_UPLOAD = "stt_upload"
    STT_LIVE = "stt_live"
    # A one-shot LLM call at KB document upload time, not tied to any
    # meeting (app/services/embeddings/kb_keywords.py) — the one UsageKind
    # whose LLMUsageEvent row always has meeting_id=None.
    KB_EXTRACTION = "kb_extraction"


class LLMUsageEvent(UUIDPrimaryKeyMixin, Base):
    """One row per billable external call, for admin cost analytics
    (app/services/admin/costs.py) — an append-only ledger underneath
    Meeting.estimated_cost_usd's running total (app/services/copilot/cost.py),
    which only that one cumulative float can't support (no per-event
    timestamps to build daily/historic figures from).

    Despite the name (kept as-is rather than churning every reference to
    it for a cosmetic rename), this table isn't LLM-only as of Phase W5:
    `provider`/`model` are deliberately a plain string, not a pg_enum bound
    to LLMProvider — Deepgram STT usage lands here too now, sharing the
    exact same ledger/atomic-total machinery, priced by `audio_seconds`
    (duration-billed) instead of `input_tokens`/`output_tokens`
    (token-billed) when that's the relevant unit for the provider.

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
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(255))
    kind: Mapped[UsageKind] = mapped_column(pg_enum(UsageKind, "usage_event_kind"))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Duration-billed usage (Deepgram STT) — None for every token-billed
    # (LLM) row, mirroring how input_tokens/output_tokens stay None for a
    # duration-billed one. Whichever pair is relevant to `provider` is set;
    # the other stays null, never a fabricated 0.
    audio_seconds: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
