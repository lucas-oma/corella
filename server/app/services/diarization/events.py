import json
from uuid import UUID

import redis

from app.core.config import get_settings
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
    return f"diar-events:{meeting_id}"


def _reported_key(meeting_id: UUID) -> str:
    return f"diar-reported:{meeting_id}"


def _removed_key(meeting_id: UUID) -> str:
    return f"diar-removed:{meeting_id}"


def has_reported_anything(meeting_id: UUID) -> bool:
    """True once at least one segment has ever been reported for this
    meeting — the caller uses this to decide whether the 2-distinct-
    speakers gate just opened for the first time (needing a one-time
    backfill of every already-labeled segment) or was already open
    (needing only this cycle's incremental change)."""
    return _redis().scard(_reported_key(meeting_id)) > 0


def record_removed(meeting_id: UUID, segment_id: str) -> None:
    """A segment was deleted (superseded by a split). Recorded regardless of
    whether the 2-speakers gate is open yet, so that if it opens *later*,
    the eventual snapshot backfill (see all_removed()) still knows to tell
    the frontend to drop the stale bubble — not just the ones removed after
    the gate happened to already be open."""
    r = _redis()
    r.sadd(_removed_key(meeting_id), segment_id)
    r.expire(_removed_key(meeting_id), STATE_TTL_SECONDS)


def all_removed(meeting_id: UUID) -> list[str]:
    return list(_redis().smembers(_removed_key(meeting_id)))


def push_event(meeting_id: UUID, event: dict, reported_segment_ids: list[str]) -> None:
    r = _redis()
    r.rpush(_events_key(meeting_id), json.dumps(event))
    r.expire(_events_key(meeting_id), STATE_TTL_SECONDS)
    if reported_segment_ids:
        r.sadd(_reported_key(meeting_id), *reported_segment_ids)
        r.expire(_reported_key(meeting_id), STATE_TTL_SECONDS)


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
