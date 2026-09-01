import logging
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class DiarizationTurn:
    start: float  # seconds
    end: float  # seconds
    speaker: str  # pipeline-local label, e.g. "SPEAKER_00"


class DiarizationUnavailable(Exception):
    """HF_TOKEN isn't configured, or the gated pipeline couldn't load.
    Callers should treat this as "skip diarization for this meeting", not a
    reason to fail the whole transcription job.
    """


@lru_cache
def _pipeline():
    settings = get_settings()
    if not settings.hf_token:
        raise DiarizationUnavailable(
            "HF_TOKEN is not configured — accept the pyannote/speaker-diarization-3.1 "
            "terms on Hugging Face and set HF_TOKEN to enable diarization"
        )

    from pyannote.audio import Pipeline  # heavy import — deferred to first use

    logger.info("Loading pyannote/speaker-diarization-3.1 pipeline")
    return Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1", use_auth_token=settings.hf_token
    )


def diarize(audio_path: str) -> list[DiarizationTurn]:
    """Run speaker diarization on a mono 16kHz WAV file. Loads the pipeline
    once per worker process (module-level lazy singleton).
    """
    pipeline = _pipeline()
    annotation = pipeline(audio_path)
    return [
        DiarizationTurn(start=turn.start, end=turn.end, speaker=speaker)
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]
