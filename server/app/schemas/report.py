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
    summary: str
    action_items: list[ActionItemRead]
    talk_ratio: dict[str, int] | None
