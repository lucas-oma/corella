from functools import lru_cache
from uuid import UUID, uuid4

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import get_settings
from app.services.embeddings.embed import EMBEDDING_DIM

# A single shared collection, scoped per-user via the `owner_id` payload
# field on each point, rather than one collection per user — simpler to
# manage and Qdrant filters on it cheaply.
KB_COLLECTION = "kb_chunks"
MEETING_COLLECTION = "meeting_chunks"
SPEAKER_COLLECTION = "speaker_embeddings"


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


def search_kb(owner_ids: list[UUID], query_embedding: list[float], top_k: int = 5) -> list[str]:
    """Top-k most relevant KB chunk texts across every id in `owner_ids` —
    just the caller themselves if ungrouped, or their whole group if they
    have one (app/services/access.py:searchable_owner_ids — that's where
    the group-sharing decision actually lives, not here). Empty if the
    collection doesn't exist yet (no documents ingested) — a normal state,
    not an error, for a user with no knowledge base.
    """
    client = _client()
    if not client.collection_exists(KB_COLLECTION) or not owner_ids:
        return []
    result = client.query_points(
        collection_name=KB_COLLECTION,
        query=query_embedding,
        query_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="owner_id", match=qmodels.MatchAny(any=[str(oid) for oid in owner_ids])
                )
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


def search_meetings(
    owner_id: UUID | None, query_embedding: list[float], top_k: int = 10
) -> list[dict]:
    """Top-k most relevant transcript chunks, across all matching meetings —
    one row per matching *chunk*, not deduplicated by meeting; the caller
    (the search API route) collapses to one (best) hit per meeting. Empty
    if the collection doesn't exist yet (no meeting has finished indexing)
    — normal, not an error.

    owner_id=None searches system-wide, with no owner filter at all — used
    only by the admin-only "All meetings" search
    (GET /api/meetings/search/all); every other caller passes the
    searching user's own id, unchanged from before this existed.
    """
    client = _client()
    if not client.collection_exists(MEETING_COLLECTION):
        return []
    query_filter = None
    if owner_id is not None:
        query_filter = qmodels.Filter(
            must=[
                qmodels.FieldCondition(key="owner_id", match=qmodels.MatchValue(value=str(owner_id)))
            ]
        )
    result = client.query_points(
        collection_name=MEETING_COLLECTION,
        query=query_embedding,
        query_filter=query_filter,
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


# --- Cross-meeting voice identity recognition (Phase O) -------------------


def ensure_speaker_collection(vector_size: int) -> None:
    """Idempotent, same 409-is-success race handling as ensure_kb_collection
    — see there for why. vector_size comes from a real call to
    embed_utterance() (pyannote/wespeaker-voxceleb-resnet34-LM), verified
    empirically rather than hardcoded — a different model/dimension than
    the text-embedding collections above.
    """
    client = _client()
    if client.collection_exists(SPEAKER_COLLECTION):
        return
    try:
        client.create_collection(
            collection_name=SPEAKER_COLLECTION,
            vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
        )
    except UnexpectedResponse as e:
        if e.status_code != 409:
            raise


def upsert_speaker_embedding(
    voice_identity_id: UUID,
    group_id: UUID | None,
    linked_user_id: UUID | None,
    embedding: list[float],
) -> None:
    """One point per VoiceIdentity — the identity's own id doubles as the
    point id (see app/models/voice_identity.py), so a re-enrollment just
    overwrites the same point rather than leaving an orphan behind.
    """
    ensure_speaker_collection(len(embedding))
    _client().upsert(
        collection_name=SPEAKER_COLLECTION,
        points=[
            qmodels.PointStruct(
                id=str(voice_identity_id),
                vector=embedding,
                payload={
                    "group_id": str(group_id) if group_id else None,
                    "linked_user_id": str(linked_user_id) if linked_user_id else None,
                },
            )
        ],
    )


def search_speaker_embeddings(
    embedding: list[float],
    score_threshold: float,
    group_id: UUID | None = None,
    linked_user_id: UUID | None = None,
    top_k: int = 1,
) -> list[dict]:
    """Best match against the durable cross-meeting voice library, scoped
    to whichever of group_id/linked_user_id is provided, combined with OR
    (`should`) rather than two separate calls — the meeting owner's own
    enrolled identity and their group's shared pool are searched in one
    pass, and cosine similarity to the *true* speaker naturally dominates
    over any other candidate, so top-1 already behaves like the intended
    "check my own identity first, then the group" priority without a
    second round-trip (see app/workers/tasks.py:diarize_utterance).
    Empty if the collection doesn't exist yet (nobody's ever enrolled or
    been recognized) — normal, not an error.
    """
    client = _client()
    if not client.collection_exists(SPEAKER_COLLECTION):
        return []
    conditions = []
    if group_id is not None:
        conditions.append(
            qmodels.FieldCondition(key="group_id", match=qmodels.MatchValue(value=str(group_id)))
        )
    if linked_user_id is not None:
        conditions.append(
            qmodels.FieldCondition(
                key="linked_user_id", match=qmodels.MatchValue(value=str(linked_user_id))
            )
        )
    if not conditions:
        return []
    result = client.query_points(
        collection_name=SPEAKER_COLLECTION,
        query=embedding,
        query_filter=qmodels.Filter(should=conditions),
        limit=top_k,
        score_threshold=score_threshold,
    )
    return [{"voice_identity_id": point.id, "score": point.score} for point in result.points]


def delete_speaker_embedding(voice_identity_id: UUID) -> None:
    """Best-effort — a not-yet-created collection (nobody's ever enrolled)
    is a no-op, not an error."""
    client = _client()
    if not client.collection_exists(SPEAKER_COLLECTION):
        return
    client.delete(
        collection_name=SPEAKER_COLLECTION,
        points_selector=qmodels.PointIdsList(points=[str(voice_identity_id)]),
    )
