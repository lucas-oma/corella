import asyncio
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
from app.models.user import User
from app.services.asr.whisper import transcribe, warm_up
from app.services.audio.mixing import mix_channel_recordings, write_wav
from app.services.vad.vad import UtteranceDetector

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

    def __init__(self, meeting_id: UUID):
        self.meeting_id = meeting_id
        self._start = time.monotonic()
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

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def on_audio(self, channel: Channel, pcm: bytes) -> None:
        """Feeds the VAD detector and enqueues any utterances it completes.
        Timestamps are captured now (flush time), not when the queue
        consumer eventually gets to them.
        """
        self.recordings[_CHANNEL_KEY[channel]].append((self.elapsed_ms(), pcm))
        for utterance_pcm in self.detectors[channel].feed(pcm):
            end_ms = self.elapsed_ms()
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

    # Load the model *before* saying "ready" — better a few extra seconds of
    # "Connecting…" on first use than a session that looks live but silently
    # stalls partway through waiting on a cold model load.
    await asyncio.get_running_loop().run_in_executor(None, warm_up)

    session = LiveSession(meeting_id)
    consumer_task = asyncio.create_task(_consume_utterances(websocket, session))

    await websocket.send_json({"type": "ready"})

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
            await _transcribe_and_send(websocket, session, utterance)
        except Exception:
            logger.exception("Live transcription failed for meeting %s", session.meeting_id)
        finally:
            session.queue.task_done()


async def _transcribe_and_send(websocket: WebSocket, session: LiveSession, utterance: _Utterance) -> None:
    text = await _transcribe_pcm(utterance.pcm)
    if not text:
        return

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


async def _transcribe_pcm(pcm: bytes) -> str:
    def _run() -> str:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            write_wav(path, pcm)
            segments = transcribe(path)
            return " ".join(s.text for s in segments).strip()
        finally:
            os.unlink(path)

    return await asyncio.get_running_loop().run_in_executor(None, _run)


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
