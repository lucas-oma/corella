import logging
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class WhisperSegment:
    start: float  # seconds
    end: float  # seconds
    text: str


@lru_cache
def _model():
    from faster_whisper import WhisperModel  # heavy import — deferred to first use

    settings = get_settings()
    logger.info(
        "Loading faster-whisper model=%s compute_type=%s",
        settings.whisper_model,
        settings.whisper_compute_type,
    )
    return WhisperModel(settings.whisper_model, compute_type=settings.whisper_compute_type)


def warm_up() -> None:
    """Force the lazy model singleton to load now rather than on first use —
    called right after a live session's WS auth succeeds so the first
    utterance isn't stuck behind several seconds of model-load latency.
    """
    _model()


def transcribe(audio_path: str) -> list[WhisperSegment]:
    """Transcribe a mono 16kHz WAV file. Loads the model once per worker
    process (module-level lazy singleton) — reloading it per task would
    dominate processing time.
    """
    segments, _info = _model().transcribe(audio_path, vad_filter=True)
    return [
        WhisperSegment(start=s.start, end=s.end, text=s.text.strip())
        for s in segments
        if s.text.strip()
    ]
