import asyncio
import base64
import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import storage
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import decode_access_token
from app.models.meeting import Channel, Meeting, MeetingStatus, TranscriptSegment
from app.models.user import User, UserRole
from app.services.asr import deepgram
from app.services.asr.deepgram_stream import DeepgramLiveStream, DeepgramStreamError, StreamResult
from app.services.asr.resolve import ResolvedStt, resolve_stt_provider
from app.services.asr.whisper import transcribe as whisper_transcribe
from app.services.asr.whisper import warm_up
from app.services.audio.mixing import extract_channel_window, mix_channel_recordings, write_wav
from app.services.copilot.live import run_cycle as run_copilot_cycle
from app.services.diarization import events as diar_events
from app.services.llm.resolve import ResolvedProvider, resolve_provider
from app.services.vad.vad import UtteranceDetector
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter()

_CHANNEL_BY_BYTE = {0: Channel.ME, 1: Channel.THEM}
_CHANNEL_KEY = {Channel.ME: "me", Channel.THEM: "them"}
AUTH_TIMEOUT_SECONDS = 5.0
SAMPLE_RATE = 16000

# Rolling live-preview cadence (local whisper path only — see
# LiveSession.maybe_schedule_preview). 1s reads as "words as you speak";
# each preview re-decodes the whole accumulating utterance from its start
# (never streamed word-by-word — a shorter re-decode restarting mid-sentence
# would visibly backtrack), so the next one isn't scheduled until 2x the
# last decode's own duration has passed — a fast machine previews every
# second, a busy one backs off automatically instead of the preview decodes
# themselves starving the real committed-segment transcription.
_PREVIEW_BASE_SECONDS = 1.0

# asyncio only holds a *weak* reference to a task once nothing else does —
# an unreferenced background task can be garbage-collected mid-run. Keep a
# strong reference here for the drain-and-finalize task, which deliberately
# outlives the connection that spawned it.
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


class _Utterance:
    __slots__ = ("channel", "pcm", "start_ms", "end_ms")

    def __init__(self, channel: Channel, pcm: bytes, start_ms: int, end_ms: int):
        self.channel = channel
        self.pcm = pcm
        self.start_ms = start_ms
        self.end_ms = end_ms


