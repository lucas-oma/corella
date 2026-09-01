from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.meeting import Channel


class TranscriptSegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    speaker_label: str | None
    channel: Channel
    start_ms: int
    end_ms: int
    text: str
