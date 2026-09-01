import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.provider_credential import LLMProvider


class CallProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reusable copilot configuration (which LLM + prompt to use) that can
    be attached to a meeting, e.g. "Sales discovery call" vs "1:1 review".
    """

    __tablename__ = "call_profiles"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    llm_provider: Mapped[LLMProvider] = mapped_column(
        pg_enum(LLMProvider, "call_profile_llm_provider"), default=LLMProvider.ANTHROPIC
    )
    llm_model: Mapped[str] = mapped_column(String(255), default="claude-sonnet-5")
    instructions: Mapped[str | None] = mapped_column(Text)
