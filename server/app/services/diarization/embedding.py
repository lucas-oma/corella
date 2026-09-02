import logging
import os
import tempfile
from functools import lru_cache

import numpy as np

from app.core.config import get_settings
from app.services.audio.mixing import write_wav

logger = logging.getLogger(__name__)

# Not the same model diarize() uses. diarize() is pyannote/speaker-diarization-3.1,
# a whole-file batch Pipeline with no notion of "here's one more utterance" —
# unusable for live, incremental labeling. This is a standalone speaker-
# embedding model instead: one fixed-size vector per utterance, compared
# against other speakers already seen so far in the same call (see
# app/services/diarization/cluster.py). Also, unlike speaker-diarization-3.1
# and its segmentation-3.0 dependency, this specific model is NOT gated on
# Hugging Face — verified empirically (loads and runs with token=None) — so
# this capability works independent of whether HF_TOKEN's account has
# accepted pyannote's gated-model terms.
_EMBEDDING_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"


@lru_cache
def _inference():
    from pyannote.audio import Inference, Model  # heavy import — deferred to first use

    settings = get_settings()
    logger.info("Loading %s", _EMBEDDING_MODEL)
    model = Model.from_pretrained(_EMBEDDING_MODEL, token=settings.hf_token)
    return Inference(model, window="whole")


def embed_utterance(pcm: bytes) -> np.ndarray:
    """A fixed-size speaker-embedding vector for one utterance's raw
    PCM16LE 16kHz mono audio. Loads the model once per worker process
    (module-level lazy singleton, same pattern as diarization/pyannote.py).
    """
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        write_wav(path, pcm)
        embedding = _inference()(path)
        return np.asarray(embedding).flatten()
    finally:
        os.unlink(path)
