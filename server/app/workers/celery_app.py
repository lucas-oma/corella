from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "corella",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Registered lazily (not imported here) so this module — imported by the
    # lightweight API image too, for `send_task` — never pulls in
    # faster-whisper/pyannote/torch. Only the actual worker process imports it.
    include=["app.workers.tasks"],
)
celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])


@celery_app.task(name="corella.ping")
def ping() -> str:
    """Trivial connectivity check, independent of the heavier ASR/diarization
    tasks in app.workers.tasks.
    """
    return "pong"
