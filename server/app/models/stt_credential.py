import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SttCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's Deepgram API key (app/services/asr/deepgram.py) — a
    deliberately separate, single-provider table from ProviderCredential
    (that one is explicitly LLM-scoped, pg_enum(LLMProvider, ...));
    Deepgram is speech-to-text, an unrelated concern, not an LLM. One row
    per user (owner_id is unique on its own, unlike ProviderCredential's
    owner_id+provider pair, since there's only one STT provider so far).
    """

    __tablename__ = "stt_credentials"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    # Encrypted with the same Fernet key as ProviderCredential (app.core.security).
    api_key_encrypted: Mapped[str] = mapped_column(Text)
