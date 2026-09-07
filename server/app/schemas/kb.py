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
    # LLM-extracted proper nouns/product names/acronyms/jargon (app/
    # services/embeddings/kb_keywords.py) — None if extraction hasn't run
    # yet, failed, or found nothing distinctive. Fed to Deepgram/Whisper at
    # transcription time (app/services/access.py:searchable_kb_keywords);
    # surfaced here too so it's visible, not just a backend-only effect.
    keywords: list[str] | None
