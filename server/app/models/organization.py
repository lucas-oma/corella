import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class OrgRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant / company. Isolation boundary for meetings, KB, call types,
    secrets, groups, and API keys. `is_instance_org` marks the single
    company org used when public registration is closed (self-host).
    """

    __tablename__ = "organizations"
    __table_args__ = (
        Index(
            "uq_one_instance_org",
            "is_instance_org",
            unique=True,
            postgresql_where=text("is_instance_org"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255))
    is_instance_org: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class OrganizationMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's role inside one organization. Instance super-admin is a
    flag on User, not a membership role.
    """

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_org_membership_user"),
        Index(
            "uq_org_one_owner",
            "organization_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[OrgRole] = mapped_column(pg_enum(OrgRole, "org_role"), default=OrgRole.MEMBER)


class OrganizationInvite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Copy-link invite into an organization. The raw token is shown once
    in the URL; only `token_hash` is stored (sha256, same idea as API keys).
    """

    __tablename__ = "organization_invites"
    __table_args__ = (
        Index(
            "uq_org_pending_invite_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[OrgRole] = mapped_column(pg_enum(OrgRole, "org_role"), default=OrgRole.MEMBER)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
