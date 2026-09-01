from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "corella",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])


@celery_app.task(name="corella.ping")
def ping() -> str:
    """Trivial task so the worker has something registered while the real
    transcription/diarization/embedding tasks (Phase B onward) land.
    """
    return "pong"
