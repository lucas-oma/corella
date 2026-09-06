import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, call_types, kb, meetings
from app.api import settings as settings_api
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
logger = logging.getLogger(__name__)


async def _warm_up_embedding_model() -> None:
    """Phase W1: the live in-process instant-recognition check
    (app/ws/live_session.py's quick-label-hint replacement) needs the
    speaker-embedding model loaded in *this* process now too, not just the
    worker's — pre-warm it at startup, same spirit as celery_app.py's own
    worker_process_init hook, so the first real live utterance in a
    freshly-started api process doesn't pay the multi-second cold-load
    penalty. Runs off the event loop (model loading is blocking, CPU-bound
    work) and is best-effort: a failure here just means the cost is paid
    lazily on the first real call instead, not a startup crash — this
    model isn't HF-gated, so failure here would be a real, unexpected
    problem worth logging loudly, not a "HF_TOKEN not configured" case
    (that's `_pipeline`/the full diarize() pipeline, still worker-only).
    """
    try:
        from app.services.diarization.embedding import _inference as warm_up_embedding

        await asyncio.get_running_loop().run_in_executor(None, warm_up_embedding)
    except Exception:
        logger.exception("Embedding model pre-warm failed at api startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await seed_admin_user()
    await _warm_up_embedding_model()
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
