from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.kb_document import KBDocumentStatus


class KBDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: str
    status: KBDocumentStatus
    chunk_count: int | None
    error: str | None
    created_at: datetime
    owner_id: UUID
    # Whoever uploaded it — always populated (falls back to "You" isn't done
    # here; the frontend decides that by comparing owner_id to itself).
    # Matters once a document can show up in someone else's list via a
    # shared group knowledge base, not just the uploader's own.
    owner_name: str