class LiveSession:
    """Per-connection state: one VAD-driven utterance detector and one
    timestamped recording buffer per channel, the session clock they're
    both measured against, and a queue of utterances awaiting transcription.

    Transcription is deliberately decoupled from the WS receive loop (see
    the queue/consumer split in the handler below) — awaiting it inline
    used to mean one slow utterance (in testing: a one-time model download)
    blocked *all* further messages, including `stop`, from being processed.
    """

    def __init__(
        self,
        meeting_id: UUID,
        owner_id: UUID,
        provider: ResolvedProvider | None,
        stt: ResolvedStt,
        is_admin: bool = False,
    ):
        self.meeting_id = meeting_id
        self.owner_id = owner_id
        self.provider = provider
        self.stt = stt
        self._start = time.monotonic()
        # Admin-only live debug panel (own session only — see the plan's
        # Phase R). Off by default and toggled by an explicit control frame;
        # `debug()` is a no-op (one `if` check) when disabled, so a normal
        # session pays nothing for this existing.
        self.is_admin = is_admin
        self.debug_enabled = False
        self.debug_queue: asyncio.Queue = asyncio.Queue()
        settings = get_settings()
        self.detectors = {
            channel: UtteranceDetector(
                settings.live_vad_aggressiveness,
                settings.live_max_utterance_seconds,
                settings.live_min_utterance_ms,
            )
            for channel in (Channel.ME, Channel.THEM)
        }
        self.recordings: dict[str, list[tuple[int, bytes]]] = {"me": [], "them": []}
        self.queue: asyncio.Queue[_Utterance] = asyncio.Queue()

        # Deepgram live streaming (stt.provider == "deepgram" only) — one
        # persistent per-channel connection, opened right after auth
        # (_open_deepgram_stream). None means "this channel is on local
        # VAD/whisper right now", either because streaming was never
        # attempted (whisper-only session) or because this channel's own
        # stream failed/dropped and fell back — the other channel's entry
        # is untouched either way (per-channel independence).
        self.deepgram_streams: dict[Channel, DeepgramLiveStream | None] = {
            Channel.ME: None,
            Channel.THEM: None,
        }
        # Captured via elapsed_ms() at the moment each channel's stream
        # connects — Deepgram's own start/duration fields are relative to
        # when *that socket* opened, not this session's own clock; every
        # timestamp derived from a Deepgram result gets this added back in
        # before it's used anywhere downstream.
        self.deepgram_offset_ms: dict[Channel, int] = {}

        # Rolling live-preview state, per channel — local whisper path only
        # (see maybe_schedule_preview). next_preview_at is a monotonic
        # deadline, not yet reached for either channel at session start;
        # preview_in_flight guards against overlapping decodes for the same
        # channel (CTranslate2 models aren't guaranteed safe for concurrent
        # calls from one instance, same constraint already documented for
        # the committed-segment path).
        self.next_preview_at: dict[Channel, float] = {}
        self.preview_in_flight: dict[Channel, bool] = {Channel.ME: False, Channel.THEM: False}

        # Live copilot trigger state — see _maybe_trigger_copilot.
        self.segments_since_cycle = 0
        self.last_cycle_at = self._start
        self.copilot_running = False

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def debug(self, stage: str, **detail) -> None:
        """Push a debug event to the admin-only live panel, if enabled for
        this session — see the plan's Phase R. Cheap when disabled (one
        `if`); never raises (a debug-instrumentation bug must never break
        the actual live session).
        """
        if not self.debug_enabled:
            return
        try:
            self.debug_queue.put_nowait(
                {"type": "debug_event", "stage": stage, "at_ms": self.elapsed_ms(), "detail": detail}
            )
        except Exception:
            logger.exception("Live session %s: failed to enqueue debug event %r", self.meeting_id, stage)

    def on_audio(self, channel: Channel, pcm: bytes) -> None:
        """Feeds the VAD detector and enqueues any utterances it completes
        — unless this channel has a healthy Deepgram stream, in which case
        the raw audio goes straight there instead (Deepgram does its own
        endpointing; local VAD would be redundant and would never see
        anything to flush). Timestamps are captured now (flush time), not
        when the queue consumer eventually gets to them.
        """
        self.recordings[_CHANNEL_KEY[channel]].append((self.elapsed_ms(), pcm))

        stream = self.deepgram_streams.get(channel)
        if stream is not None:
            stream.send(pcm)
            return

        for utterance_pcm in self.detectors[channel].feed(pcm):
            end_ms = self.elapsed_ms()
            self.debug(
                "vad_utterance_flushed", channel=channel.value, duration_ms=_duration_ms(utterance_pcm)
            )
            self.queue.put_nowait(
                _Utterance(channel, utterance_pcm, max(0, end_ms - _duration_ms(utterance_pcm)), end_ms)
            )

    def maybe_schedule_preview(self, channel: Channel) -> bytes | None:
        """A rolling live preview — re-decoding the utterance a channel's VAD
        detector is still accumulating, well before the real pause-bounded
        flush — is what actually makes local transcription feel live rather
        than arriving in one lump per utterance. Local whisper path only — a
        channel counts as "local" whenever it isn't currently routed to a
        healthy Deepgram stream, which covers both a whisper-only session
        and a Deepgram channel that's fallen back mid-session
        (deepgram_streams[channel] is None either way). A channel still on
        a healthy Deepgram stream gets true interim results pushed by
        Deepgram itself, so no local re-decode is needed or wanted there.
        Returns the buffer to preview-decode if one is genuinely due right
        now (not before the self-paced deadline, not already mid-decode for
        this channel, and there's enough buffered speech to bother with),
        else None — the caller (_handle_audio_frame) does the actual
        decode, since that's async and this method is sync.
        """
        if self.deepgram_streams.get(channel) is not None:
            return None
        if self.preview_in_flight[channel]:
            return None
        if time.monotonic() < self.next_preview_at.get(channel, 0):
            return None
        pending = self.detectors[channel].peek_current_utterance()
        if pending is None:
            return None
        self.preview_in_flight[channel] = True
        return pending

    def enqueue_leftovers(self) -> None:
        """Called at session end — flush any in-progress (no trailing
        pause) utterance per channel."""
        for channel, detector in self.detectors.items():
            leftover = detector.flush_remaining()
            if leftover:
                end_ms = self.elapsed_ms()
                self.queue.put_nowait(
                    _Utterance(channel, leftover, max(0, end_ms - _duration_ms(leftover)), end_ms)
                )


