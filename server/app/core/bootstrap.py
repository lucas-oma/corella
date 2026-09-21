import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.organization import OrganizationMembership, OrgRole
from app.models.user import User
from app.services.organizations import (
    add_membership,
    create_organization,
    default_org_name,
    get_instance_org,
    get_membership,
)

logger = logging.getLogger(__name__)


async def seed_admin_user() -> None:
    """Ensure the env-configured super-admin account exists, and is owner
    of the instance org (created here if migration 0023 left an empty one,
    or created fresh on a brand-new database).

    Only *creates* the account if ADMIN_EMAIL is not already taken — it
    never touches an existing account's password, so changing ADMIN_PASSWORD
    later and restarting won't reset it.
    """
    settings = get_settings()
    if not settings.admin_email or not settings.admin_password:
        logger.info("ADMIN_EMAIL/ADMIN_PASSWORD not set — skipping admin bootstrap")
        return

    async with SessionLocal() as db:
        existing = await db.scalar(select(User).where(User.email == settings.admin_email))
        if existing is None:
            user = User(
                email=settings.admin_email,
                hashed_password=hash_password(settings.admin_password),
                full_name=settings.admin_full_name,
                is_super_admin=True,
            )
            db.add(user)
            await db.flush()
            logger.info("Created bootstrap super-admin account for %s", settings.admin_email)
        else:
            user = existing
            if not user.is_super_admin:
                user.is_super_admin = True

        instance = await get_instance_org(db)
        if instance is None:
            await create_organization(
                db,
                owner=user,
                name=default_org_name(user.full_name),
                is_instance_org=True,
            )
        else:
            membership = await get_membership(db, user.id, instance.id)
            if membership is None:
                owner = await db.scalar(
                    select(OrganizationMembership).where(
                        OrganizationMembership.organization_id == instance.id,
                        OrganizationMembership.role == OrgRole.OWNER,
                    )
                )
                role = OrgRole.OWNER if owner is None else OrgRole.ADMIN
                await add_membership(
                    db, user=user, organization_id=instance.id, role=role, activate=True
                )
            elif user.active_organization_id is None:
                user.active_organization_id = instance.id

        await db.commit()
