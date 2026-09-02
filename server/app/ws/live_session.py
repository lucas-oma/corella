import asyncio
import base64
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import storage
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import decode_access_token
from app.models.meeting import Channel, Meeting, MeetingStatus, TranscriptSegment
from app.models.user import User, UserRole
from app.services.asr import deepgram
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
        """Feeds the VAD detector and enqueues any utterances it completes.
        Timestamps are captured now (flush time), not when the queue
        consumer eventually gets to them.
        """
        self.recordings[_CHANNEL_KEY[channel]].append((self.elapsed_ms(), pcm))
        for utterance_pcm in self.detectors[channel].feed(pcm):
            end_ms = self.elapsed_ms()
            self.debug(
                "vad_utterance_flushed", channel=channel.value, duration_ms=_duration_ms(utterance_pcm)
            )
            self.queue.put_nowait(
                _Utterance(channel, utterance_pcm, max(0, end_ms - _duration_ms(utterance_pcm)), end_ms)
            )

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
        meeting.started_at = datetime.now(timezone.utc)
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
    consumer_task = asyncio.create_task(_consume_utterances(websocket, session))
    diarization_poll_task = asyncio.create_task(_poll_diarization_updates(websocket, session))
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
                _handle_audio_frame(session, message["bytes"])
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
        await _mark_processing(meeting_id)

        # Unlike consumer_task, nothing left to push events to once the
        # connection is ending — stop them here rather than surviving into
        # the background _drain_and_finalize task.
        diarization_poll_task.cancel()
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


def _handle_audio_frame(session: LiveSession, data: bytes) -> None:
    if len(data) < 2:
        return
    channel = _CHANNEL_BY_BYTE.get(data[0])
    if channel is None:
        return
    session.on_audio(channel, data[1:])


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


async def _poll_diarization_updates(websocket: WebSocket, session: LiveSession) -> None:
    """Bridges the worker's per-utterance diarize_utterance task (a separate
    Celery process) back to this live connection. Runs for the life of the
    connection — cancelled directly in the main handler's `finally`, unlike
    `consumer_task` which deliberately survives into the background; there's
    nothing left to push events to once the connection is gone.

    The worker pushes an explicit event to a per-meeting Redis list the
    moment it decides something changed (app/services/diarization/events.py)
    — including the "2+ distinct speakers" gating and the one-time full
    backfill once that gate first opens — rather than this loop trying to
    infer what changed by diffing DB state itself; drains and forwards
    whatever's pending, unmodified, each cycle.
    """
    while True:
        await asyncio.sleep(1)
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


async def _transcribe_and_send(websocket: WebSocket, session: LiveSession, utterance: _Utterance) -> bool:
    # Both Me (one mic, possibly several people around it) and Them (one
    # shared tab/system-audio track, possibly several remote participants)
    # get live diarization — Channel.UNKNOWN, never actually reachable from
    # this WS path (_CHANNEL_BY_BYTE only maps 0/1 to ME/THEM), deliberately
    # excluded rather than assuming every channel needs it.
    needs_diarization = utterance.channel in (Channel.ME, Channel.THEM)
    text, words = await _transcribe_pcm(
        utterance.pcm, session.stt, word_timestamps=needs_diarization, debug=session.debug
    )
    if not text:
        session.debug(
            "transcript_empty", channel=utterance.channel.value, duration_ms=_duration_ms(utterance.pcm)
        )
        return False

    async with SessionLocal() as db:
        row = TranscriptSegment(
            meeting_id=session.meeting_id,
            speaker_id=None,
            channel=utterance.channel,
            start_ms=utterance.start_ms,
            end_ms=utterance.end_ms,
            text=text,
            is_partial=False,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

    if needs_diarization:
        # Live diarization: fire-and-forget, never delays the transcript
        # itself. See app/workers/tasks.py:diarize_utterance and
        # _poll_diarization_updates below for how a label eventually comes
        # back. The dispatched audio is a wider window of already-received
        # audio on *this utterance's own channel*, not just this utterance
        # — diarize()'s pipeline needs several seconds of context to
        # reliably place a speaker-change point (verified empirically:
        # unreliable well under ~10s). session.recordings already
        # accumulates both channels regardless (needed for the final
        # mixdown either way), so this is just reading the matching one.
        settings = get_settings()
        window_start_ms = max(0, utterance.end_ms - settings.diarization_context_window_ms)
        channel_key = _CHANNEL_KEY[utterance.channel]
        window_pcm = extract_channel_window(
            session.recordings[channel_key], window_start_ms, utterance.end_ms
        )
        session.debug(
            "diarization_dispatched",
            segment_id=str(row.id),
            channel=utterance.channel.value,
            window_ms=utterance.end_ms - window_start_ms,
        )
        celery_app.send_task(
            "corella.diarize_utterance",
            args=[
                str(session.meeting_id),
                str(row.id),
                base64.b64encode(window_pcm).decode(),
                utterance.start_ms - window_start_ms,
                utterance.end_ms - utterance.start_ms,
                json.dumps([{"word": w.word, "start": w.start, "end": w.end} for w in words]),
            ],
        )

    try:
        await websocket.send_json(
            {
                "type": "transcript",
                "segment": {
                    "id": str(row.id),
                    "channel": utterance.channel.value,
                    "start_ms": utterance.start_ms,
                    "end_ms": utterance.end_ms,
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
            meeting.ended_at = datetime.now(timezone.utc)
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
