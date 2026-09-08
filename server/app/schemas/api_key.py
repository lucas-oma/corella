from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreate(BaseModel):
    name: str


class ApiKeyCreated(ApiKeyRead):
    """Returned only from the create endpoint — the one and only time the
    real key is ever shown. Never returned from list/get; nothing else
    round-trips it, since only its hash is stored (app/models/api_key.py).
    """

    key: str
