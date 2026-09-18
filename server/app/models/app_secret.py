from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AppSecret(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Instance-wide named secret (Admin / Settings → Secrets).

    Values are encrypted at rest (app.core.security.encrypt_secret) and
    never returned by the API — list/create/update expose `name` only.
    Call-type pre/post headers reference them as {{secret.NAME}} and
    dispatch interpolates the real value (app/services/admin/call_hooks.py).
    """

    __tablename__ = "app_secrets"

    # Machine-safe identifier used in {{secret.NAME}} — unique, validated
    # in the schema (letter then letters/digits/underscores).
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value_encrypted: Mapped[str] = mapped_column(Text)
