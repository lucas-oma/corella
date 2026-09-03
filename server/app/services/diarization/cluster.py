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
    """One voice in a meeting-channel's registry — persisted across every
    periodic reconciliation pass (app/workers/tasks.py:reconcile_diarization),
    not just one decision. `speaker_id` is None for a *provisional* entry: a
    voice the registry has noticed but hasn't yet earned a real "Speaker
    N"/"Them N" label (see diarization_guest_min_ms/diarization_guest_min_share
    in app/core/config.py) — this app's version of the reference design's
    "guest folding", the actual fix for a short/noisy fragment permanently
    minting a spurious extra speaker. `weight_ms` is real, non-double-counted
    assigned speech time (only ever incremented by a segment's own duration
    the first time that segment is resolved, never by raw window/turn
    duration, which would double-count across passes' overlapping windows) —
    the only thing the guest floor is measured against.
    """

    centroid: list[float]
    count: int
    weight_ms: int = 0
    speaker_id: str | None = None  # Speaker.id, as a str — None until promoted


@dataclass
class PendingSegment:
    """A TranscriptSegment matched (by turn overlap) to a still-provisional
    Cluster — held here, per meeting+channel, until that cluster either
    crosses the guest floor (promoted, every pending entry pointing at it
    gets labeled at once) or the meeting ends still folded (segment stays
    unlabeled — MeetingDetail.tsx already degrades that gracefully, same as
    every other "no signal" case this app has always had).
    """

    segment_id: str  # TranscriptSegment.id, as a str
    cluster_index: int  # index into the same meeting+channel's `clusters` list


def _state_key(meeting_id: UUID, channel: Channel) -> str:
    return f"diar:{meeting_id}:{channel.value}"


def _pending_key(meeting_id: UUID, channel: Channel) -> str:
    return f"diar-pending:{meeting_id}:{channel.value}"


@contextmanager
def locked_state(meeting_id: UUID, channel: Channel):
    """Per-meeting-per-channel lock around one reconciliation pass's
    read-decide-write cycle — without it, two passes dispatched close
    together (the reconcile loop and a final catch-up pass, say) could both
    see the same registry state and double-claim it. Scoped by channel (not
    just meeting) so a "Me" voice and a "Them" voice never get clustered
    against each other's centroids — Me and Them are unrelated pools of
    people, one a local mic capture, the other a shared tab/system-audio
    track. Yields a mutable (list[Cluster], list[PendingSegment]) pair;
    mutate either in place, both are saved back to Redis together when the
    block exits — a single pass's decision can touch both at once (e.g.
    promoting a cluster drains pending entries pointing at it), and they'd
    drift out of sync under separate locks.
    """
    r = _redis()
    key = _state_key(meeting_id, channel)
    pending_key = _pending_key(meeting_id, channel)
    with r.lock(f"diar-lock:{meeting_id}:{channel.value}", timeout=30):
        raw = r.get(key)
        clusters = [Cluster(**c) for c in json.loads(raw)] if raw else []
        pending_raw = r.get(pending_key)
        pending = [PendingSegment(**p) for p in json.loads(pending_raw)] if pending_raw else []
        yield clusters, pending
        r.set(key, json.dumps([asdict(c) for c in clusters]), ex=STATE_TTL_SECONDS)
        r.set(pending_key, json.dumps([asdict(p) for p in pending]), ex=STATE_TTL_SECONDS)


def peek_clusters(meeting_id: UUID, channel: Channel) -> list[Cluster]:
    """Lock-free read of the current registry state — used only by
    app/workers/tasks.py:quick_label_hint, the fast advisory "does this
    match someone already confirmed" check dispatched the instant a
    segment commits (see that task's own docstring for why this needs to
    be lock-free and read-only: it must never contend with, or mutate,
    reconcile_diarization's own locked_state). A small race against a
    concurrent reconciliation pass here only ever costs a possibly-one-pass-
    stale hint, never a wrong permanent write — hints never touch Postgres
    or this registry, only reconcile_diarization does that, under the real
    lock.
    """
    raw = _redis().get(_state_key(meeting_id, channel))
    return [Cluster(**c) for c in json.loads(raw)] if raw else []


def best_match(
    clusters: list[Cluster], embedding: np.ndarray, exclude: set[int] | None = None
) -> tuple[int | None, float]:
    """Index of the most similar existing cluster and its cosine similarity
    to `embedding` — (None, -1.0) if there are no (non-excluded) clusters.
    `exclude` backs claim-once matching (reconcile_diarization): a registry
    entry already claimed by a busier local voice this same pass can't also
    be claimed by a second one — without that, two different real speakers
    active in the same reconciliation window could both match the same
    stored voice and get merged.
    """
    best_idx: int | None = None
    best_sim = -1.0
    for i, c in enumerate(clusters):
        if exclude and i in exclude:
            continue
        centroid = np.array(c.centroid)
        sim = float(np.dot(embedding, centroid) / (np.linalg.norm(embedding) * np.linalg.norm(centroid)))
        if sim > best_sim:
            best_idx, best_sim = i, sim
    return best_idx, best_sim


def update_centroid(cluster: Cluster, embedding: np.ndarray) -> None:
    """Running average, weighted by how many reconciliation passes already
    fed this cluster — one noisy pass shouldn't swing an established
    speaker's centroid as much as it would a brand new one. Runs on every
    pass a voice is re-observed, independent of weight_ms (which only moves
    on newly-resolved segments) — re-observing the same speaker across
    overlapping windows is harmless, even desirable, for centroid quality."""
    centroid = np.array(cluster.centroid)
    updated = (centroid * cluster.count + embedding) / (cluster.count + 1)
    cluster.centroid = updated.tolist()
    cluster.count += 1


def meets_guest_floor(cluster: Cluster, total_channel_weight_ms: int, settings) -> bool:
    """Whether a provisional cluster has earned a real "Speaker N"/"Them N"
    label yet — both an absolute floor (diarization_guest_min_ms) and a
    floor relative to everything tracked on this channel so far
    (diarization_guest_min_share), whichever is stricter. See Cluster's own
    docstring for why weight_ms is safe to compare here (never
    double-counted across overlapping reconciliation windows)."""
    if total_channel_weight_ms <= 0:
        return False
    return (
        cluster.weight_ms >= settings.diarization_guest_min_ms
        and cluster.weight_ms / total_channel_weight_ms >= settings.diarization_guest_min_share
    )
