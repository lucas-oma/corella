import logging
from functools import lru_cache

from app.core.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # matches the default model (all-MiniLM-L6-v2)


@lru_cache
def _model():
    from sentence_transformers import SentenceTransformer  # heavy import — deferred to first use

    settings = get_settings()
    logger.info("Loading embedding model=%s", settings.embedding_model)
    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of chunks. Loads the model once per worker process
    (module-level lazy singleton) — reloading it per document would
    dominate processing time.
    """
    if not texts:
        return []
    return _model().encode(texts, convert_to_numpy=True).tolist()
