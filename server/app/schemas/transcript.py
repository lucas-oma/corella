from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.meeting import Channel


class TranscriptSegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    speaker_label: str | None
    # Set only when speaker_label resolves to an enrolled account, not an
    # anonymous recognized-by-name guest — the viewer renders "Me" only
    # when this equals their own id (see Speaker.linked_user_id).
    linked_user_id: UUID | None
    channel: Channel
    start_ms: int
    end_ms: int
    text: str
