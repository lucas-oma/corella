import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from uuid import UUID

import numpy as np
import redis

from app.core.config import get_settings
from app.models.meeting import Channel

# Verified against pyannote/wespeaker-voxceleb-resnet34-LM using real
# recorded conversation (not synthetic TTS, which this model doesn't
# discriminate at all — cross-speaker pairs scored *higher* than
# same-speaker ones on that test data, a false positive in the wrong
# direction). On real speech, same-speaker similarity clustered at
# 0.67-0.75 and different-speaker at 0.01-0.14 — a wide, clean gap this
# threshold sits comfortably in the middle of.
SIMILARITY_THRESHOLD = 0.55

# Purely a scratch pad for the live clustering computation, not a source of
# truth (Speaker/TranscriptSegment rows in Postgres are) — bounded so an
# abandoned or crashed session doesn't linger in Redis forever.
STATE_TTL_SECONDS = 6 * 60 * 60

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


@dataclass
class Cluster:
    centroid: list[float]
    count: int
    speaker_id: str  # Speaker.id, as a str — every cluster gets one immediately on creation


@dataclass
class PendingEmbedding:
    """A whole-utterance embedding that didn't confidently match any
    existing cluster but wasn't trusted enough to mint a brand-new one from
    on its own either (app/workers/tasks.py:diarize_utterance's defer path)
    — held here, per meeting+channel, until either a later cluster update
    makes it match (diarization_backfill_similarity_threshold) or another
    pending embedding mutually corroborates it (see
    try_promote_mutual_pair) enough to jointly seed a real new cluster.
    Segment stays unlabeled in Postgres for as long as this stays pending —
    same accepted trade-off as insufficient_signal's own unresolved
    segments, MeetingDetail.tsx already degrades those gracefully.
    """

    segment_id: str  # TranscriptSegment.id, as a str
    embedding: list[float]


def _state_key(meeting_id: UUID, channel: Channel) -> str:
    return f"diar:{meeting_id}:{channel.value}"


def _pending_key(meeting_id: UUID, channel: Channel) -> str:
    return f"diar-pending:{meeting_id}:{channel.value}"


def peek_clusters(meeting_id: UUID, channel: Channel) -> list[Cluster]:
    """Lock-free read of the current cluster state — used only to make a
    fast, best-effort confidence decision about whether the expensive
    diarize() pass is even worth running for a new utterance
    (app/workers/tasks.py:diarize_utterance's skip-check). A small race here
    against a concurrent update only ever costs a possibly-one-utterance-
    stale confidence read, never a wrong final assignment — the actual
    cluster write always still goes through locked_state below.
    """
    raw = _redis().get(_state_key(meeting_id, channel))
    return [Cluster(**c) for c in json.loads(raw)] if raw else []


@contextmanager
def locked_state(meeting_id: UUID, channel: Channel):
    """Per-meeting-per-channel lock around one read-decide-write cycle of
    online speaker clustering — without it, two utterances dispatched close
    together could both see no matching cluster and both decide "new
    speaker" for what's actually the same one. Scoped by channel (not just
    meeting) so a "Me" voice and a "Them" voice never get clustered against
    each other's centroids — Me and Them are unrelated pools of people, one
    a local mic capture, the other a shared tab/system-audio track. Yields
    a mutable (list[Cluster], list[PendingEmbedding]) pair; mutate either in
    place, both are saved back to Redis together when the block exits — the
    same lock covers both because a single utterance's decision can touch
    both at once (e.g. resolving a pending embedding also updates a
    cluster's centroid), and they'd drift out of sync under separate locks.
    """
    r = _redis()
    key = _state_key(meeting_id, channel)
    pending_key = _pending_key(meeting_id, channel)
    with r.lock(f"diar-lock:{meeting_id}:{channel.value}", timeout=10):
        raw = r.get(key)
        clusters = [Cluster(**c) for c in json.loads(raw)] if raw else []
        pending_raw = r.get(pending_key)
        pending = [PendingEmbedding(**p) for p in json.loads(pending_raw)] if pending_raw else []
        yield clusters, pending
        r.set(key, json.dumps([asdict(c) for c in clusters]), ex=STATE_TTL_SECONDS)
        r.set(pending_key, json.dumps([asdict(p) for p in pending]), ex=STATE_TTL_SECONDS)


def try_promote_mutual_pair(pending: list[PendingEmbedding]) -> tuple[int, int] | None:
    """The first pair of still-pending embeddings that mutually agree with
    each other at SIMILARITY_THRESHOLD — real corroborating evidence from
    two independent utterances, not one weak score trusted alone. Returns
    their (index, index) into `pending`, or None.

    Why this exists at all: without it, once a channel's first cluster is
    created, no utterance that fails to confidently match it can ever
    become a new cluster on its own (it would just defer forever) — a real,
    genuinely different second speaker would never get their own identity,
    silently absorbed into "unresolved" or the wrong cluster instead.
    Verified live against real 2-speaker ground-truth audio: this alone
    (deferring but never promoting) collapsed a real 2-speaker file down to
    1 cluster, erasing the second speaker entirely — worse than the
    over-segmentation bug being fixed. Requiring two pending embeddings to
    agree with each other before minting a cluster from them fixed it
    (verified: both real 2-speaker test files then correctly converged to
    2 clusters, matching ground truth, with no regression on real
    single-speaker files).
    """
    for a in range(len(pending)):
        for b in range(a + 1, len(pending)):
            ea, eb = np.array(pending[a].embedding), np.array(pending[b].embedding)
            sim = float(np.dot(ea, eb) / (np.linalg.norm(ea) * np.linalg.norm(eb)))
            if sim >= SIMILARITY_THRESHOLD:
                return a, b
    return None


def best_match(clusters: list[Cluster], embedding: np.ndarray) -> tuple[int | None, float]:
    """Index of the most similar existing cluster and its cosine similarity
    to `embedding` — (None, -1.0) if there are no clusters yet."""
    best_idx: int | None = None
    best_sim = -1.0
    for i, c in enumerate(clusters):
        centroid = np.array(c.centroid)
        sim = float(np.dot(embedding, centroid) / (np.linalg.norm(embedding) * np.linalg.norm(centroid)))
        if sim > best_sim:
            best_idx, best_sim = i, sim
    return best_idx, best_sim


def update_centroid(cluster: Cluster, embedding: np.ndarray) -> None:
    """Running average, weighted by how many utterances already fed this
    cluster — one loud outlier utterance shouldn't swing an established
    speaker's centroid as much as it would a brand new one."""
    centroid = np.array(cluster.centroid)
    updated = (centroid * cluster.count + embedding) / (cluster.count + 1)
    cluster.centroid = updated.tolist()
    cluster.count += 1
