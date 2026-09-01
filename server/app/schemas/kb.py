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
