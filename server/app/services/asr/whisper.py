import logging
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class WhisperWord:
    start: float  # seconds, relative to the transcribed audio
    end: float  # seconds
    word: str


@dataclass
class WhisperSegment:
    start: float  # seconds
    end: float  # seconds
    text: str
    words: list[WhisperWord]  # empty unless transcribe(word_timestamps=True)


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


def transcribe(audio_path: str, word_timestamps: bool = False) -> list[WhisperSegment]:
    """Transcribe a mono 16kHz WAV file. Loads the model once per worker
    process (module-level lazy singleton) — reloading it per task would
    dominate processing time.

    word_timestamps=True is currently unused by any caller in this codebase
    (same-room diarization no longer splits an utterance into several
    speaker-turn spans — see reconcile_diarization's own docstring) but
    kept as real API surface for a future caller that needs per-word
    timing; off by default since nothing today needs the extra decode cost.
    """
    segments, _info = _model().transcribe(audio_path, vad_filter=True, word_timestamps=word_timestamps)
    result = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        words = (
            [WhisperWord(start=w.start, end=w.end, word=w.word) for w in s.words]
            if word_timestamps and s.words
            else []
        )
        result.append(WhisperSegment(start=s.start, end=s.end, text=text, words=words))
    return result
