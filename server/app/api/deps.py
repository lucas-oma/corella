from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import API_KEY_PREFIX, decode_access_token, hash_api_key
from app.models.api_key import ApiKey
from app.models.organization import Organization, OrganizationMembership, OrgRole
from app.models.user import User
from app.services.organizations import OrgActor, resolve_active_membership

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
_optional_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


@dataclass
class AuthContext:
    """The signed-in user plus the organization this request runs in.

    JWT requests use users.active_organization_id (the switcher). API-key
    requests use the key's bound organization, ignoring the switcher.
    """

    user: User
    organization: Organization
    membership: OrganizationMembership
    api_key: ApiKey | None = None

    @property
    def org_id(self) -> UUID:
        return self.organization.id

    @property
    def is_super_admin(self) -> bool:
        return self.user.is_super_admin

    @property
    def is_org_owner(self) -> bool:
        return self.membership.role == OrgRole.OWNER

    @property
    def is_org_admin(self) -> bool:
        return self.membership.role in (OrgRole.OWNER, OrgRole.ADMIN)

    def as_actor(self) -> OrgActor:
        return OrgActor(user=self.user, organization=self.organization, membership=self.membership)


async def get_optional_user(
    token: str | None = Depends(_optional_oauth2),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if not token:
        return None
    user_id = decode_access_token(token)
    if user_id is None:
        return None
    return await db.get(User, user_id)


async def get_current_user(
    token: str = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_id = decode_access_token(token)
    if user_id is None:
        raise credentials_error

    user = await db.get(User, user_id)
    if user is None:
        raise credentials_error

    return user


async def get_auth_context(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    resolved = await resolve_active_membership(db, current_user)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of any organization",
        )
    org, membership = resolved
    await db.commit()
    return AuthContext(user=current_user, organization=org, membership=membership)


async def get_auth_context_flexible(
    token: str = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """JWT or API key. An API key always runs in the org it was minted
    for, even if its owner has since switched the web UI to another org.
    """
    if token.startswith(API_KEY_PREFIX):
        credentials_error = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
        api_key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(token)))
        if api_key is None:
            raise credentials_error
        user = await db.get(User, api_key.owner_id)
        if user is None:
            raise credentials_error
        org = await db.get(Organization, api_key.organization_id)
        if org is None:
            raise credentials_error
        membership = await db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == org.id,
            )
        )
        if membership is None:
            raise credentials_error
        api_key.last_used_at = datetime.now(UTC)
        await db.commit()
        return AuthContext(user=user, organization=org, membership=membership, api_key=api_key)

    user = await get_current_user(token, db)
    return await get_auth_context(user, db)


async def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super-admin privileges required"
        )
    return current_user


async def require_org_admin(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not ctx.is_org_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organization admin privileges required"
        )
    return ctx


async def require_org_owner(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not ctx.is_org_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organization owner privileges required"
        )
    return ctx
