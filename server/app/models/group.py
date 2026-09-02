from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Group(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Admin-managed set of users who share a knowledge base pool (and, once
    Phase H2 exists, cross-meeting voice recognition) and can see each
    other's meeting reports — but never each other's raw transcript/audio,
    and never each other's meeting search. One group per user (User.group_id),
    not many-to-many, for now.
    """

    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String(255))
