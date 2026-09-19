import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class HookLog(UUIDPrimaryKeyMixin, Base):
    """One outbound pre/post hook attempt for a meeting. Written on every
    dispatch that actually tried to call an API (success or error) —
    including header-resolve / template / HTTP failures. Never returned
    on MeetingRead; admins fetch these from GET /api/meetings/{id}/hook-logs.
    Request/response bodies are redacted before persist
    (app/services/admin/call_hooks.py).
    """

    __tablename__ = "hook_logs"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    # "pre" | "post"
    phase: Mapped[str] = mapped_column(String(8))
    # "success" | "error"
    outcome: Mapped[str] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(2048))
    request_headers: Mapped[str | None] = mapped_column(Text)
    request_body: Mapped[str | None] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    ran_async: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
