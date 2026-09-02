from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.meeting import ActionItemStatus


class ActionItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    text: str
    status: ActionItemStatus


class ActionItemUpdate(BaseModel):
    status: ActionItemStatus


class ReportResponse(BaseModel):
    title: str
    summary: str
    key_topics: list[str]
    sentiment: str | None
    notable_quotes: list[str]
    coach_score: int | None
    action_items: list[ActionItemRead]
    talk_ratio: dict[str, int] | None
