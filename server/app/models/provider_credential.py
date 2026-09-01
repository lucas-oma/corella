import enum
import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class LLMProvider(str, enum.Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"


class ProviderCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's configuration for one LLM provider: an encrypted API key for
    hosted providers, or a base URL for a self-hosted Ollama instance.
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("owner_id", "provider", name="uq_owner_provider"),)

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[LLMProvider] = mapped_column(
        pg_enum(LLMProvider, "provider_credential_provider")
    )
    # Encrypted with a Fernet key derived from jwt_secret (see app.core.security);
    # never stored or returned in plaintext.
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(String(512))
