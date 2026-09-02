from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CallTypeOption(BaseModel):
    """The lightweight, public shape — every authenticated user needs this
    to create a meeting (GET /api/call-types), not just admins. No
    guidance/webhook internals."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    is_default: bool


class CallTypeRead(BaseModel):
    """Admin-only full shape (GET/POST/PATCH /api/admin/call-types).
    webhook_headers is deliberately absent — write-only, same
    secret-handling convention as every other credential in this app."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    report_guidance: str | None
    is_default: bool
    webhook_enabled: bool
    webhook_url: str | None
    webhook_method: str
    webhook_body_template: str | None


class CallTypeCreate(BaseModel):
    name: str
    slug: str
    report_guidance: str | None = None
    is_default: bool = False
    webhook_enabled: bool = False
    webhook_url: str | None = None
    webhook_method: str = "POST"
    # Raw JSON object text, e.g. '{"Authorization": "Bearer ..."}' —
    # encrypted at rest (app.core.security.encrypt_secret), never returned.
    webhook_headers: str | None = None
    webhook_body_template: str | None = None


class CallTypeUpdate(BaseModel):
    """Every field optional — only what's actually present in the request
    body is applied (mirrors PreferencesUpdate's model_fields_set pattern
    in app/api/settings.py), so a PATCH can touch just one field."""

    name: str | None = None
    slug: str | None = None
    report_guidance: str | None = None
    is_default: bool | None = None
    webhook_enabled: bool | None = None
    webhook_url: str | None = None
    webhook_method: str | None = None
    webhook_headers: str | None = None
    webhook_body_template: str | None = None
