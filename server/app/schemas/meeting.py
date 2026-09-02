from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.meeting import CallType, MeetingStatus


class MeetingCreate(BaseModel):
    title: str = "Untitled meeting"
    call_type: CallType = CallType.MEETING


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: MeetingStatus
    call_type: CallType
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    has_audio: bool
    processing_error: str | None
    summary: str | None
    key_topics: list[str] | None
    sentiment: str | None
    notable_quotes: list[str] | None
    coach_score: int | None
    estimated_cost_usd: float | None
    created_at: datetime
    owner_id: UUID
    owner_name: str


class GroupMeetingRead(BaseModel):
    """A group-mate's meeting, for the Dashboard's group-browsing list —
    deliberately a narrower shape than MeetingRead: no has_audio/
    processing_error here, since this is what's shown *before* opening the
    meeting (which then re-checks group-visibility server-side anyway).
    summary/key_topics ARE included (unlike audio/transcript, which stay
    strictly owner/admin-only) — both are already part of the report shape
    a group member can read once they open the meeting; surfacing them here
    too is what lets the Dashboard's client-side group filter (title/
    summary/key_topics/owner_name) have something to search against
    without a new endpoint or crossing into transcript content."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: MeetingStatus
    summary: str | None
    key_topics: list[str] | None
    created_at: datetime
    owner_id: UUID
    owner_name: str


class MeetingSearchResult(BaseModel):
    meeting_id: UUID
    title: str
    status: MeetingStatus
    created_at: datetime
    snippet: str
    start_ms: int  # where in the recording the best-matching chunk starts
    owner_id: UUID
    owner_name: str
