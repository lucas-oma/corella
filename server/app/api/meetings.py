import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core import storage
from app.core.db import get_db
from app.models.meeting import ActionItem, Meeting, MeetingStatus, TranscriptSegment
from app.models.user import User, UserRole
from app.schemas.meeting import GroupMeetingRead, MeetingCreate, MeetingRead, MeetingSearchResult
from app.schemas.report import ActionItemRead, ActionItemUpdate, ReportResponse
from app.schemas.transcript import TranscriptSegmentRead
from app.services.copilot.report import ReportError, generate_report
from app.services.embeddings.qdrant_store import delete_meeting_chunks
from app.services.embeddings.qdrant_store import search_meetings as qdrant_search_meetings
from app.services.embeddings.query import embed_query
from app.services.llm.resolve import resolve_provider
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

_ALLOWED_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".oga", ".flac", ".aac", ".opus", ".caf",
}


def _looks_like_audio(filename: str | None, content_type: str | None) -> bool:
    """Browsers/OSes are inconsistent about what Content-Type they report
    for a given file (an exact-match allowlist was silently rejecting real
    audio files), so accept on either signal — a recognized extension, or a
    content-type that at least claims to be audio. ffmpeg is the real
    validator: it runs in the worker and produces a clear, user-visible
    error (Meeting.processing_error) if the file turns out not to be audio.
    """
    if Path(filename or "").suffix.lower() in _ALLOWED_AUDIO_EXTENSIONS:
        return True
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct.startswith("audio/") or ct == "video/webm"


async def _get_owned_meeting(
    meeting_id: UUID, current_user: User, db: AsyncSession
) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or meeting.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return meeting


async def _get_group_visible_meeting(
    meeting_id: UUID, current_user: User, db: AsyncSession
) -> Meeting:
    """Owner OR same group as the owner OR an admin — used *only* by the
    report-shaped reads (GET /{id}, GET /{id}/action-items). Every other
    route (audio, transcript, report generation, action-item edits, delete,
    upload) stays on the strict _get_owned_meeting above (or, for admins,
    _get_full_readable_meeting below) — group membership alone only ever
    grants the report, never the raw recording.
    """
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if meeting.owner_id == current_user.id or current_user.role == UserRole.ADMIN:
        return meeting
    if current_user.group_id is not None:
        owner_group_id = await db.scalar(select(User.group_id).where(User.id == meeting.owner_id))
        if owner_group_id is not None and owner_group_id == current_user.group_id:
            return meeting
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")


async def _get_full_readable_meeting(
    meeting_id: UUID, current_user: User, db: AsyncSession
) -> Meeting:
    """Owner OR an admin — used *only* by the two raw-content reads (GET
    .../audio, GET .../transcript). Deliberately does NOT include group
    membership: a group-mate only ever gets the report-shaped view via
    _get_group_visible_meeting above, never the recording itself. Read-only
    — every write route (delete, upload, report generation, action-item
    edits) stays on the strict owner-only _get_owned_meeting, with no admin
    override, even for an admin.
    """
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or (meeting.owner_id != current_user.id and current_user.role != UserRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return meeting


@router.get("", response_model=list[MeetingRead])
async def list_meetings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Meeting]:
    result = await db.scalars(
        select(Meeting)
        .where(Meeting.owner_id == current_user.id)
        .order_by(Meeting.created_at.desc())
    )
    return list(result)


@router.post("", response_model=MeetingRead, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    payload: MeetingCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meeting:
    meeting = Meeting(owner_id=current_user.id, title=payload.title, call_type=payload.call_type)
    db.add(meeting)
    await db.commit()
    # refresh() reloads meeting's own columns, not the lazy="joined" owner
    # relationship (never triggered at all for a freshly-constructed object
    # — nothing queried it yet) — MeetingRead needs owner_name, and it's
    # already right here on current_user, so just set it directly rather
    # than trust an implicit relationship load.
    await db.refresh(meeting)
    meeting.owner = current_user
    return meeting


@router.get("/search", response_model=list[MeetingSearchResult])
async def search_meetings(
    q: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MeetingSearchResult]:
    """Semantic search over transcript content (app/services/embeddings/,
    corella.index_meeting_search), not a title/substring filter. Registered
    before /{meeting_id} — route order matters here, or "search" would be
    parsed as a meeting_id UUID and 422 instead of matching this route.
    """
    q = q.strip()
    if not q:
        return []

    embedding = await embed_query(q)
    hits = qdrant_search_meetings(current_user.id, embedding, top_k=10)

    # Qdrant returns results ordered best-first; keep only the first
    # (best-scoring) hit per meeting.
    best_by_meeting: dict[UUID, dict] = {}
    for hit in hits:
        meeting_id = UUID(hit["meeting_id"])
        if meeting_id not in best_by_meeting:
            best_by_meeting[meeting_id] = hit
    if not best_by_meeting:
        return []

    meetings = await db.scalars(
        select(Meeting).where(
            Meeting.id.in_(best_by_meeting.keys()), Meeting.owner_id == current_user.id
        )
    )
    results = []
    for meeting in meetings:
        hit = best_by_meeting[meeting.id]
        results.append(
            MeetingSearchResult(
                meeting_id=meeting.id,
                title=meeting.title,
                status=meeting.status,
                created_at=meeting.created_at,
                snippet=hit["text"],
                start_ms=hit["start_ms"],
            )
        )
    results.sort(key=lambda r: best_by_meeting[r.meeting_id]["score"], reverse=True)
    return results


@router.get("/group", response_model=list[GroupMeetingRead])
async def list_group_meetings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Meeting]:
    """Group-mates' meetings, for the Dashboard's group-browsing tab —
    report-only visibility (title/status/who/when), not the full
    MeetingRead shape; opening one still goes through
    _get_group_visible_meeting like any other access, this list is just
    for browsing. Empty for an ungrouped user, not an error. Registered
    before /{meeting_id} — same route-order reason as /search.
    """
    if current_user.group_id is None:
        return []
    result = await db.scalars(
        select(Meeting)
        .join(User, User.id == Meeting.owner_id)
        .where(User.group_id == current_user.group_id, Meeting.owner_id != current_user.id)
        .order_by(Meeting.created_at.desc())
    )
    return list(result)