async def _open_deepgram_stream(websocket: WebSocket, session: LiveSession, channel: Channel) -> None:
    """Opens one channel's persistent Deepgram connection and wires its
    callbacks in. A final result goes through the exact same _commit_segment
    path a local VAD/whisper utterance uses — diarization dispatch and the
    `transcript` WS event don't need to know which engine actually produced
    a segment. A connect failure (or a later drop, via on_closed) just
    leaves deepgram_streams[channel] at None, which is exactly what makes
    LiveSession.on_audio/maybe_schedule_preview fall this one channel back
    to local VAD/whisper for the rest of the session — the other channel is
    never touched.
    """
    channel_key = _CHANNEL_KEY[channel]

    async def on_result(result: StreamResult) -> None:
        if not result.is_final:
            try:
                await websocket.send_json(
                    {"type": "partial_transcript", "channel": channel_key, "text": result.text}
                )
            except Exception:
                pass  # client may already be gone
            return

        offset_ms = session.deepgram_offset_ms.get(channel, 0)
        start_ms = offset_ms + round(result.start_s * 1000)
        end_ms = offset_ms + round((result.start_s + result.duration_s) * 1000)
        try:
            await _commit_segment(websocket, session, channel, start_ms, end_ms, result.text, result.words)
        except Exception:
            logger.exception(
                "Live session %s: committing a Deepgram-streamed segment failed", session.meeting_id
            )

    async def on_closed() -> None:
        logger.warning(
            "Live session %s: Deepgram stream for the %s channel dropped — "
            "falling back to local VAD/whisper for the rest of the session",
            session.meeting_id,
            channel_key,
        )
        session.deepgram_streams[channel] = None
        session.debug("stt_fallback", provider="deepgram", channel=channel_key, reason="stream closed")

    stream = DeepgramLiveStream(session.stt.api_key, session.stt.model, session.stt.language, on_result, on_closed)
    try:
        # Captured right before connecting, not after — the offset only
        # needs to be close, and this keeps it simple; connect() itself is
        # typically sub-second.
        session.deepgram_offset_ms[channel] = session.elapsed_ms()
        await stream.connect()
        session.deepgram_streams[channel] = stream
    except DeepgramStreamError:
        logger.exception(
            "Live session %s: failed to open a Deepgram stream for the %s channel — "
            "using local VAD/whisper for the whole session on this channel",
            session.meeting_id,
            channel_key,
        )
        session.debug("stt_fallback", provider="deepgram", channel=channel_key, reason="connect failed")


@router.websocket("/ws/meetings/{meeting_id}/live")
async def live_session_ws(websocket: WebSocket, meeting_id: UUID) -> None:
    await websocket.accept()

    user = await _authenticate(websocket)
    if user is None:
        return

    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None or meeting.owner_id != user.id:
            await websocket.close(code=4404, reason="Meeting not found")
            return
        if meeting.status != MeetingStatus.RECORDING:
            await websocket.close(code=4409, reason="Meeting is not in a recording state")
            return
        meeting.started_at = datetime.now(UTC)
        await db.commit()
        provider = await resolve_provider(db, user.id)
        stt = await resolve_stt_provider(db, user.id)

    # Load the local model *before* saying "ready" regardless of which STT
    # engine is preferred — better a few extra seconds of "Connecting…" on
    # first use than a session that looks live but silently stalls partway
    # through waiting on a cold model load. Kept warm even when Deepgram is
    # preferred: a per-utterance Deepgram failure falls back to local
    # whisper for that utterance (see _transcribe_pcm), so it needs to
    # already be ready, not cold-loading mid-call.
    await asyncio.get_running_loop().run_in_executor(None, warm_up)

    session = LiveSession(meeting_id, user.id, provider, stt, is_admin=user.role == UserRole.ADMIN)

    if stt.provider == "deepgram":
        # One persistent connection per channel, opened right alongside the
        # local whisper warm-up above (which stays unconditional — it's the
        # fallback target for either channel, not just the local-only
        # path). A failure to even connect just means that channel starts
        # the session on local VAD/whisper already — see
        # _open_deepgram_stream, never a reason to fail the whole session.
        await asyncio.gather(
            _open_deepgram_stream(websocket, session, Channel.ME),
            _open_deepgram_stream(websocket, session, Channel.THEM),
        )

    consumer_task = asyncio.create_task(_consume_utterances(websocket, session))
    diarization_poll_task = asyncio.create_task(_poll_diarization_updates(websocket, session))
    reconcile_task = asyncio.create_task(_reconcile_diarization_loop(session))
    debug_pump_task = asyncio.create_task(_pump_debug_events(websocket, session))

    await websocket.send_json({"type": "ready"})
    if provider is None:
        try:
            await websocket.send_json({"type": "copilot_unavailable"})
        except Exception:
            pass

    stopped_gracefully = False
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                _handle_audio_frame(session, websocket, message["bytes"])
            elif message.get("text") is not None:
                if _is_stop(message["text"]):
                    stopped_gracefully = True
                    break
                _handle_control_frame(session, message["text"])
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Live session %s crashed mid-stream", meeting_id)
    finally:
        # Runs whether the client said "stop", dropped the connection, or we
        # hit an error — a live meeting must never be left stuck at
        # `recording` with nothing left to finish it (same lesson as the
        # Phase B/C orphaned-meeting bug).
        #
        # Draining a possible transcription backlog and mixing the full
        # recording can take a while on a long call — that used to happen
        # *before* replying, so "Stop" could hang for as long as that took.
        # Mark the meeting `processing` immediately instead and let the
        # frontend fall into the exact same polling path the upload flow
        # already uses; the real finalize keeps running detached from this
        # connection.
        session.enqueue_leftovers()
        await _close_deepgram_streams(session)
        await _mark_processing(meeting_id)

        # Unlike consumer_task, nothing left to push events to once the
        # connection is ending — stop them here rather than surviving into
        # the background _drain_and_finalize task. A final reconciliation
        # pass for any trailing audio this loop hasn't gotten to yet is
        # dispatched separately, inside _drain_and_finalize.
        diarization_poll_task.cancel()
        reconcile_task.cancel()
        debug_pump_task.cancel()

        if stopped_gracefully:
            try:
                await websocket.send_json({"type": "stopped"})
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass

        _spawn_background(_drain_and_finalize(session, consumer_task))


