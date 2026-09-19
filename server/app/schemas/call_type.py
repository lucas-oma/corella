from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CallTypeOption(BaseModel):
    """The lightweight, public shape — every authenticated user needs this
    to create a meeting (GET /api/call-types), not just admins. No
    guidance/pre-post-call internals."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    is_default: bool


class CallTypeRead(BaseModel):
    """Admin-only full shape (GET/POST/PATCH /api/admin/call-types).
    Header fields are the stored templates (often {{secret.NAME}} refs) —
    decrypted for the admin UI. Real secret *values* live in app_secrets
    and are never returned."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    report_guidance: str | None
    is_default: bool

    pre_call_enabled: bool
    pre_call_url: str | None
    pre_call_method: str
    pre_call_headers: str | None = None
    pre_call_body_template: str | None
    pre_call_use_as_context: bool
    pre_call_async: bool

    post_call_enabled: bool
    post_call_url: str | None
    post_call_method: str
    post_call_headers: str | None = None
    post_call_body_template: str | None
    post_call_send_full_payload: bool
    post_call_async: bool


class CallTypeCreate(BaseModel):
    name: str
    slug: str
    report_guidance: str | None = None
    is_default: bool = False

    pre_call_enabled: bool = False
    pre_call_url: str | None = None
    pre_call_method: str = "GET"
    # Raw JSON object text, e.g. '{"Authorization": "Bearer {{secret.NAME}}"}'
    # — encrypted at rest; the template (not resolved values) is returned
    # on GET so admins can see which secrets a hook uses.
    pre_call_headers: str | None = None
    pre_call_body_template: str | None = None
    pre_call_use_as_context: bool = False
    pre_call_async: bool = False

    post_call_enabled: bool = False
    post_call_url: str | None = None
    post_call_method: str = "POST"
    post_call_headers: str | None = None
    post_call_body_template: str | None = None
    post_call_send_full_payload: bool = False
    post_call_async: bool = False


class CallTypeUpdate(BaseModel):
    """Every field optional — only what's actually present in the request
    body is applied (mirrors PreferencesUpdate's model_fields_set pattern
    in app/api/settings.py), so a PATCH can touch just one field."""

    name: str | None = None
    slug: str | None = None
    report_guidance: str | None = None
    is_default: bool | None = None

    pre_call_enabled: bool | None = None
    pre_call_url: str | None = None
    pre_call_method: str | None = None
    pre_call_headers: str | None = None
    pre_call_body_template: str | None = None
    pre_call_use_as_context: bool | None = None
    pre_call_async: bool | None = None

    post_call_enabled: bool | None = None
    post_call_url: str | None = None
    post_call_method: str | None = None
    post_call_headers: str | None = None
    post_call_body_template: str | None = None
    post_call_send_full_payload: bool | None = None
    post_call_async: bool | None = None
