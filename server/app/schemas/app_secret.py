import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

# Same character class call-hook interpolation accepts
# (app/services/admin/call_hooks.py:_SECRET_REF).
SECRET_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class AppSecretRead(BaseModel):
    """Name only — the value is write-only, same convention as every
    other credential in this app."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class AppSecretCreate(BaseModel):
    name: str
    value: str

    @field_validator("name")
    @classmethod
    def name_is_machine_safe(cls, value: str) -> str:
        name = value.strip()
        if not SECRET_NAME_RE.fullmatch(name):
            raise ValueError(
                "Name must start with a letter and contain only letters, digits, and underscores"
            )
        return name

    @field_validator("value")
    @classmethod
    def value_required(cls, value: str) -> str:
        if not value:
            raise ValueError("Value is required")
        return value


class AppSecretUpdate(BaseModel):
    """Partial — omit `value` (or send null) to keep the stored secret;
    omit `name` to keep the current name. An empty string value is
    rejected; clearing a secret is a delete."""

    name: str | None = None
    value: str | None = None

    @field_validator("name")
    @classmethod
    def name_is_machine_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip()
        if not SECRET_NAME_RE.fullmatch(name):
            raise ValueError(
                "Name must start with a letter and contain only letters, digits, and underscores"
            )
        return name

    @field_validator("value")
    @classmethod
    def value_not_blank(cls, value: str | None) -> str | None:
        if value is not None and value == "":
            raise ValueError("Value cannot be empty — omit it to keep the current secret")
        return value