async def _close_deepgram_streams(session: LiveSession) -> None:
    """Closes whichever channels still have a healthy Deepgram stream open
    — awaited, not fire-and-forget, so a still-in-flight final utterance
    (the caller stopped mid-sentence) gets committed via _commit_segment
    *before* _mark_processing/_drain_and_finalize run, same as how a local
    VAD channel's own leftover gets flushed via enqueue_leftovers() first.
    A channel already on local VAD/whisper (deepgram_streams[channel] is
    None, whether it never streamed or already fell back) has nothing to
    close here.
    """
    streams = [s for s in session.deepgram_streams.values() if s is not None]
    if not streams:
        return
    results = await asyncio.gather(*(s.close() for s in streams), return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.exception(
                "Live session %s: failed to close a Deepgram stream cleanly",
                session.meeting_id,
                exc_info=result,
            )


async def _authenticate(websocket: WebSocket) -> User | None:
    """Browsers can't set custom WebSocket headers, and a query-string
    token would land in access logs — so auth is the first text frame
    instead: {"type": "auth", "token": "..."}.
    """
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect):
        await websocket.close(code=4401, reason="Authentication timed out")
        return None

    try:
        payload = json.loads(raw)
        token = payload["token"]
    except (json.JSONDecodeError, KeyError, TypeError):
        await websocket.close(code=4401, reason='First message must be {"type":"auth","token":...}')
        return None

    user_id = decode_access_token(token)
    if user_id is None:
        await websocket.close(code=4401, reason="Invalid token")
        return None

    async with SessionLocal() as db:
        user = await db.get(User, user_id)
    if user is None:
        await websocket.close(code=4401, reason="Invalid token")
        return None
    return user


def _is_stop(raw: str) -> bool:
    try:
        return json.loads(raw).get("type") == "stop"
    except json.JSONDecodeError:
        return False


