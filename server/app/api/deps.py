from datetime import UTC, datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import API_KEY_PREFIX, decode_access_token, hash_api_key
from app.models.api_key import ApiKey
from app.models.user import User, UserRole

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


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


async def get_current_user_flexible(
    token: str = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Same bearer-token extraction as get_current_user, but also accepts
    an API key (app/models/api_key.py) in place of a JWT — for the
    handful of routes an external/machine caller actually needs (create
    meeting, list call types, get meeting/transcript/insights/report;
    see app/api/meetings.py and app/api/call_types.py). Every other
    route keeps using get_current_user
    unchanged, so this widened acceptance is scoped to exactly where it's
    needed, not global.

    Disambiguated by prefix (app.core.security.API_KEY_PREFIX) rather
    than attempting-and-failing a JWT decode first — cheap, and avoids
    ever treating a malformed JWT as "maybe an API key."
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
        api_key.last_used_at = datetime.now(UTC)
        await db.commit()
        return user

    return await get_current_user(token, db)


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return current_user
