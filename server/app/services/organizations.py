"""Organization lifecycle helpers — create, membership, default call types.

Isolation and request-scoped active org live in app.api.deps; this module
is the shared write path used by register, bootstrap, invites, and the
org router.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_invite_token
from app.models.call_type import CallType
from app.models.group import Group, GroupMembership
from app.models.organization import Organization, OrganizationMembership, OrgRole
from app.models.user import User

# Same guidance as alembic 0012's seed, copied into each new org so a
# signup isn't an empty call-type catalog. Hooks stay unset.
DEFAULT_CALL_TYPES: tuple[tuple[str, str, str, bool], ...] = (
    (
        "meeting",
        "Meeting",
        "This is a general meeting. Focus the summary and key_topics on decisions made and open "
        "questions left unresolved.",
        True,
    ),
    (
        "sales",
        "Sales call",
        "This is a sales call. Focus the summary and key_topics on the prospect's pain points, "
        "objections raised, budget/timeline signals, and next steps or deal stage. sentiment should "
        "reflect how receptive the prospect seemed. Prioritize quotes about pricing, timeline, or "
        "objections for notable_quotes.",
        False,
    ),
    (
        "support",
        "Support call",
        "This is a customer support call. Focus the summary and key_topics on the issue reported, "
        "whether it was resolved, and any escalation risk. sentiment should reflect the customer's "
        "frustration or satisfaction level. Prioritize quotes describing the problem or the "
        "resolution for notable_quotes.",
        False,
    ),
    (
        "interview",
        "Interview",
        "This is a job interview. Focus the summary and key_topics on the candidate's strengths, "
        "gaps, and fit signals relative to what was asked. sentiment should reflect how the "
        "conversation went overall. Prioritize quotes that reveal candidate strengths or concerns "
        "for notable_quotes.",
        False,
    ),
    (
        "one_on_one",
        "1:1",
        "This is a one-on-one check-in. Focus the summary and key_topics on blockers raised, growth "
        "or career topics, and commitments made by either person. sentiment should reflect the "
        "overall tone of the conversation. Prioritize quotes about blockers or commitments for "
        "notable_quotes.",
        False,
    ),
)


def default_org_name(full_name: str) -> str:
    name = (full_name or "").strip() or "User"
    return f"{name} Org"


def seed_default_call_types_sync(db: Session, organization_id: UUID) -> None:
    for slug, name, guidance, is_default in DEFAULT_CALL_TYPES:
        db.add(
            CallType(
                organization_id=organization_id,
                name=name,
                slug=slug,
                report_guidance=guidance,
                is_default=is_default,
            )
        )


async def seed_default_call_types(db: AsyncSession, organization_id: UUID) -> None:
    for slug, name, guidance, is_default in DEFAULT_CALL_TYPES:
        db.add(
            CallType(
                organization_id=organization_id,
                name=name,
                slug=slug,
                report_guidance=guidance,
                is_default=is_default,
            )
        )


@dataclass
class OrgActor:
    user: User
    organization: Organization
    membership: OrganizationMembership

    @property
    def org_id(self) -> UUID:
        return self.organization.id

    @property
    def is_org_owner(self) -> bool:
        return self.membership.role == OrgRole.OWNER

    @property
    def is_org_admin(self) -> bool:
        return self.membership.role in (OrgRole.OWNER, OrgRole.ADMIN)


async def owned_org_count(db: AsyncSession, user_id: UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.role == OrgRole.OWNER,
        )
    )
    return int(count or 0)


async def get_membership(
    db: AsyncSession, user_id: UUID, organization_id: UUID
) -> OrganizationMembership | None:
    return await db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )


async def get_instance_org(db: AsyncSession) -> Organization | None:
    return await db.scalar(select(Organization).where(Organization.is_instance_org.is_(True)))


async def create_organization(
    db: AsyncSession,
    *,
    owner: User,
    name: str,
    is_instance_org: bool = False,
    activate: bool = True,
) -> Organization:
    org = Organization(name=name, is_instance_org=is_instance_org)
    db.add(org)
    await db.flush()
    db.add(
        OrganizationMembership(
            user_id=owner.id, organization_id=org.id, role=OrgRole.OWNER
        )
    )
    await seed_default_call_types(db, org.id)
    if activate:
        owner.active_organization_id = org.id
    return org


async def add_membership(
    db: AsyncSession,
    *,
    user: User,
    organization_id: UUID,
    role: OrgRole,
    activate: bool = False,
) -> OrganizationMembership:
    membership = OrganizationMembership(
        user_id=user.id, organization_id=organization_id, role=role
    )
    db.add(membership)
    if activate or user.active_organization_id is None:
        user.active_organization_id = organization_id
    return membership


async def resolve_active_membership(
    db: AsyncSession, user: User
) -> tuple[Organization, OrganizationMembership] | None:
    """Return the user's active org membership, auto-picking another if
    the stored active id is missing or stale. Persists the pick.
    """
    if user.active_organization_id is not None:
        membership = await get_membership(db, user.id, user.active_organization_id)
        if membership is not None:
            org = await db.get(Organization, membership.organization_id)
            if org is not None:
                return org, membership

    membership = await db.scalar(
        select(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)
        .order_by(OrganizationMembership.created_at)
    )
    if membership is None:
        return None
    org = await db.get(Organization, membership.organization_id)
    if org is None:
        return None
    user.active_organization_id = org.id
    return org, membership


async def group_ids_for_user(db: AsyncSession, user_id: UUID, organization_id: UUID) -> list[UUID]:
    rows = await db.scalars(
        select(GroupMembership.group_id)
        .join(Group, Group.id == GroupMembership.group_id)
        .where(
            GroupMembership.user_id == user_id,
            Group.organization_id == organization_id,
        )
    )
    return list(rows)


async def users_share_group(
    db: AsyncSession, user_a: UUID, user_b: UUID, organization_id: UUID
) -> bool:
    a_groups = await group_ids_for_user(db, user_a, organization_id)
    if not a_groups:
        return False
    shared = await db.scalar(
        select(GroupMembership.id).where(
            GroupMembership.user_id == user_b,
            GroupMembership.group_id.in_(a_groups),
        )
    )
    return shared is not None


def mint_invite_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_invite_token(raw)


def invite_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=get_settings().invite_expire_days)
