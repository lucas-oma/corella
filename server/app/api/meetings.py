import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_auth_context,
    get_auth_context_flexible,
    require_org_admin,
    require_super_admin,
)
from app.core import storage
from app.core.db import get_db
from app.models.call_type import CallType
from app.models.group import Group, GroupMembership
from app.models.hook_log import HookLog
from app.models.meeting import (
    ActionItem,
    CaptureMode,
    CopilotInsight,
    Meeting,
    MeetingStatus,
    TranscriptSegment,
)
from app.models.user import User
from app.schemas.copilot_insight import CopilotInsightRead
from app.schemas.hook_log import HookLogRead
from app.schemas.meeting import GroupMeetingRead, MeetingCreate, MeetingRead, MeetingSearchResult
from app.schemas.report import ActionItemRead, ActionItemUpdate, ReportResponse
from app.schemas.transcript import TranscriptSegmentRead
from app.services.admin.call_hooks import dispatch_pre_call, record_hook_queue_failure
from app.services.copilot.report import ReportError, generate_report
from app.services.embeddings.qdrant_store import delete_meeting_chunks
from app.services.embeddings.qdrant_store import search_meetings as qdrant_search_meetings
from app.services.embeddings.query import embed_query
from app.services.llm.resolve import resolve_provider
from app.services.organizations import users_share_group
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
    meeting_id: UUID, ctx: AuthContext, db: AsyncSession
) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if (
        meeting is None
        or meeting.owner_id != ctx.user.id
        or meeting.organization_id != ctx.org_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return meeting


async def _get_group_visible_meeting(
    meeting_id: UUID, ctx: AuthContext, db: AsyncSession
) -> Meeting:
    """Owner OR shared group OR org admin OR super-admin — report-shaped
    reads only. Group membership alone never grants transcript/audio.
    """
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if ctx.is_super_admin:
        return meeting
    if meeting.organization_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if meeting.owner_id == ctx.user.id or ctx.is_org_admin:
        return meeting
    if await users_share_group(db, ctx.user.id, meeting.owner_id, ctx.org_id):
        return meeting
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")


async def _get_full_readable_meeting(
    meeting_id: UUID, ctx: AuthContext, db: AsyncSession
) -> Meeting:
    """Owner OR org admin OR super-admin — raw transcript/audio. Not group.
    Writes stay on _get_owned_meeting with no admin override.
    """
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if ctx.is_super_admin:
        return meeting
    if meeting.organization_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if meeting.owner_id == ctx.user.id or ctx.is_org_admin:
        return meeting
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")


@router.get("", response_model=list[MeetingRead])
async def list_meetings(
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[Meeting]:
    result = await db.scalars(
        select(Meeting)
        .where(Meeting.owner_id == ctx.user.id, Meeting.organization_id == ctx.org_id)
        .order_by(Meeting.created_at.desc())
    )
    return list(result)


@router.post("", response_model=MeetingRead, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    payload: MeetingCreate,
    ctx: AuthContext = Depends(get_auth_context_flexible),
    db: AsyncSession = Depends(get_db),
) -> Meeting:
    if payload.call_type_id is not None:
        call_type = await db.get(CallType, payload.call_type_id)
        if call_type is None or call_type.organization_id != ctx.org_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown call_type_id")
    else:
        call_type = await db.scalar(
            select(CallType).where(
                CallType.organization_id == ctx.org_id, CallType.is_default.is_(True)
            )
        )

    capture_mode = payload.capture_mode
    capture_app = payload.capture_app if capture_mode == CaptureMode.MEETING_TAB else None

    meeting = Meeting(
        owner_id=ctx.user.id,
        organization_id=ctx.org_id,
        title=payload.title,
        call_type_id=call_type.id if call_type else None,
        capture_mode=capture_mode,
        capture_app=capture_app,
    )
    db.add(meeting)
    await db.commit()
    # refresh() reloads meeting's own columns, not the lazy="joined"
    # owner/call_type relationships (never triggered at all for a
    # freshly-constructed object — nothing queried them yet) — both are
    # already right here, so set them directly rather than trust an
    # implicit relationship load. Same pattern as the pre-existing
    # owner-assignment below.
    await db.refresh(meeting)
    meeting.owner = ctx.user
    meeting.call_type = call_type

    # Pre-call: default sync so context exists before the 201 (and the
    # live socket) — bounded by settings.pre_call_timeout_seconds. Async
    # queues the same dispatch on the worker so create returns immediately;
    # live copilot re-reads pre_call_context each cycle. A no-op if this
    # call type has no pre-call configured; never raises on failure.
    if call_type is not None and call_type.pre_call_enabled and call_type.pre_call_url:
        if call_type.pre_call_async:
            try:
                celery_app.send_task("corella.dispatch_pre_call", args=[str(meeting.id)])
            except Exception:
                logger.exception("Failed to queue pre-call for meeting %s", meeting.id)
                await record_hook_queue_failure(db, meeting, "pre")
                await db.commit()
        else:
            context = await dispatch_pre_call(db, meeting)
            if call_type.pre_call_use_as_context and context:
                meeting.pre_call_context = context
            await db.commit()

    return meeting


async def _search_meetings(
    q: str,
    owner_id: UUID | None,
    db: AsyncSession,
    *,
    organization_id: UUID | None = None,
    unscoped: bool = False,
) -> list[MeetingSearchResult]:
    """Shared by both search routes below — embed -> Qdrant search -> keep
    the best-scoring chunk per meeting -> join Postgres for display fields.
    owner_id=None searches system-wide (admin only); otherwise scoped to
    that one owner, same as before this was split out.
    """
    q = q.strip()
    if not q:
        return []

    embedding = await embed_query(q)
    hits = qdrant_search_meetings(
        owner_id, embedding, top_k=10, organization_id=organization_id, unscoped=unscoped
    )

    # Qdrant returns results ordered best-first; keep only the first
    # (best-scoring) hit per meeting.
    best_by_meeting: dict[UUID, dict] = {}
    for hit in hits:
        meeting_id = UUID(hit["meeting_id"])
        if meeting_id not in best_by_meeting:
            best_by_meeting[meeting_id] = hit
    if not best_by_meeting:
        return []

    query = select(Meeting).where(Meeting.id.in_(best_by_meeting.keys()))
    if owner_id is not None:
        query = query.where(Meeting.owner_id == owner_id)
    if organization_id is not None:
        query = query.where(Meeting.organization_id == organization_id)
    meetings = await db.scalars(query)
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
                owner_id=meeting.owner_id,
                owner_name=meeting.owner_name,
            )
        )
    results.sort(key=lambda r: best_by_meeting[r.meeting_id]["score"], reverse=True)
    return results


