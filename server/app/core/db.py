from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


def _async_database_url() -> str:
    """Coerce the configured sync-style URL into the asyncpg driver."""
    settings = get_settings()
    url = settings.database_url
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


engine = create_async_engine(_async_database_url(), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


# Celery tasks run synchronously (no event loop), so the worker gets its own
# plain sync engine/session rather than reusing the async one above. Points
# at the same database via the sync psycopg driver `database_url` is already
# in (no URL rewriting needed, unlike the async engine).
sync_engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False)


@contextmanager
def get_sync_db() -> Iterator[Session]:
    with SyncSessionLocal() as session:
        yield session
