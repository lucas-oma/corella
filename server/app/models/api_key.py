import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

# Per-key cap on a live WebSocket session authenticated with this key
# (app/ws/live_session.py). Browser/JWT recordings are uncapped — a person
# sitting on the live page is a different risk than a daemon that never
# sends stop. 60 minutes is the default (and what existing keys get via
# the migration); 1..480 (8h) is the allowed range, no "unlimited".
DEFAULT_MAX_DURATION_MINUTES = 60
MIN_MAX_DURATION_MINUTES = 1
MAX_MAX_DURATION_MINUTES = 480


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A self-service credential (Settings, per-user — app/api/settings.py)
    letting an external system act as its owner via the API: create/read
    meetings, and authenticate the live WebSocket, without a browser login
    (app/api/deps.py:get_current_user_flexible,
    app/ws/live_session.py:_authenticate).

    Only `key_hash` (sha256 of the actual key, app/core/security.py:
    generate_api_key/hash_api_key) is stored — same rationale as a
    password hash, but sha256 rather than bcrypt is the right tool here:
    the key itself is already a high-entropy random token we generated
    (not a low-entropy user-chosen secret), so there's no offline-
    brute-force case to slow down, and the plaintext is genuinely
    unrecoverable once shown. `key_prefix` is plaintext specifically so
    the Settings list can identify a key without ever re-displaying it.
    """

    __tablename__ = "api_keys"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    key_prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    max_duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MAX_DURATION_MINUTES, server_default="60"
    )
    # Bumped on every successful authentication (REST or WS) — lets a user
    # tell a live, integrated key apart from one they forgot about.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