@router.get("/search", response_model=list[MeetingSearchResult])
async def search_meetings(
    q: str,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[MeetingSearchResult]:
    """Semantic search over the caller's own transcript content in the
    active org. Registered before /{meeting_id}.
    """
    return await _search_meetings(q, ctx.user.id, db, organization_id=ctx.org_id)


@router.get("/search/org", response_model=list[MeetingSearchResult])
async def search_org_meetings(
    q: str,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MeetingSearchResult]:
    """Org-wide transcript search for org owner/admin."""
    return await _search_meetings(q, None, db, organization_id=ctx.org_id)


@router.get("/search/all", response_model=list[MeetingSearchResult])
async def search_all_meetings(
    q: str,
    _admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MeetingSearchResult]:
    """Instance-wide transcript search — super-admin only."""
    return await _search_meetings(q, None, db, unscoped=True)


@router.get("/group", response_model=list[GroupMeetingRead])
async def list_group_meetings(
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[Meeting]:
    """Group-mates' meetings in the active org — report-only list."""
    my_groups = select(GroupMembership.group_id).join(Group).where(
        GroupMembership.user_id == ctx.user.id,
        Group.organization_id == ctx.org_id,
    )
    teammate_ids = select(GroupMembership.user_id).where(
        GroupMembership.group_id.in_(my_groups),
        GroupMembership.user_id != ctx.user.id,
    )
    result = await db.scalars(
        select(Meeting)
        .where(
            Meeting.organization_id == ctx.org_id,
            Meeting.owner_id.in_(teammate_ids),
        )
        .order_by(Meeting.created_at.desc())
    )
    return list(result)


@router.get("/org", response_model=list[GroupMeetingRead])
async def list_org_meetings(
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> list[Meeting]:
    """Every meeting in the active org — org owner/admin All tab."""
    result = await db.scalars(
        select(Meeting)
        .where(Meeting.organization_id == ctx.org_id)
        .order_by(Meeting.created_at.desc())
    )
    return list(result)


@router.get("/all", response_model=list[GroupMeetingRead])
async def list_all_meetings(
    _admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> list[Meeting]:
    """Every meeting on the instance — super-admin only."""
    result = await db.scalars(select(Meeting).order_by(Meeting.created_at.desc()))
    return list(result)


@router.get("/{meeting_id}", response_model=MeetingRead)
async def get_meeting(
    meeting_id: UUID,
    ctx: AuthContext = Depends(get_auth_context_flexible),
    db: AsyncSession = Depends(get_db),
) -> Meeting:
    return await _get_group_visible_meeting(meeting_id, ctx, db)


@router.get("/{meeting_id}/hook-logs", response_model=list[HookLogRead])
async def list_meeting_hook_logs(
    meeting_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[HookLog]:
    """Org-admin (this org) or super-admin: redacted pre/post hook attempts."""
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not ctx.is_super_admin and (
        meeting.organization_id != ctx.org_id or not ctx.is_org_admin
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization admin privileges required")
    result = await db.scalars(
        select(HookLog).where(HookLog.meeting_id == meeting_id).order_by(HookLog.created_at)
    )
    return list(result)


@router.post("/{meeting_id}/audio", response_model=MeetingRead)
async def upload_meeting_audio(
    meeting_id: UUID,
    file: UploadFile,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> Meeting:
    meeting = await _get_owned_meeting(meeting_id, ctx, db)

    if not _looks_like_audio(file.filename, file.content_type):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Doesn't look like an audio file: {file.filename} ({file.content_type})",
        )

    meeting.audio_path = await storage.save_upload(meeting_id, file)
    meeting.status = MeetingStatus.PROCESSING
    meeting.processing_error = None
    meeting.capture_mode = CaptureMode.UPLOAD
    meeting.capture_app = None

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
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    meeting = await _get_owned_meeting(meeting_id, ctx, db)
    await db.delete(meeting)
    await db.commit()
    storage.delete_meeting_files(meeting_id)
    delete_meeting_chunks(meeting_id)


@router.get("/{meeting_id}/audio")
async def get_meeting_audio(
    meeting_id: UUID,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    meeting = await _get_full_readable_meeting(meeting_id, ctx, db)
    if not meeting.audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No audio for this meeting")

    return storage.range_response(meeting.audio_path, request.headers.get("range"))


@router.get("/{meeting_id}/transcript", response_model=list[TranscriptSegmentRead])
async def get_meeting_transcript(
    meeting_id: UUID,
    ctx: AuthContext = Depends(get_auth_context_flexible),
    db: AsyncSession = Depends(get_db),
) -> list[TranscriptSegment]:
    await _get_full_readable_meeting(meeting_id, ctx, db)

    result = await db.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting_id)
        .order_by(TranscriptSegment.start_ms)
    )
    return list(result)


@router.get("/{meeting_id}/insights", response_model=list[CopilotInsightRead])
async def get_meeting_insights(
    meeting_id: UUID,
    ctx: AuthContext = Depends(get_auth_context_flexible),
    db: AsyncSession = Depends(get_db),
) -> list[CopilotInsight]:
    """Every live-copilot cycle's persisted suggestion/blockers/coach_score
    (app/services/copilot/live.py:run_cycle), timestamp-ordered — shown next
    to the transcript on MeetingDetail. Same access boundary as the
    transcript itself (_get_full_readable_meeting, owner + admin only, no
    group): these are meaningless without the transcript context they're
    anchored to (at_ms), which already stops at that same boundary.
    """
    await _get_full_readable_meeting(meeting_id, ctx, db)

    result = await db.scalars(
        select(CopilotInsight)
        .where(CopilotInsight.meeting_id == meeting_id)
        .order_by(CopilotInsight.at_ms)
    )
    return list(result)


@router.post("/{meeting_id}/report", response_model=ReportResponse)
async def create_meeting_report(
    meeting_id: UUID,
    ctx: AuthContext = Depends(get_auth_context_flexible),
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    meeting = await _get_owned_meeting(meeting_id, ctx, db)

    provider = await resolve_provider(db, ctx.user.id)
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
        coach_score=result.coach_score,
        estimated_cost_usd=result.estimated_cost_usd,
        action_items=[ActionItemRead.model_validate(item) for item in result.action_items],
        talk_ratio=result.talk_ratio,
    )


@router.get("/{meeting_id}/action-items", response_model=list[ActionItemRead])
async def list_action_items(
    meeting_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[ActionItem]:
    await _get_group_visible_meeting(meeting_id, ctx, db)
    result = await db.scalars(
        select(ActionItem).where(ActionItem.meeting_id == meeting_id).order_by(ActionItem.created_at)
    )
    return list(result)


@router.patch("/{meeting_id}/action-items/{item_id}", response_model=ActionItemRead)
async def update_action_item(
    meeting_id: UUID,
    item_id: UUID,
    payload: ActionItemUpdate,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> ActionItem:
    await _get_owned_meeting(meeting_id, ctx, db)
    item = await db.get(ActionItem, item_id)
    if item is None or item.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action item not found")
    item.status = payload.status
    await db.commit()
    await db.refresh(item)
    return item
