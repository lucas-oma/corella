import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    get_auth_context,
    get_current_user,
    get_optional_user,
    require_org_admin,
    require_org_owner,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import create_access_token, hash_invite_token, hash_password
from app.models.group import Group, GroupMembership
from app.models.organization import Organization, OrganizationInvite, OrganizationMembership, OrgRole
from app.models.user import User
from app.models.voice_identity import VoiceIdentity
from app.schemas.group import GroupCreate, GroupRead
from app.schemas.organization import (
    CurrentOrgUpdate,
    GroupMemberUpdate,
    InviteAccept,
    InviteCreate,
    InvitePreview,
    InviteRead,
    MemberRead,
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
    TransferOwnership,
)
from app.schemas.user import MemberCreate, MemberUpdate, OrgMembershipRead, Token, UserRead
from app.services.email import send_invite_email
from app.services.organizations import (
    add_membership,
    create_organization,
    default_org_name,
    get_instance_org,
    get_membership,
    group_ids_for_user,
    invite_expiry,
    mint_invite_token,
    owned_org_count,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/organizations", tags=["organizations"])
invites_router = APIRouter(prefix="/api/invites", tags=["invites"])


def _org_read(org: Organization, role: OrgRole) -> OrganizationRead:
    return OrganizationRead(
        id=org.id,
        name=org.name,
        is_instance_org=org.is_instance_org,
        role=role,
        created_at=org.created_at,
    )


async def build_user_read(db: AsyncSession, user: User) -> UserRead:
    memberships = (
        await db.execute(
            select(Organization, OrganizationMembership.role)
            .join(OrganizationMembership, OrganizationMembership.organization_id == Organization.id)
            .where(OrganizationMembership.user_id == user.id)
            .order_by(Organization.created_at)
        )
    ).all()
    group_ids: list[UUID] = []
    if user.active_organization_id is not None:
        group_ids = await group_ids_for_user(db, user.id, user.active_organization_id)
    has_voice = await db.scalar(
        select(VoiceIdentity.id).where(VoiceIdentity.linked_user_id == user.id).limit(1)
    )
    return UserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_super_admin=user.is_super_admin,
        active_organization_id=user.active_organization_id,
        organizations=[
            OrgMembershipRead(id=org.id, name=org.name, role=role, is_instance_org=org.is_instance_org)
            for org, role in memberships
        ],
        group_ids=group_ids,
        voice_enrolled=has_voice is not None,
    )


@router.get("", response_model=list[OrganizationRead])
async def list_my_organizations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[OrganizationRead]:
    rows = (
        await db.execute(
            select(Organization, OrganizationMembership.role)
            .join(OrganizationMembership, OrganizationMembership.organization_id == Organization.id)
            .where(OrganizationMembership.user_id == current_user.id)
            .order_by(Organization.created_at)
        )
    ).all()
    return [_org_read(org, role) for org, role in rows]


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
async def create_org(
    payload: OrganizationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationRead:
    settings = get_settings()
    if not settings.allow_public_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creating organizations is disabled on this instance",
        )
    if await owned_org_count(db, current_user.id) >= settings.max_orgs_per_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You can own at most {settings.max_orgs_per_user} organization(s)",
        )
    name = payload.name.strip() or default_org_name(current_user.full_name)
    org = await create_organization(db, owner=current_user, name=name, activate=True)
    await db.commit()
    await db.refresh(org)
    return _org_read(org, OrgRole.OWNER)


@router.put("/current", response_model=OrganizationRead)
async def switch_current_org(
    payload: CurrentOrgUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationRead:
    membership = await get_membership(db, current_user.id, payload.organization_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    org = await db.get(Organization, payload.organization_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    current_user.active_organization_id = org.id
    await db.commit()
    return _org_read(org, membership.role)


@router.patch("/{org_id}", response_model=OrganizationRead)
async def rename_org(
    org_id: UUID,
    payload: OrganizationUpdate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> OrganizationRead:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    ctx.organization.name = payload.name.strip()
    await db.commit()
    await db.refresh(ctx.organization)
    return _org_read(ctx.organization, ctx.membership.role)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(
    org_id: UUID,
    ctx: AuthContext = Depends(require_org_owner),
    db: AsyncSession = Depends(get_db),
) -> None:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    if ctx.organization.is_instance_org:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot delete the default organization",
        )
    remaining = await db.scalar(select(func.count()).select_from(Organization))
    if remaining is not None and remaining <= 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot delete the last organization",
        )
    await db.delete(ctx.organization)
    await db.commit()


@router.get("/{org_id}/members", response_model=list[MemberRead])
async def list_members(
    org_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[MemberRead]:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    rows = (
        await db.execute(
            select(User, OrganizationMembership)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == org_id)
            .order_by(User.created_at)
        )
    ).all()
    out: list[MemberRead] = []
    for user, membership in rows:
        out.append(
            MemberRead(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=membership.role,
                group_ids=await group_ids_for_user(db, user.id, org_id),
                is_super_admin=user.is_super_admin,
            )
        )
    return out


@router.post("/{org_id}/members", response_model=MemberRead, status_code=status.HTTP_201_CREATED)
async def create_member(
    org_id: UUID,
    payload: MemberCreate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberRead:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    if payload.role == OrgRole.OWNER:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cannot create an owner this way")
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")
    if not get_settings().allow_public_registration:
        instance = await get_instance_org(db)
        if instance is None or instance.id != org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="New accounts join the instance organization",
            )
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        is_super_admin=False,
        active_organization_id=org_id,
    )
    db.add(user)
    await db.flush()
    await add_membership(db, user=user, organization_id=org_id, role=payload.role, activate=True)
    await db.commit()
    return MemberRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=payload.role,
        group_ids=[],
        is_super_admin=False,
    )


