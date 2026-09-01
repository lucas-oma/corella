import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import storage
from app.core.db import get_db
from app.models.meeting import Meeting, MeetingStatus, TranscriptSegment
from app.models.user import User
from app.schemas.meeting import MeetingCreate, MeetingRead
from app.schemas.transcript import TranscriptSegmentRead
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
    meeting = Meeting(owner_id=current_user.id, title=payload.title)
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)
    return meeting


@router.get("/{meeting_id}", response_model=MeetingRead)
async def get_meeting(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Meeting:
    return await _get_owned_meeting(meeting_id, current_user, db)


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


@router.get("/{meeting_id}/audio")
async def get_meeting_audio(
    meeting_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    meeting = await _get_owned_meeting(meeting_id, current_user, db)
    if not meeting.audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No audio for this meeting")

    return storage.range_response(meeting.audio_path, request.headers.get("range"))


@router.get("/{meeting_id}/transcript", response_model=list[TranscriptSegmentRead])
async def get_meeting_transcript(
    meeting_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TranscriptSegment]:
    await _get_owned_meeting(meeting_id, current_user, db)

    result = await db.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting_id)
        .order_by(TranscriptSegment.start_ms)
    )
    return list(result)
