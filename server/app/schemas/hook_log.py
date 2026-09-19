from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class HookLogRead(BaseModel):
    """Admin-only. Bodies and headers are already redacted at persist time."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    phase: str
    outcome: str
    method: str
    url: str
    request_headers: str | None
    request_body: str | None
    response_status: int | None
    response_body: str | None
    error: str | None
    duration_ms: int | None
    ran_async: bool
    created_at: datetime
