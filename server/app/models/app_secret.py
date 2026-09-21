import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AppSecret(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Org-scoped named secret (Organization → Secrets).

    Values are encrypted at rest (app.core.security.encrypt_secret) and
    never returned by the API — list/create/update expose `name` only.
    Call-type pre/post headers reference them as {{secret.NAME}} and
    dispatch interpolates the real value (app/services/admin/call_hooks.py).
    """

    __tablename__ = "app_secrets"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_app_secrets_org_name"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    # Machine-safe identifier used in {{secret.NAME}} — unique per org,
    # validated in the schema (letter then letters/digits/underscores).
    name: Mapped[str] = mapped_column(String(64), index=True)
    value_encrypted: Mapped[str] = mapped_column(Text)
