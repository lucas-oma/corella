import secrets
from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import get_settings

# A meeting stays `recording` for its entire live session (app/ws/
# live_session.py) — the *only* thing that ever actually stopped a second
# WebSocket from attaching to the same meeting was that status check,
# which a second connection trivially passes since the first session
# hasn't ended yet. This lock is the real guard: SET NX EX is atomic, so
# exactly one connection ever wins the race, regardless of which of
# several concurrent connect attempts got there "first" in wall-clock time.
#
# TTL, not held forever: a session whose `finally` never ran (a hard
# crash) self-heals within this window rather than permanently locking
# the meeting out of ever being recorded again — same graceful-
# degradation instinct as everywhere else in this codebase. The holding
# session renews it well before expiry (see live_session.py's renewal
# loop) for as long as it's actually alive.
_LOCK_TTL_SECONDS = 30
_KEY_PREFIX = "live_recording_lock:"


def _key(meeting_id: UUID) -> str:
    return f"{_KEY_PREFIX}{meeting_id}"


def _client() -> aioredis.Redis:
    # A short-lived connection per call, same pattern app/ws/live_session.py
    # already uses for its diarization pub/sub client — this module has no
    # reason to hold one open longer than a single operation.
    return aioredis.Redis.from_url(get_settings().redis_url, decode_responses=True)


async def acquire(meeting_id: UUID) -> str | None:
    """Attempts to claim the lock for this meeting. Returns a random token
    to hand back to renew/release on success, or None if another
    connection already holds it — the caller (live_session_ws) closes the
    WebSocket in that case rather than ever opening a second session.
    """
    token = secrets.token_hex(16)
    client = _client()
    try:
        acquired = await client.set(_key(meeting_id), token, nx=True, ex=_LOCK_TTL_SECONDS)
        return token if acquired else None
    finally:
        await client.aclose()


async def renew(meeting_id: UUID, token: str) -> None:
    """Pushes the TTL back out — only if `token` still matches what's
    stored, so a session can never renew a lock it doesn't actually hold
    (e.g. its own copy already expired and a different connection won the
    lock in the meantime). Best-effort: a renewal failure just means the
    lock might expire a little early, not a crash.
    """
    client = _client()
    try:
        if await client.get(_key(meeting_id)) == token:
            await client.expire(_key(meeting_id), _LOCK_TTL_SECONDS)
    finally:
        await client.aclose()


async def release(meeting_id: UUID, token: str) -> None:
    """Clears the lock — only if `token` still matches, same reasoning as
    renew(): never clear a lock that isn't actually this session's own
    (it may have already expired and been re-acquired by someone else).
    """
    client = _client()
    try:
        if await client.get(_key(meeting_id)) == token:
            await client.delete(_key(meeting_id))
    finally:
        await client.aclose()
