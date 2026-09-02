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


@contextmanager
def locked_state(meeting_id: UUID, channel: Channel):
    """Per-meeting-per-channel lock around one read-decide-write cycle of
    online speaker clustering — without it, two utterances dispatched close
    together could both see no matching cluster and both decide "new
    speaker" for what's actually the same one. Scoped by channel (not just
    meeting) so a "Me" voice and a "Them" voice never get clustered against
    each other's centroids — Me and Them are unrelated pools of people, one
    a local mic capture, the other a shared tab/system-audio track. Yields
    a mutable list[Cluster]; mutate it in place, it's saved back to Redis
    when the block exits.
    """
    r = _redis()
    key = f"diar:{meeting_id}:{channel.value}"
    with r.lock(f"diar-lock:{meeting_id}:{channel.value}", timeout=10):
        raw = r.get(key)
        clusters = [Cluster(**c) for c in json.loads(raw)] if raw else []
        yield clusters
        r.set(key, json.dumps([asdict(c) for c in clusters]), ex=STATE_TTL_SECONDS)


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
