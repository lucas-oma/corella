import enum
import uuid

from sqlalchemy import ARRAY, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User


class KBDocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class KBDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A knowledge-base source document. Chunked and embedded by a background
    worker into the shared Qdrant `kb_chunks` collection, scoped by an
    `owner_id` payload field (see app.services.embeddings), once status
    transitions past PENDING.
    """

    __tablename__ = "kb_documents"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[KBDocumentStatus] = mapped_column(
        pg_enum(KBDocumentStatus, "kb_document_status"), default=KBDocumentStatus.PENDING
    )
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    # LLM-extracted proper nouns/product names/acronyms/jargon from this
    # document's own text (app/services/embeddings/kb_keywords.py), fed to
    # Deepgram's `keywords` param / faster-whisper's `initial_prompt` at
    # transcription time (app/services/access.py:searchable_kb_keywords) to
    # bias STT toward whatever this group's knowledge base actually talks
    # about. None means extraction was never attempted or failed — never
    # blocks the document from reaching READY (best-effort, same
    # graceful-degradation spirit as the Deepgram-STT-falls-back-to-whisper
    # path in app/workers/tasks.py).
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    owner: Mapped[User] = relationship(lazy="joined")

    @property
    def owner_name(self) -> str:
        """Not a column — lets KBDocumentRead show whose document this is
        once a shared group knowledge base means it isn't always the
        viewer's own."""
        return self.owner.full_name
