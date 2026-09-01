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
    created_at: datetime