@router.get("/all", response_model=list[GroupMeetingRead])
async def list_all_meetings(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[Meeting]:
    """Every meeting in the system, for the Dashboard's admin-only "All
    meetings" tab — same report-shaped list shape as /group (title/status/
    who/when), not the full MeetingRead. Opening one goes through
    _get_group_visible_meeting (report) or _get_full_readable_meeting
    (audio/transcript) like any other access — this list is just for
    browsing. require_admin (app/api/deps.py) 403s a non-admin outright.
    Registered before /{meeting_id} — same route-order reason as /search
    and /group.
    """
    result = await db.scalars(select(Meeting).order_by(Meeting.created_at.desc()))
    return list(result)


@router.get("/{meeting_id}", response_model=MeetingRead)
async def get_meeting(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meeting:
    return await _get_group_visible_meeting(meeting_id, current_user, db)


@router.post("/{meeting_id}/audio", response_model=MeetingRead)
async def upload_meeting_audio(
    meeting_id: UUID,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meeting:
    meeting = await _get_owned_meeting(meeting_id, current_user, db)

    if not _looks_like_audio(file.filename, file.content_type):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Doesn't look like an audio file: {file.filename} ({file.content_type})",
        )

    meeting.audio_path = await storage.save_upload(meeting_id, file)
    meeting.status = MeetingStatus.PROCESSING
    meeting.processing_error = None

    try:
        celery_app.send_task("corella.process_meeting_audio", args=[str(meeting_id)])
    except Exception:
        # Couldn't even hand the job off (e.g. Redis unreachable) — land on
        # `failed` with a clear reason rather than leaving the meeting stuck
        # on `processing` forever with nothing ever going to work on it.
        logger.exception("Failed to dispatch process_meeting_audio for meeting %s", meeting_id)
        meeting.status = MeetingStatus.FAILED
        meeting.processing_error = "Could not start processing — the background worker is unreachable."

    await db.commit()
    await db.refresh(meeting)
    return meeting


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    meeting = await _get_owned_meeting(meeting_id, current_user, db)
    await db.delete(meeting)
    await db.commit()
    storage.delete_meeting_files(meeting_id)
    delete_meeting_chunks(meeting_id)


@router.get("/{meeting_id}/audio")
async def get_meeting_audio(
    meeting_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    meeting = await _get_full_readable_meeting(meeting_id, current_user, db)
    if not meeting.audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No audio for this meeting")

    return storage.range_response(meeting.audio_path, request.headers.get("range"))


@router.get("/{meeting_id}/transcript", response_model=list[TranscriptSegmentRead])
async def get_meeting_transcript(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TranscriptSegment]:
    await _get_full_readable_meeting(meeting_id, current_user, db)

    result = await db.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting_id)
        .order_by(TranscriptSegment.start_ms)
    )
    return list(result)


@router.post("/{meeting_id}/report", response_model=ReportResponse)
async def create_meeting_report(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    meeting = await _get_owned_meeting(meeting_id, current_user, db)

    provider = await resolve_provider(db, current_user.id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No LLM provider connected — add one in Settings first.",
        )

    try:
        result = await generate_report(db, meeting, provider)
    except ReportError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    return ReportResponse(
        title=result.title,
        summary=result.summary,
        key_topics=result.key_topics,
        sentiment=result.sentiment,
        notable_quotes=result.notable_quotes,
        action_items=[ActionItemRead.model_validate(item) for item in result.action_items],
        talk_ratio=result.talk_ratio,
    )


@router.get("/{meeting_id}/action-items", response_model=list[ActionItemRead])
async def list_action_items(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ActionItem]:
    await _get_group_visible_meeting(meeting_id, current_user, db)
    result = await db.scalars(
        select(ActionItem).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.created_at)
    )
    return list(result)


@router.patch("/{meeting_id}/action-items/{item_id}", response_model=ActionItemRead)
async def update_action_item(
    meeting_id: UUID,
    item_id: UUID,
    payload: ActionItemUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionItem:
    await _get_owned_meeting(meeting_id, current_user, db)
    item = await db.get(ActionItem, item_id)
    if item is None or item.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action item not found")
    item.status = payload.status
    await db.commit()
    await db.refresh(item)
    return item
