import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)


async def seed_admin_user() -> None:
    """Ensure the env-configured admin account exists.

    Runs once on API startup. Only *creates* the account if ADMIN_EMAIL is
    not already taken — it never touches an existing account's password, so
    changing ADMIN_PASSWORD later and restarting won't reset it (rotate the
    password through the app instead).
    """
    settings = get_settings()
    if not settings.admin_email or not settings.admin_password:
        logger.info("ADMIN_EMAIL/ADMIN_PASSWORD not set — skipping admin bootstrap")
        return

    async with SessionLocal() as db:
        existing = await db.scalar(select(User).where(User.email == settings.admin_email))
        if existing is not None:
            return

        db.add(
            User(
                email=settings.admin_email,
                hashed_password=hash_password(settings.admin_password),
                full_name=settings.admin_full_name,
                role=UserRole.ADMIN,
            )
        )
        await db.commit()
        logger.info("Created bootstrap admin account for %s", settings.admin_email)
