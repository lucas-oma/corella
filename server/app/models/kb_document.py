import enum
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class KBDocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class KBDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A knowledge-base source document. Chunked and embedded into the
    per-user Qdrant `kb_chunks` collection by a background worker once
    status transitions past PENDING (see app.services.embeddings).
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
