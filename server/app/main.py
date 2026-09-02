import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, call_types, kb, meetings, settings as settings_api
from app.core.bootstrap import seed_admin_user
from app.core.config import get_settings
from app.ws import live_session

# Without this, the root logger defaults to WARNING with no handler at all —
# every logger.info()/logger.exception() call in the app (background task
# failures, skipped copilot cycles, etc.) silently vanishes rather than
# reaching stdout/docker logs.
logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await seed_admin_user()
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(call_types.router)
    app.include_router(meetings.router)
    app.include_router(kb.router)
    app.include_router(settings_api.router)
    app.include_router(live_session.router)

    @app.get("/api/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