def _handle_control_frame(session: LiveSession, raw: str) -> None:
    """Non-stop text control frames — currently just the admin-only debug
    toggle: {"type": "debug", "enabled": true|false}. Server-side gated on
    session.is_admin (set once at session start from the real DB role) —
    never trust the client's own claim, same as every other permission
    check in this codebase.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return
    if payload.get("type") != "debug":
        return
    if not session.is_admin:
        return
    session.debug_enabled = bool(payload.get("enabled"))


def _handle_audio_frame(session: LiveSession, websocket: WebSocket, data: bytes) -> None:
    if len(data) < 2:
        return
    channel = _CHANNEL_BY_BYTE.get(data[0])
    if channel is None:
        return
    session.on_audio(channel, data[1:])
    pending_preview = session.maybe_schedule_preview(channel)
    if pending_preview is not None:
        _spawn_background(_run_preview_decode(websocket, session, channel, pending_preview))


async def _consume_utterances(websocket: WebSocket, session: LiveSession) -> None:
    """Runs for the life of the connection, transcribing queued utterances
    one at a time (CTranslate2 models aren't guaranteed safe for concurrent
    calls from one instance) without blocking the receive loop above.
    """
    while True:
        utterance = await session.queue.get()
        try:
            created = await _transcribe_and_send(websocket, session, utterance)
            if created:
                session.segments_since_cycle += 1
                await _maybe_trigger_copilot(websocket, session)
        except Exception:
            logger.exception("Live transcription failed for meeting %s", session.meeting_id)
        finally:
            session.queue.task_done()


_DIARIZATION_FALLBACK_INTERVAL_SECONDS = 5.0


async def _poll_diarization_updates(websocket: WebSocket, session: LiveSession) -> None:
    """Bridges the worker's periodic reconcile_diarization task (a separate
    Celery process, dispatched by _reconcile_diarization_loop below) back to
    this live connection. Runs for the life of the connection — cancelled
    directly in the main handler's `finally`, unlike `consumer_task` which
    deliberately survives into the background; there's nothing left to push
    events to once the connection is gone.

    The worker pushes an explicit event to a per-meeting Redis list the
    moment it decides something changed (app/services/diarization/events.py)
    — including the "promoted speaker" gating and the one-time full backfill
    once that gate first opens — rather than this loop trying to infer what
    changed by diffing DB state itself; drains and forwards whatever's
    pending, unmodified, each cycle.

    Woken by Redis pub/sub the moment the worker pushes something (near-
    instant), not a fixed poll interval — a flat 1s sleep here used to add up
    to a full second of pure dead latency on top of whatever the worker's own
    reconciliation pass took. Pub/sub delivery isn't guaranteed (a message
    published with no subscriber connected is simply dropped), so this still
    falls back to draining on a much longer timer regardless of whether a
    ping arrived — the correctness guarantee lives in drain_events reading
    real state, not in never missing a ping.
    """
    client = aioredis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(diar_events.notify_channel(session.meeting_id))
        while True:
            try:
                await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_DIARIZATION_FALLBACK_INTERVAL_SECONDS
                )
            except Exception:
                logger.exception("Live session %s: diarization pub/sub wait failed", session.meeting_id)

            try:
                events = await asyncio.get_running_loop().run_in_executor(
                    None, diar_events.drain_events, session.meeting_id
                )
            except Exception:
                logger.exception("Live session %s: diarization-event drain failed", session.meeting_id)
                continue

            for event in events:
                try:
                    await websocket.send_json(event)
                except Exception:
                    pass  # client may already be gone
    finally:
        await pubsub.aclose()
        await client.aclose()


_RECONCILE_CHECK_INTERVAL_SECONDS = 2.0


def _dispatch_reconciliation(session: LiveSession, channel: Channel, settings) -> bool:
    """Slices this channel's rolling window (already-received audio,
    app/services/audio/mixing.py:extract_channel_window — session.recordings
    accumulates both channels regardless of STT engine, needed for the final
    mixdown either way) and dispatches one corella.reconcile_diarization
    task, if there's enough accumulated audio yet
    (diarization_reconcile_min_window_ms). Returns whether it actually
    dispatched — the caller uses this to decide whether to reset that
    channel's next-dispatch clock; a channel with too little audio so far
    should be re-checked again soon, not made to wait a full interval for
    nothing.
    """
    end_ms = session.elapsed_ms()
    start_ms = max(0, end_ms - settings.diarization_reconcile_window_ms)
    if end_ms - start_ms < settings.diarization_reconcile_min_window_ms:
        return False
    channel_key = _CHANNEL_KEY[channel]
    window_pcm = extract_channel_window(session.recordings[channel_key], start_ms, end_ms)
    if not window_pcm:
        return False
    session.debug("diarization_reconcile_dispatched", channel=channel_key, window_ms=end_ms - start_ms)
    celery_app.send_task(
        "corella.reconcile_diarization",
        args=[str(session.meeting_id), channel.value, base64.b64encode(window_pcm).decode(), start_ms],
    )
    return True


async def _reconcile_diarization_loop(session: LiveSession) -> None:
    """Periodically dispatches app/workers/tasks.py:reconcile_diarization
    for each active channel — the live-session half of the periodic-window
    diarization design (see app/core/config.py's diarization_reconcile_*
    settings docstring for why this replaced per-utterance dispatch). Runs
    for the life of the connection, cancelled in the main handler's
    `finally` alongside diarization_poll_task; a final catch-up pass for any
    trailing audio this loop hasn't gotten around to yet is dispatched
    separately in _drain_and_finalize, after the connection itself has
    already ended.
    """
    settings = get_settings()
    next_at: dict[Channel, float] = {Channel.ME: 0.0, Channel.THEM: 0.0}
    while True:
        await asyncio.sleep(_RECONCILE_CHECK_INTERVAL_SECONDS)
        now = time.monotonic()
        for channel in (Channel.ME, Channel.THEM):
            if now < next_at[channel]:
                continue
            if _dispatch_reconciliation(session, channel, settings):
                next_at[channel] = now + settings.diarization_reconcile_interval_ms / 1000


async def _pump_debug_events(websocket: WebSocket, session: LiveSession) -> None:
    """Forwards session.debug_queue events to the client verbatim. Runs for
    the life of the connection, cancelled in the main handler's `finally`
    alongside diarization_poll_task — nothing left to push to once the
    connection is ending, same reasoning as that task.
    """
    while True:
        event = await session.debug_queue.get()
        try:
            await websocket.send_json(event)
        except Exception:
            pass  # client may already be gone


async def _maybe_trigger_copilot(websocket: WebSocket, session: LiveSession) -> None:
    settings = get_settings()
    elapsed = time.monotonic() - session.last_cycle_at
    if session.provider is None or session.copilot_running:
        return

    if (
        session.segments_since_cycle < settings.copilot_trigger_segments
        and elapsed < settings.copilot_trigger_seconds
    ):
        return

    session.segments_since_cycle = 0
    session.last_cycle_at = time.monotonic()
    session.copilot_running = True
    _spawn_background(_run_copilot_and_send(websocket, session))


async def _run_copilot_and_send(websocket: WebSocket, session: LiveSession) -> None:
    session.debug("copilot_cycle_started")
    started = time.monotonic()
    try:
        async with SessionLocal() as db:
            result = await run_copilot_cycle(db, session.meeting_id, session.owner_id, session.provider)
    except Exception:
        logger.exception("Copilot cycle failed for meeting %s", session.meeting_id)
        result = None
    finally:
        session.copilot_running = False

    elapsed_ms = int((time.monotonic() - started) * 1000)
    session.debug("copilot_cycle_result", ok=result is not None, elapsed_ms=elapsed_ms)

    if result is None:
        return
    try:
        await websocket.send_json(
            {
                "type": "copilot",
                "suggestion": result.suggestion,
                "blockers": result.blockers,
                "action_items": result.action_items,
                "coach_score": result.coach_score,
            }
        )
    except Exception:
        pass  # client may already be gone


async def _run_preview_decode(
    websocket: WebSocket, session: LiveSession, channel: Channel, pcm: bytes
) -> None:
    """The actual rolling-preview decode `maybe_schedule_preview` decided is
    due — a full re-decode of the channel's still-accumulating utterance,
    pushed as a draft the frontend replaces in place on each update and
    clears the moment the real committed segment for that channel arrives.
    Never touches the committed-segment path (session.queue) at all — this
    is purely an additional, disposable, more-frequent read of the same
    buffer the real VAD-triggered flush also reads.
    """
    started = time.monotonic()
    text = ""
    try:
        text, _words = await _transcribe_pcm_whisper(pcm, word_timestamps=False, debug=session.debug)
    except Exception:
        logger.exception("Live session %s: preview decode failed for %s", session.meeting_id, channel.value)
    finally:
        # Self-paces regardless of outcome (including a failed decode) —
        # never schedule faster than 2x what a decode actually just cost.
        elapsed = time.monotonic() - started
        session.next_preview_at[channel] = time.monotonic() + max(_PREVIEW_BASE_SECONDS, elapsed * 2)
        session.preview_in_flight[channel] = False

    if not text:
        return
    try:
        await websocket.send_json({"type": "partial_transcript", "channel": channel.value, "text": text})
    except Exception:
        pass  # client may already be gone


async def _transcribe_and_send(websocket: WebSocket, session: LiveSession, utterance: _Utterance) -> bool:
    # word_timestamps is no longer needed for diarization purposes (Phase
    # W's periodic reconciliation relabels already-committed segments by
    # turn overlap, never splits one — see reconcile_diarization's own
    # docstring), so it's always False on the committed-segment path now.
    text, words = await _transcribe_pcm(utterance.pcm, session.stt, word_timestamps=False, debug=session.debug)
    if not text:
        session.debug(
            "transcript_empty", channel=utterance.channel.value, duration_ms=_duration_ms(utterance.pcm)
        )
        return False

    return await _commit_segment(
        websocket, session, utterance.channel, utterance.start_ms, utterance.end_ms, text, words
    )


async def _commit_segment(
    websocket: WebSocket,
    session: LiveSession,
    channel: Channel,
    start_ms: int,
    end_ms: int,
    text: str,
    words: list,
) -> bool:
    """Persists one committed transcript segment and pushes the `transcript`
    WS event — shared by both STT paths: the local VAD/whisper queue
    consumer (_transcribe_and_send above) and a Deepgram stream's own final
    results (_open_deepgram_stream's on_result). The authoritative same-room
    diarization decision is not made per-segment here — see
    _reconcile_diarization_loop, which periodically reconciles a rolling
    window of each channel's audio against the persistent voice registry
    and is the only thing that ever creates/promotes a speaker or writes a
    label to Postgres. A fast, read-only *hint* dispatch is, though (see
    corella.quick_label_hint) — a real user report that live labeling felt
    "not live at all" traced to that periodic pass's own real compute time
    (6-33s measured in production) stacking on top of its own interval; the
    hint recognizes an *already-confirmed* voice almost instantly, without
    waiting for the next pass, while never itself deciding anything new.
    """
    async with SessionLocal() as db:
        row = TranscriptSegment(
            meeting_id=session.meeting_id,
            speaker_id=None,
            channel=channel,
            start_ms=start_ms,
            end_ms=end_ms,
            text=text,
            is_partial=False,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

    if channel in (Channel.ME, Channel.THEM):
        channel_key = _CHANNEL_KEY[channel]
        utterance_pcm = extract_channel_window(session.recordings[channel_key], start_ms, end_ms)
        if utterance_pcm:
            session.debug("quick_label_hint_dispatched", segment_id=str(row.id), channel=channel.value)
            celery_app.send_task(
                "corella.quick_label_hint",
                args=[str(session.meeting_id), str(row.id), channel.value, base64.b64encode(utterance_pcm).decode()],
            )

    try:
        await websocket.send_json(
            {
                "type": "transcript",
                "segment": {
                    "id": str(row.id),
                    "channel": channel.value,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                },
            }
        )
    except Exception:
        pass  # client may already be gone; the segment is still persisted

    return True


def _noop_debug(stage: str, **detail) -> None:
    pass


async def _transcribe_pcm(
    pcm: bytes, stt: ResolvedStt, word_timestamps: bool = False, debug=_noop_debug
) -> tuple[str, list]:
    """Deepgram (if resolved for this session) or local faster-whisper —
    see app/services/asr/resolve.py. A Deepgram failure mid-session falls
    back to local whisper for *that* utterance rather than dropping it,
    same graceful-degradation spirit as the upload path's equivalent
    fallback (app/workers/tasks.py:_resolve_and_maybe_transcribe_deepgram).

    `debug` is session.debug (or a no-op default for callers that don't
    care, e.g. the upload path never reaches this function at all) — see
    the plan's Phase R admin debug panel.
    """
    if stt.provider == "deepgram":
        pcm_duration_ms = _duration_ms(pcm)
        debug("stt_request", provider="deepgram", model=stt.model, language=stt.language, pcm_duration_ms=pcm_duration_ms)
        started = time.monotonic()
        try:
            segments = await deepgram.transcribe(
                _wav_bytes(pcm), stt.model, stt.api_key, word_timestamps, language=stt.language
            )
            text, words = _segments_to_text_words(segments)
            debug(
                "stt_response",
                provider="deepgram",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                char_count=len(text),
                text_preview=text[:120],
            )
            return text, words
        except deepgram.SttError as e:
            logger.exception(
                "Deepgram live transcription failed; falling back to local whisper for this utterance"
            )
            debug("stt_fallback", provider="deepgram", reason=str(e))
    return await _transcribe_pcm_whisper(pcm, word_timestamps, debug)


async def _transcribe_pcm_whisper(pcm: bytes, word_timestamps: bool, debug=_noop_debug) -> tuple[str, list]:
    def _run() -> tuple[str, list]:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            write_wav(path, pcm)
            segments = whisper_transcribe(path, word_timestamps=word_timestamps)
            return _segments_to_text_words(segments)
        finally:
            os.unlink(path)

    debug("stt_request", provider="whisper", pcm_duration_ms=_duration_ms(pcm))
    started = time.monotonic()
    text, words = await asyncio.get_running_loop().run_in_executor(None, _run)
    debug(
        "stt_response",
        provider="whisper",
        elapsed_ms=int((time.monotonic() - started) * 1000),
        char_count=len(text),
        text_preview=text[:120],
    )
    return text, words


def _segments_to_text_words(segments) -> tuple[str, list]:
    text = " ".join(s.text for s in segments).strip()
    words = [w for s in segments for w in s.words]
    return text, words


def _wav_bytes(pcm: bytes) -> bytes:
    """Deepgram's REST API takes a real audio payload, not raw PCM — a
    small, fast (milliseconds, not the multi-second cost transcription
    itself has) file round-trip reusing the same write_wav() every other
    audio path already writes through.
    """
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        write_wav(path, pcm)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


def _duration_ms(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> int:
    return int(len(pcm) / 2 / sample_rate * 1000)


async def _mark_processing(meeting_id: UUID) -> None:
    """Fast, synchronous status flip so the frontend can leave the live page
    immediately and fall into the same `processing` polling UI the upload
    path already has — the real work happens in _drain_and_finalize."""
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is not None and meeting.status == MeetingStatus.RECORDING:
            meeting.status = MeetingStatus.PROCESSING
            await db.commit()


async def _drain_and_finalize(session: LiveSession, consumer_task: asyncio.Task) -> None:
    """Runs detached from the WS connection (see _spawn_background) — the
    client already got its response and moved on by the time this finishes.
    """
    try:
        await asyncio.wait_for(session.queue.join(), timeout=600)
    except Exception:
        logger.exception(
            "Live session %s: draining the utterance queue failed", session.meeting_id
        )
    consumer_task.cancel()

    # One last reconciliation pass per channel — the periodic loop was
    # already cancelled when the connection ended, so without this, any
    # trailing audio in its window that hadn't been reconciled yet would
    # never get a label at all. Fire-and-forget, same as index_meeting_search
    # / generate_report below: the meeting still finalizes as READY without
    # waiting on it, and the frontend degrades an unlabeled segment
    # gracefully either way.
    settings = get_settings()
    for channel in (Channel.ME, Channel.THEM):
        try:
            _dispatch_reconciliation(session, channel, settings)
        except Exception:
            logger.exception(
                "Live session %s: final reconciliation dispatch failed for %s",
                session.meeting_id,
                channel.value,
            )

    try:
        await _finalize(session)
    except Exception:
        logger.exception("Live session %s: _finalize raised", session.meeting_id)


async def _finalize(session: LiveSession) -> None:
    mixed_pcm = mix_channel_recordings(session.recordings)

    async with SessionLocal() as db:
        meeting = await db.get(Meeting, session.meeting_id)
        if meeting is None:
            return
        try:
            if mixed_pcm:
                meeting_dir = storage.meeting_dir(meeting.id)
                meeting_dir.mkdir(parents=True, exist_ok=True)
                wav_path = meeting_dir / "original.wav"
                write_wav(str(wav_path), mixed_pcm)
                meeting.audio_path = str(wav_path)
                meeting.duration_seconds = round(_duration_ms(mixed_pcm) / 1000)
            meeting.ended_at = datetime.now(UTC)
            meeting.status = MeetingStatus.READY
            meeting.processing_error = None
            await db.commit()
        except Exception as e:
            logger.exception("Failed to finalize live meeting %s", meeting.id)
            await db.rollback()
            meeting.status = MeetingStatus.FAILED
            meeting.processing_error = f"Failed to finalize live recording: {e}"[:2000]
            await db.commit()
            return

    try:
        celery_app.send_task("corella.index_meeting_search", args=[str(session.meeting_id)])
    except Exception:
        # Search indexing is a nice-to-have, not a reason to retroactively
        # mark an already-successfully-finalized meeting as failed.
        logger.exception("Failed to dispatch index_meeting_search for meeting %s", session.meeting_id)

    try:
        celery_app.send_task("corella.generate_report", args=[str(session.meeting_id)])
    except Exception:
        # Same reasoning as index_meeting_search above — the meeting is
        # already successfully finalized either way; the existing manual
        # "Generate report" button is still there if this doesn't run.
        logger.exception("Failed to dispatch generate_report for meeting %s", session.meeting_id)
