from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.meeting import MeetingStatus


class MeetingCreate(BaseModel):
    title: str = "Untitled meeting"


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: MeetingStatus
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    has_audio: bool
    processing_error: str | None
    summary: str | None
    created_at: datetime


class MeetingSearchResult(BaseModel):
    meeting_id: UUID
    title: str
    status: MeetingStatus
    created_at: datetime
    snippet: str
    start_ms: int  # where in the recording the best-matching chunk starts
