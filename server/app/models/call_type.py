from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CallType(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Admin-managed call type — replaces the old hardcoded enum
    (meeting/sales/support/interview/one_on_one). Steers the post-call
    report's focus via `report_guidance` (appended to the report prompt,
    see app/services/copilot/report.py), and can optionally fire a
    webhook once a call of this type finishes automatic post-call
    processing (see app/services/admin/webhooks.py).
    """

    __tablename__ = "call_types"

    name: Mapped[str] = mapped_column(String(255))
    # Stable, admin-facing-but-machine-safe key — lowercase-hyphenated.
    # Not used for lookups anywhere in code (Meeting references a row by
    # id, not slug); kept mainly so a migration/export has a stable
    # identifier independent of a display-name rename.
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    report_guidance: Mapped[str | None] = mapped_column(Text)
    # Exactly one row should have this true at a time — enforced in
    # application code (app/api/admin.py), not a DB constraint: unsetting
    # every other row happens in the same transaction as setting this one.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    webhook_method: Mapped[str] = mapped_column(String(16), default="POST")
    # Encrypted with the same Fernet key derived from jwt_secret every
    # other credential in this app uses (app.core.security) — headers
    # commonly carry an Authorization value. Write-only, never returned
    # by the API after saving, same convention as every other secret.
    webhook_headers_encrypted: Mapped[str | None] = mapped_column(Text)
    # Raw JSON text with {{placeholder}} tokens — see
    # app/services/admin/webhooks.py:render_template for the substitution
    # rules and the supported placeholder list.
    webhook_body_template: Mapped[str | None] = mapped_column(Text)
