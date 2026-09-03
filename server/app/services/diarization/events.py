import json
from uuid import UUID

import redis

from app.core.config import get_settings
from app.models.meeting import Channel
from app.services.diarization.cluster import STATE_TTL_SECONDS

# The worker (app/workers/tasks.py:diarize_utterance) knows exactly what it
# just did — labeled a segment in place, or deleted one and replaced it with
# several — so it pushes an explicit event here rather than the live WS
# handler trying to infer what changed by repeatedly diffing DB state
# (fragile: a same-poll-cycle race between a segment's creation and its
# labeling made "is this a brand new id or a freshly-labeled one" ambiguous).
# app/ws/live_session.py's _poll_diarization_updates drains this list and
# forwards events to the browser verbatim.

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def _events_key(meeting_id: UUID) -> str:
    # Shared across channels, deliberately — one WS connection, one stream
    # of events is simpler, and each event's segments now carry their own
    # "channel" field so the frontend can route a Me vs. Them update
    # correctly (see _segment_payload in app/workers/tasks.py).
    return f"diar-events:{meeting_id}"


def notify_channel(meeting_id: UUID) -> str:
    """Pub/sub wake-up channel, separate from the durable list above — the
    list is still the actual source of truth (drain_events reads it), this
    is purely a low-latency "something's there, go check now" ping so
    app/ws/live_session.py doesn't have to poll on a fixed interval to find
    out. A ping published with no subscriber listening is simply lost
    (pub/sub gives no delivery guarantee), which is fine: the subscriber
    also re-checks the list on a much longer fallback timer, so a missed
    ping only ever costs that fallback interval, never a stuck update.
    """
    return f"diar-notify:{meeting_id}"


def _reported_key(meeting_id: UUID, channel: Channel) -> str:
    return f"diar-reported:{meeting_id}:{channel.value}"


def _removed_key(meeting_id: UUID, channel: Channel) -> str:
    return f"diar-removed:{meeting_id}:{channel.value}"


def has_reported_anything(meeting_id: UUID, channel: Channel) -> bool:
    """True once at least one segment has ever been reported for this
    meeting *on this channel* — the caller uses this to decide whether the
    2-distinct-speakers gate just opened for the first time on that channel
    (needing a one-time backfill of every already-labeled segment) or was
    already open (needing only this cycle's incremental change). Scoped per
    channel: Me and Them reaching 2 distinct speakers are unrelated events,
    each needs its own gate/backfill."""
    return _redis().scard(_reported_key(meeting_id, channel)) > 0


def record_removed(meeting_id: UUID, segment_id: str, channel: Channel) -> None:
    """A segment was deleted (superseded by a split). Recorded regardless of
    whether that channel's 2-speakers gate is open yet, so that if it opens
    *later*, the eventual snapshot backfill (see all_removed()) still knows
    to tell the frontend to drop the stale bubble — not just the ones
    removed after the gate happened to already be open."""
    r = _redis()
    key = _removed_key(meeting_id, channel)
    r.sadd(key, segment_id)
    r.expire(key, STATE_TTL_SECONDS)


def all_removed(meeting_id: UUID, channel: Channel) -> list[str]:
    return list(_redis().smembers(_removed_key(meeting_id, channel)))


def push_event(meeting_id: UUID, event: dict, reported_segment_ids: list[str], channel: Channel) -> None:
    r = _redis()
    r.rpush(_events_key(meeting_id), json.dumps(event))
    r.expire(_events_key(meeting_id), STATE_TTL_SECONDS)
    if reported_segment_ids:
        key = _reported_key(meeting_id, channel)
        r.sadd(key, *reported_segment_ids)
        r.expire(key, STATE_TTL_SECONDS)
    # Wake up a subscriber immediately rather than making it wait out its own
    # poll interval — see notify_channel's docstring for why this is safe
    # to be best-effort.
    r.publish(notify_channel(meeting_id), "1")


def drain_events(meeting_id: UUID) -> list[dict]:
    """Pops and returns every pending event for this meeting, oldest first."""
    r = _redis()
    key = _events_key(meeting_id)
    events = []
    while True:
        raw = r.lpop(key)
        if raw is None:
            break
        events.append(json.loads(raw))
    return events
