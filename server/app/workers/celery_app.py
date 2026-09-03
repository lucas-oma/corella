import logging

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import get_settings

logger = logging.getLogger(__name__)
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


@worker_process_init.connect
def _prewarm_models(**_kwargs) -> None:
    """Eagerly loads every heavy model this worker process will eventually
    need, once, right after Celery forks it — rather than leaving each
    @lru_cache singleton (pyannote.py, embedding.py, whisper.py) to cold-load
    on whichever task happens to hit it first. A live diarize_utterance call
    landing on a cold process was a real, measured multi-second latency
    source; this moves that cost to worker startup, off the critical path of
    any real task.

    Imported lazily (not at module level) for the same reason celery_app
    itself defers app.workers.tasks — this module is also imported by the
    lightweight api image, which must never pull in torch/pyannote/whisper.
    """
    from app.services.asr.whisper import warm_up as warm_up_whisper
    from app.services.diarization.embedding import _inference as warm_up_embedding
    from app.services.diarization.pyannote import DiarizationUnavailable
    from app.services.diarization.pyannote import _pipeline as warm_up_diarization

    warm_up_whisper()

    # Both gated on HF_TOKEN — an instance without diarization configured at
    # all should start cleanly, not crash-loop every worker process.
    try:
        warm_up_diarization()
        warm_up_embedding()
    except DiarizationUnavailable as e:
        logger.info("Skipping diarization model pre-warm: %s", e)
    except Exception:
        logger.exception("Diarization model pre-warm failed — will retry lazily on first real task")
