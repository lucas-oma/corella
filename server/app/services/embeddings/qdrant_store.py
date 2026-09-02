from functools import lru_cache
from uuid import UUID, uuid4

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http import models as qmodels

from app.core.config import get_settings
from app.services.embeddings.embed import EMBEDDING_DIM

# A single shared collection, scoped per-user via the `owner_id` payload
# field on each point, rather than one collection per user — simpler to
# manage and Qdrant filters on it cheaply.
KB_COLLECTION = "kb_chunks"
MEETING_COLLECTION = "meeting_chunks"


@lru_cache
def _client() -> QdrantClient:
    return QdrantClient(url=get_settings().qdrant_url)


def ensure_kb_collection() -> None:
    """Idempotent, and safe under concurrent callers: multiple Celery
    workers can race the exists-check/create here (two documents processed
    at once), so a 409 from `create_collection` — someone else won the
    race in the gap between our check and our create — is treated as
    success rather than propagated as a task failure.
    """
    client = _client()
    if client.collection_exists(KB_COLLECTION):
        return
    try:
        client.create_collection(
            collection_name=KB_COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=EMBEDDING_DIM, distance=qmodels.Distance.COSINE
            ),
        )
    except UnexpectedResponse as e:
        if e.status_code != 409:
            raise


def upsert_chunks(
    document_id: UUID, owner_id: UUID, chunks: list[str], embeddings: list[list[float]]
) -> None:
    ensure_kb_collection()
    points = [
        qmodels.PointStruct(
            id=str(uuid4()),
            vector=embedding,
            payload={
                "owner_id": str(owner_id),
                "document_id": str(document_id),
                "chunk_index": i,
                "text": chunk,
            },
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True))
    ]
    _client().upsert(collection_name=KB_COLLECTION, points=points)


def search_kb(owner_id: UUID, query_embedding: list[float], top_k: int = 5) -> list[str]:
    """Top-k most relevant KB chunk texts for this user. Empty if the
    collection doesn't exist yet (no documents ingested) — a normal state,
    not an error, for a user with no knowledge base.
    """
    client = _client()
    if not client.collection_exists(KB_COLLECTION):
        return []
    result = client.query_points(
        collection_name=KB_COLLECTION,
        query=query_embedding,
        query_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(key="owner_id", match=qmodels.MatchValue(value=str(owner_id)))
            ]
        ),
        limit=top_k,
    )
    return [point.payload["text"] for point in result.points if point.payload]


def delete_document_chunks(document_id: UUID) -> None:
    """Best-effort — a document with no chunks yet (never processed, or the
    collection doesn't exist yet) is a no-op, not an error.
    """
    client = _client()
    if not client.collection_exists(KB_COLLECTION):
        return
    client.delete(
        collection_name=KB_COLLECTION,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="document_id", match=qmodels.MatchValue(value=str(document_id))
                    )
                ]
            )
        ),
    )


# --- Meeting transcript search (semantic search on the Dashboard) --------


def ensure_meeting_collection() -> None:
    """Same idempotent-under-races shape as ensure_kb_collection() — see
    there for why a 409 here is treated as success, not a failure."""
    client = _client()
    if client.collection_exists(MEETING_COLLECTION):
        return
    try:
        client.create_collection(
            collection_name=MEETING_COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=EMBEDDING_DIM, distance=qmodels.Distance.COSINE
            ),
        )
    except UnexpectedResponse as e:
        if e.status_code != 409:
            raise


def upsert_meeting_chunks(
    meeting_id: UUID,
    owner_id: UUID,
    chunks: list[tuple[str, int, int]],
    embeddings: list[list[float]],
) -> None:
    """`chunks` is (text, start_ms, end_ms) — start_ms is what lets a search
    result deep-link straight to the moment it was said, not just the
    meeting as a whole."""
    ensure_meeting_collection()
    points = [
        qmodels.PointStruct(
            id=str(uuid4()),
            vector=embedding,
            payload={
                "owner_id": str(owner_id),
                "meeting_id": str(meeting_id),
                "chunk_index": i,
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
            },
        )
        for i, ((text, start_ms, end_ms), embedding) in enumerate(zip(chunks, embeddings, strict=True))
    ]
    _client().upsert(collection_name=MEETING_COLLECTION, points=points)


# Verified empirically against a real transcript: a genuinely matching
# query scored 0.47 cosine similarity, a completely unrelated one scored
# 0.015 — a wide gap. Without a floor, a search with no real match in the
# user's meetings still returns *something* (whatever's least-bad), which
# reads as a false positive; 0.2 sits comfortably in the gap.
_MIN_RELEVANCE_SCORE = 0.2


def search_meetings(owner_id: UUID, query_embedding: list[float], top_k: int = 10) -> list[dict]:
    """Top-k most relevant transcript chunks for this user, across all their
    meetings — one row per matching *chunk*, not deduplicated by meeting;
    the caller (the search API route) collapses to one (best) hit per
    meeting. Empty if the collection doesn't exist yet (no meeting has
    finished indexing) — normal, not an error.
    """
    client = _client()
    if not client.collection_exists(MEETING_COLLECTION):
        return []
    result = client.query_points(
        collection_name=MEETING_COLLECTION,
        query=query_embedding,
        query_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(key="owner_id", match=qmodels.MatchValue(value=str(owner_id)))
            ]
        ),
        limit=top_k,
        score_threshold=_MIN_RELEVANCE_SCORE,
    )
    return [
        {
            "meeting_id": point.payload["meeting_id"],
            "text": point.payload["text"],
            "start_ms": point.payload["start_ms"],
            "score": point.score,
        }
        for point in result.points
        if point.payload
    ]


def delete_meeting_chunks(meeting_id: UUID) -> None:
    """Best-effort — a meeting with no chunks yet (no transcript, never
    indexed, or the collection doesn't exist yet) is a no-op, not an error.
    """
    client = _client()
    if not client.collection_exists(MEETING_COLLECTION):
        return
    client.delete(
        collection_name=MEETING_COLLECTION,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="meeting_id", match=qmodels.MatchValue(value=str(meeting_id))
                    )
                ]
            )
        ),
    )
