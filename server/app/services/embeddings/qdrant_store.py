import logging
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
logger = logging.getLogger(__name__)

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
    document_id: UUID,
    owner_id: UUID,
    chunks: list[str],
    embeddings: list[list[float]],
    group_id: UUID | None = None,
    organization_id: UUID | None = None,
) -> None:
    ensure_kb_collection()
    points = [
        qmodels.PointStruct(
            id=str(uuid4()),
            vector=embedding,
            payload={
                "owner_id": str(owner_id),
                "group_id": str(group_id) if group_id else None,
                "organization_id": str(organization_id) if organization_id else None,
                "document_id": str(document_id),
                "chunk_index": i,
                "text": chunk,
            },
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True))
    ]
    _client().upsert(collection_name=KB_COLLECTION, points=points)


def search_kb(
    owner_ids: list[UUID],
    query_embedding: list[float],
    top_k: int = 5,
    group_id: UUID | None = None,
    group_ids: list[UUID] | None = None,
    organization_id: UUID | None = None,
) -> list[str]:
    """Top-k most relevant KB chunk texts. Always org-scoped when
    organization_id is provided. Matches a payload `group_id` in the
    caller's groups *or* a legacy `owner_id` in `owner_ids`.
    """
    if organization_id is None:
        return []
    client = _client()
    if not client.collection_exists(KB_COLLECTION):
        return []
    must: list[qmodels.FieldCondition] = [
        qmodels.FieldCondition(
            key="organization_id", match=qmodels.MatchValue(value=str(organization_id))
        )
    ]
    should: list[qmodels.FieldCondition] = []
    ids = list(group_ids or [])
    if group_id is not None:
        ids.append(group_id)
    if ids:
        should.append(
            qmodels.FieldCondition(
                key="group_id", match=qmodels.MatchAny(any=[str(gid) for gid in ids])
            )
        )
    if owner_ids:
        should.append(
            qmodels.FieldCondition(
                key="owner_id", match=qmodels.MatchAny(any=[str(oid) for oid in owner_ids])
            )
        )
    if not should:
        return []
    result = client.query_points(
        collection_name=KB_COLLECTION,
        query=query_embedding,
        query_filter=qmodels.Filter(must=must or None, should=should or None),
        limit=top_k,
    )
    return [point.payload["text"] for point in result.points if point.payload]


def delete_document_chunks(document_id: UUID) -> None:
    """Best-effort — a document with no chunks yet (never processed, the
    collection doesn't exist, or Qdrant is unreachable) is a no-op, not
    an error. The Postgres row is already gone by the time this runs.
    """
    try:
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
    except Exception:
        logger.exception("delete_document_chunks: Qdrant cleanup failed for %s", document_id)


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
    organization_id: UUID | None = None,
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
                "organization_id": str(organization_id) if organization_id else None,
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
    owner_id: UUID | None,
    query_embedding: list[float],
    top_k: int = 10,
    organization_id: UUID | None = None,
    unscoped: bool = False,
) -> list[dict]:
    """Top-k most relevant transcript chunks. `unscoped=True` is super-admin
    instance-wide search only. Every other caller must pass organization_id
    so a missing org payload never matches every tenant.
    """
    client = _client()
    if not client.collection_exists(MEETING_COLLECTION):
        return []
    must: list[qmodels.FieldCondition] = []
    if not unscoped:
        if organization_id is None:
            return []
        must.append(
            qmodels.FieldCondition(
                key="organization_id", match=qmodels.MatchValue(value=str(organization_id))
            )
        )
    if owner_id is not None:
        must.append(
            qmodels.FieldCondition(key="owner_id", match=qmodels.MatchValue(value=str(owner_id)))
        )
    query_filter = qmodels.Filter(must=must) if must else None
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
    organization_id: UUID | None = None,
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
                    "organization_id": str(organization_id) if organization_id else None,
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
    organization_id: UUID | None = None,
    top_k: int = 1,
) -> list[dict]:
    """Best match against the durable cross-meeting voice library, scoped
    to the organization (and optionally the owner's own enrolled identity).
    """
    if organization_id is None:
        return []
    client = _client()
    if not client.collection_exists(SPEAKER_COLLECTION):
        return []
    must: list[qmodels.FieldCondition] = [
        qmodels.FieldCondition(
            key="organization_id", match=qmodels.MatchValue(value=str(organization_id))
        )
    ]
    should: list[qmodels.FieldCondition] = []
    if linked_user_id is not None:
        should.append(
            qmodels.FieldCondition(
                key="linked_user_id", match=qmodels.MatchValue(value=str(linked_user_id))
            )
        )
    if not must and not should:
        return []
    result = client.query_points(
        collection_name=SPEAKER_COLLECTION,
        query=embedding,
        query_filter=qmodels.Filter(must=must or None, should=should or None),
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


def backfill_missing_organization_id(organization_id: UUID) -> None:
    """Stamp organization_id onto points that predate the org model.

    Searches already require a matching organization_id, so missing
    payloads never leak across tenants; this just recovers recall for
    existing installs. No-op when Qdrant is empty or unreachable.
    """
    oid = str(organization_id)
    client = _client()
    for collection in (KB_COLLECTION, MEETING_COLLECTION, SPEAKER_COLLECTION):
        if not client.collection_exists(collection):
            continue
        offset = None
        while True:
            records, offset = client.scroll(
                collection_name=collection,
                limit=128,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            ids = [row.id for row in records if not (row.payload or {}).get("organization_id")]
            if ids:
                client.set_payload(
                    collection_name=collection,
                    payload={"organization_id": oid},
                    points=ids,
                )
            if offset is None:
                break
