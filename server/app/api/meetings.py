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

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

_ALLOWED_AUDIO_TYPES = {
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/ogg",
    "audio/flac",
    "video/webm",  # MediaRecorder in the browser often tags audio-only as this
}


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

    content_type = (file.content_type or "").split(";")[0].strip()
    if content_type not in _ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content type: {file.content_type}",
        )

    meeting.audio_path = await storage.save_upload(meeting_id, file)
    meeting.status = MeetingStatus.PROCESSING
    meeting.processing_error = None
    await db.commit()
    await db.refresh(meeting)

    celery_app.send_task("corella.process_meeting_audio", args=[str(meeting_id)])

    return meeting


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
