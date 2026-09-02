import enum
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.provider_credential import LLMProvider


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), default=UserRole.MEMBER
    )
    # Admin-assigned, nullable — an ungrouped user (the default) is fully
    # isolated, same as before groups existed at all. One group per user,
    # not many-to-many (app/models/group.py).
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL")
    )

    # Explicit per-user overrides of the otherwise-automatic provider/model
    # resolution (app/services/llm/resolve.py, app/services/asr/resolve.py).
    # All nullable and all default to nothing — unset means "keep the
    # existing fixed-priority auto behavior," so every account that never
    # visits Settings' new selectors is completely unaffected.
    preferred_llm_provider: Mapped[LLMProvider | None] = mapped_column(
        pg_enum(LLMProvider, "user_preferred_llm_provider")
    )
    preferred_llm_model: Mapped[str | None] = mapped_column(String(255))
    # Plain string ("deepgram" | "whisper"), not a pg enum — mirrors
    # ResolvedStt.provider's existing shape; not worth a DB enum for two
    # values that live entirely in application code today.
    preferred_stt_provider: Mapped[str | None] = mapped_column(String(32))
    preferred_stt_model: Mapped[str | None] = mapped_column(String(255))
    # None means Deepgram's automatic multi-language detection ("multi") —
    # see app/services/asr/resolve.py for why that's the default rather than
    # leaving the request-level `language` param unset entirely.
    preferred_stt_language: Mapped[str | None] = mapped_column(String(32))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"