@router.patch("/{org_id}/members/{user_id}", response_model=MemberRead)
async def update_member(
    org_id: UUID,
    user_id: UUID,
    payload: MemberUpdate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> MemberRead:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    membership = await get_membership(db, user_id, org_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if membership.role == OrgRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot change the owner's role")
    if payload.role is not None:
        if payload.role == OrgRole.OWNER:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Transfer ownership instead")
        if not ctx.is_org_owner and membership.role == OrgRole.ADMIN and payload.role == OrgRole.MEMBER:
            # org admins may demote members they promoted; they may also
            # demote other admins — plan says they can promote/demote
            # admin↔member. OK.
            pass
        membership.role = payload.role
    await db.commit()
    user = await db.get(User, user_id)
    return MemberRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        group_ids=await group_ids_for_user(db, user.id, org_id),
        is_super_admin=user.is_super_admin,
    )


@router.delete("/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    org_id: UUID,
    user_id: UUID,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    membership = await get_membership(db, user_id, org_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if membership.role == OrgRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot remove the owner")
    user = await db.get(User, user_id)
    await db.delete(membership)
    if user is not None and user.active_organization_id == org_id:
        user.active_organization_id = None
    await db.commit()


@router.post("/{org_id}/transfer", response_model=MemberRead)
async def transfer_ownership(
    org_id: UUID,
    payload: TransferOwnership,
    ctx: AuthContext = Depends(require_org_owner),
    db: AsyncSession = Depends(get_db),
) -> MemberRead:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    target = await get_membership(db, payload.user_id, org_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    ctx.membership.role = OrgRole.ADMIN
    await db.flush()
    target.role = OrgRole.OWNER
    await db.commit()
    user = await db.get(User, payload.user_id)
    return MemberRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=OrgRole.OWNER,
        group_ids=await group_ids_for_user(db, user.id, org_id),
        is_super_admin=user.is_super_admin,
    )


@router.post("/{org_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_org(
    org_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    if ctx.is_org_owner:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Transfer ownership before leaving",
        )
    await db.delete(ctx.membership)
    ctx.user.active_organization_id = None
    await db.commit()


def _invite_read(
    invite: OrganizationInvite, token: str | None = None, email_sent: bool | None = None
) -> InviteRead:
    return InviteRead(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        token=token,
        email_sent=email_sent,
    )


async def _email_invite(
    invite: OrganizationInvite, token: str, *, org_name: str, inviter_name: str
) -> bool:
    return await send_invite_email(
        to=invite.email,
        organization_name=org_name,
        inviter_name=inviter_name,
        role=invite.role.value,
        token=token,
        expires_at=invite.expires_at,
    )


@router.get("/{org_id}/invites", response_model=list[InviteRead])
async def list_invites(
    org_id: UUID,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> list[InviteRead]:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    rows = await db.scalars(
        select(OrganizationInvite)
        .where(OrganizationInvite.organization_id == org_id, OrganizationInvite.accepted_at.is_(None))
        .order_by(OrganizationInvite.created_at.desc())
    )
    return [_invite_read(inv) for inv in rows]


@router.post("/{org_id}/invites", response_model=InviteRead, status_code=status.HTTP_201_CREATED)
async def create_invite(
    org_id: UUID,
    payload: InviteCreate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteRead:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    if payload.role == OrgRole.OWNER:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cannot invite as owner")
    already = await db.scalar(
        select(OrganizationMembership.id)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == org_id, User.email == payload.email)
    )
    if already is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That email is already a member")
    pending = await db.scalar(
        select(OrganizationInvite).where(
            OrganizationInvite.organization_id == org_id,
            OrganizationInvite.email == payload.email,
            OrganizationInvite.accepted_at.is_(None),
        )
    )
    if pending is not None:
        await db.delete(pending)
        await db.flush()
    raw, token_hash = mint_invite_token()
    invite = OrganizationInvite(
        organization_id=org_id,
        email=payload.email,
        role=payload.role,
        token_hash=token_hash,
        expires_at=invite_expiry(),
        invited_by_id=ctx.user.id,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    email_sent = await _email_invite(
        invite, raw, org_name=ctx.organization.name, inviter_name=ctx.user.full_name
    )
    return _invite_read(invite, token=raw, email_sent=email_sent)


@router.post("/{org_id}/invites/{invite_id}/resend", response_model=InviteRead)
async def resend_invite(
    org_id: UUID,
    invite_id: UUID,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteRead:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    invite = await db.get(OrganizationInvite, invite_id)
    if invite is None or invite.organization_id != org_id or invite.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    raw, token_hash = mint_invite_token()
    invite.token_hash = token_hash
    invite.expires_at = invite_expiry()
    await db.commit()
    await db.refresh(invite)
    email_sent = await _email_invite(
        invite, raw, org_name=ctx.organization.name, inviter_name=ctx.user.full_name
    )
    return _invite_read(invite, token=raw, email_sent=email_sent)


@router.delete("/{org_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    org_id: UUID,
    invite_id: UUID,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    invite = await db.get(OrganizationInvite, invite_id)
    if invite is None or invite.organization_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    await db.delete(invite)
    await db.commit()


@router.get("/{org_id}/groups", response_model=list[GroupRead])
async def list_groups(
    org_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[GroupRead]:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    result = await db.execute(
        select(Group, func.count(GroupMembership.id))
        .outerjoin(GroupMembership, GroupMembership.group_id == Group.id)
        .where(Group.organization_id == org_id)
        .group_by(Group.id)
        .order_by(Group.created_at)
    )
    return [
        GroupRead(id=group.id, name=group.name, created_at=group.created_at, member_count=count)
        for group, count in result.all()
    ]


@router.post("/{org_id}/groups", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(
    org_id: UUID,
    payload: GroupCreate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> GroupRead:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    group = Group(name=payload.name, organization_id=org_id)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return GroupRead(id=group.id, name=group.name, created_at=group.created_at, member_count=0)


@router.delete("/{org_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    org_id: UUID,
    group_id: UUID,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    group = await db.get(Group, group_id)
    if group is None or group.organization_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    await db.delete(group)
    await db.commit()


@router.put("/{org_id}/groups/{group_id}/members", status_code=status.HTTP_204_NO_CONTENT)
async def set_group_members(
    org_id: UUID,
    group_id: UUID,
    payload: GroupMemberUpdate,
    ctx: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    if ctx.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Switch to this organization first")
    group = await db.get(Group, group_id)
    if group is None or group.organization_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    existing = list(await db.scalars(select(GroupMembership).where(GroupMembership.group_id == group_id)))
    for row in existing:
        await db.delete(row)
    for user_id in payload.user_ids:
        membership = await get_membership(db, user_id, org_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="User is not in this organization")
        db.add(GroupMembership(user_id=user_id, group_id=group_id))
    await db.commit()


async def _invite_by_token(db: AsyncSession, token: str) -> OrganizationInvite:
    invite = await db.scalar(
        select(OrganizationInvite).where(OrganizationInvite.token_hash == hash_invite_token(token))
    )
    if invite is None or invite.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has expired")
    return invite


@invites_router.get("/{token}", response_model=InvitePreview)
async def preview_invite(token: str, db: AsyncSession = Depends(get_db)) -> InvitePreview:
    invite = await _invite_by_token(db, token)
    org = await db.get(Organization, invite.organization_id)
    return InvitePreview(
        organization_name=org.name if org else "Organization",
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
    )


@invites_router.post("/{token}/accept", response_model=Token)
async def accept_invite(
    token: str,
    payload: InviteAccept,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> Token:
    """Public — works even when registration is closed."""
    invite = await _invite_by_token(db, token)
    existing = await db.scalar(select(User).where(User.email == invite.email))
    if existing is None:
        if not payload.password or not payload.full_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="password and full_name are required to create an account",
            )
        user = User(
            email=invite.email,
            hashed_password=hash_password(payload.password),
            full_name=payload.full_name,
            is_super_admin=False,
            active_organization_id=invite.organization_id,
        )
        db.add(user)
        await db.flush()
        await add_membership(
            db, user=user, organization_id=invite.organization_id, role=invite.role, activate=True
        )
    else:
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists. Log in, then accept the invite.",
            )
        if current_user.email != invite.email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This invite was sent to a different email address",
            )
        if await get_membership(db, existing.id, invite.organization_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member of this organization")
        await add_membership(
            db, user=existing, organization_id=invite.organization_id, role=invite.role, activate=True
        )
        user = existing
    invite.accepted_at = datetime.now(UTC)
    await db.commit()
    return Token(access_token=create_access_token(user.id))
