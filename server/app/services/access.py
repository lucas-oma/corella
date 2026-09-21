from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.core.config import get_settings
from app.models.group import GroupMembership
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.organization import OrgRole
from app.models.user import User
from app.services.organizations import group_ids_for_user


async def searchable_owner_ids(
    db: AsyncSession, owner_id: UUID, organization_id: UUID
) -> list[UUID]:
    """Whose knowledge-base documents `owner_id` can search inside this
    org: themselves, plus every member of any group they belong to. Not
    used for meeting search/transcripts.
    """
    group_ids = await group_ids_for_user(db, owner_id, organization_id)
    if not group_ids:
        return [owner_id]
    member_ids = await db.scalars(
        select(GroupMembership.user_id).where(GroupMembership.group_id.in_(group_ids))
    )
    ids = {owner_id, *member_ids}
    return list(ids)


def kb_visible_clause(
    user: User,
    organization_id: UUID,
    *,
    group_ids: list[UUID],
    org_admin: bool = False,
) -> ColumnElement[bool]:
    """Which kb_documents this user may list / copilot-search in the
    active org.

    Org owner/admin see every document in the org (maintenance). A grouped
    member sees documents assigned to their groups, plus unassigned docs
    they uploaded. An ungrouped member sees only their own unassigned docs.
    """
    in_org = KBDocument.organization_id == organization_id
    if org_admin:
        return in_org
    if group_ids:
        return and_(
            in_org,
            or_(
                KBDocument.group_id.in_(group_ids),
                and_(KBDocument.group_id.is_(None), KBDocument.owner_id == user.id),
            ),
        )
    return and_(in_org, KBDocument.group_id.is_(None), KBDocument.owner_id == user.id)


async def searchable_kb_keywords(
    db: AsyncSession, owner_id: UUID, organization_id: UUID
) -> list[str]:
    """Every distinct keyword from READY KB documents `owner_id` can
    search in this org — same visibility as copilot.
    """
    user = await db.get(User, owner_id)
    if user is None:
        return []
    group_ids = await group_ids_for_user(db, owner_id, organization_id)
    from app.models.organization import OrganizationMembership

    membership = await db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == owner_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )
    org_admin = membership is not None and membership.role in (OrgRole.OWNER, OrgRole.ADMIN)
    rows = await db.scalars(
        select(KBDocument.keywords).where(
            kb_visible_clause(user, organization_id, group_ids=group_ids, org_admin=org_admin),
            KBDocument.status == KBDocumentStatus.READY,
            KBDocument.keywords.is_not(None),
        )
    )
    limit = get_settings().stt_keyword_limit
    seen: dict[str, str] = {}
    for keywords in rows:
        for keyword in keywords:
            key = keyword.lower()
            if key not in seen:
                seen[key] = keyword
                if len(seen) >= limit:
                    return list(seen.values())
    return list(seen.values())
