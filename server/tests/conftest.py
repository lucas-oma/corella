"""Shared pytest fixtures. The DATABASE_URL override below MUST run before
any `app.*` import anywhere in this file (or transitively) — app.core.db
creates its module-level async/sync engines from Settings.database_url at
IMPORT time, and Settings itself is @lru_cache'd, so whichever value is set
first wins for the whole test process.

Tests run against a REAL Postgres database, not SQLite — this schema uses
Postgres-native enums and arrays that SQLite can't represent faithfully, and
this project's own standing discipline (see CONTRIBUTING.md) is to verify
against real infrastructure, not a convenient stand-in that would mask the
exact kind of bug a fake backend can't reproduce.

Every fixture below builds its own fresh, NullPool engine per test rather
than reusing app.core.db's shared module-level engine/SessionLocal — found
necessary empirically, not by design upfront: reusing the shared pool hit
the exact "attached to a different loop" bug this project's own production
code has already hit and fixed once (app/workers/tasks.py's
_with_engine_cleanup), because pytest-asyncio and httpx's ASGITransport
don't guarantee every fixture and request lands on the identical event
loop. A fresh, disposed-every-test NullPool engine sidesteps the whole
class of bug rather than fighting event-loop-scope configuration.
"""

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://corella:corella@localhost:5432/corella_test",
)
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")

from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402
from uuid import UUID  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.core.db import _async_database_url, get_db  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# Every table this schema defines, in FK-safe TRUNCATE order (CASCADE makes
# the ordering mostly moot, but listed newest-first-added for readability).
_TABLES = [
    "action_items",
    "transcript_segments",
    "speakers",
    "notes",
    "meetings",
    "call_types",
    "voice_identities",
    "llm_usage_events",
    "stt_credentials",
    "provider_credentials",
    "kb_documents",
    "call_profiles",
    "users",
    "groups",
]


@pytest.fixture(scope="session", autouse=True)
def _migrate_test_database():
    """Runs the real Alembic migration chain against the test database once
    per test session — this is also, incidentally, a standing regression
    check that `alembic upgrade head` actually succeeds from empty."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_ALEMBIC_INI.parent / "alembic"))
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def _engine() -> AsyncIterator[AsyncEngine]:
    """A brand-new engine per test, NullPool (no connection reuse across
    checkouts at all) — see the module docstring for why. Every other
    fixture below is built on this one so a single test's DB fixture, its
    factory-created rows, and its HTTP requests all share one consistent,
    disposed-at-teardown engine."""
    test_engine = create_async_engine(_async_database_url(), poolclass=NullPool)
    yield test_engine
    async with test_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {', '.join(_TABLES)} CASCADE"))
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def app_client(_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """The real FastAPI app, real routing/dependency layer, real HTTP calls
    — via ASGITransport rather than a live server process. get_db is
    overridden (FastAPI's own recommended testing pattern) to this test's
    dedicated engine instead of the app's shared module-level one."""
    session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def make_user(db: AsyncSession):
    """Factory: make_user(email=..., role=..., group_id=...) -> User, with a
    known password ("testpass123") so callers that need to log in via the
    real API can. Returns the persisted User with owner/call_type-style
    relationships available (not needed here, but consistent)."""

    async def _make(
        email: str = "user@example.com",
        password: str = "testpass123",
        full_name: str = "Test User",
        role: UserRole = UserRole.MEMBER,
        group_id: UUID | None = None,
    ) -> User:
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=role,
            group_id=group_id,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    return _make


@pytest.fixture
def auth_headers():
    """auth_headers(user) -> {"Authorization": "Bearer ..."} via a real JWT,
    the same helper create_access_token every real login already uses —
    not a fake/short-circuited auth path."""

    def _headers(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user.id)}"}

    return _headers
