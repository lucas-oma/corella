from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.api_key import (
    DEFAULT_MAX_DURATION_MINUTES,
    MAX_MAX_DURATION_MINUTES,
    MIN_MAX_DURATION_MINUTES,
)


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    max_duration_minutes: int
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreate(BaseModel):
    name: str
    max_duration_minutes: int = Field(
        default=DEFAULT_MAX_DURATION_MINUTES,
        ge=MIN_MAX_DURATION_MINUTES,
        le=MAX_MAX_DURATION_MINUTES,
    )


class ApiKeyUpdate(BaseModel):
    """Currently just the duration cap — that's the one field a key's
    owner might want to change without rotating the credential itself."""

    max_duration_minutes: int = Field(
        ge=MIN_MAX_DURATION_MINUTES,
        le=MAX_MAX_DURATION_MINUTES,
    )


class ApiKeyCreated(ApiKeyRead):
    """Returned only from the create endpoint — the one and only time the
    real key is ever shown. Never returned from list/get; nothing else
    round-trips it, since only its hash is stored (app/models/api_key.py).
    """

    key: str
