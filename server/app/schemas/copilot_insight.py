from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CopilotInsightRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    at_ms: int
    suggestion: str | None
    blockers: list[str]
    coach_score: int | None
