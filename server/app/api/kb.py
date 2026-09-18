import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core import storage
from app.core.db import get_db
from app.models.group import Group
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.user import User
from app.schemas.kb import KBDocumentRead
from app.services.access import kb_visible_clause
from app.services.embeddings.qdrant_store import delete_document_chunks
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])

_ALLOWED_KB_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}


def _looks_like_kb_document(filename: str | None, content_type: str | None) -> bool:
    """Same rationale as _looks_like_audio in api/meetings.py: browsers are
    inconsistent about Content-Type, so accept on extension OR a plausible
    content-type, and let the worker's extraction step be the real gate.
    """
    if Path(filename or "").suffix.lower() in _ALLOWED_KB_EXTENSIONS:
        return True
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct in {"application/pdf", "text/plain", "text/markdown", "text/x-markdown"}


@router.get("/documents", response_model=list[KBDocumentRead])
async def list_kb_documents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[KBDocument]:
    """Members see their group's assigned documents (plus any unassigned
    docs they uploaded). Admins see every document so they can maintain
    every group's knowledge base.
    """
    result = await db.scalars(
        select(KBDocument)
        .where(kb_visible_clause(current_user, admin_sees_all=True))
        .order_by(KBDocument.created_at.desc())
    )
    return list(result)


@router.post("/documents", response_model=KBDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_kb_document(
    file: UploadFile,
    group_id: UUID | None = Form(default=None),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> KBDocument:
    """Admin-only. `group_id` assigns the document to that group's shared
    pool (copilot + member list). Omit it for an unassigned document.
    """
    if not _looks_like_kb_document(file.filename, file.content_type):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file: {file.filename} ({file.content_type}). "
            "Accepted: PDF, Markdown, plain text.",
        )

    group: Group | None = None
    if group_id is not None:
        group = await db.get(Group, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    document = KBDocument(
        owner_id=current_user.id,
        group_id=group_id,
        filename=file.filename or "untitled",
        content_type=file.content_type or "application/octet-stream",
        storage_path="",
        status=KBDocumentStatus.PENDING,
    )
    db.add(document)
    await db.flush()  # assign document.id before it's used as the storage dir name

    document.storage_path = await storage.save_kb_upload(document.id, file)

    try:
        celery_app.send_task("corella.process_kb_document", args=[str(document.id)])
    except Exception:
        logger.exception("Failed to dispatch process_kb_document for document %s", document.id)
        document.status = KBDocumentStatus.FAILED
        document.error = "Could not start processing — the background worker is unreachable."

    await db.commit()
    # Same reasoning as meetings.py:create_meeting — refresh() doesn't
    # populate the lazy="joined" owner relationship for a freshly-
    # constructed object, and KBDocumentRead needs owner_name.
    await db.refresh(document)
    document.owner = current_user
    document.group = group
    return document


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb_document(
    document_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    document = await db.get(KBDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    await db.delete(document)
    await db.commit()

    storage.delete_kb_document_files(document_id)
    delete_document_chunks(document_id)
