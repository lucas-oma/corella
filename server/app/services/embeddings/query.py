import asyncio
from functools import lru_cache

from app.core.config import get_settings

# Verified empirically (Phase E) against the worker's sentence-transformers
# path for the same model: cosine similarity ≈ 1.000000 on identical input.
# Deliberately the *same* model as embed.py's worker-side ingestion model —
# a query embedded in a different vector space than the KB chunks would
# make similarity search meaningless.


@lru_cache
def _model():
    from fastembed import TextEmbedding  # ONNX-based, no torch — safe for the api image

    return TextEmbedding(model_name=get_settings().embedding_model)


def _embed(text: str) -> list[float]:
    return next(iter(_model().embed([text]))).tolist()


async def embed_query(text: str) -> list[float]:
    """Runs in a thread — fastembed's ONNX inference is a blocking call,
    same rationale as whisper transcription's executor dispatch.
    """
    return await asyncio.get_running_loop().run_in_executor(None, _embed, text)
