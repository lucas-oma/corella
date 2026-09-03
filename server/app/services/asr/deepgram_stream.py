import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import websockets

from app.services.asr.whisper import WhisperWord

logger = logging.getLogger(__name__)

WS_URL = "wss://api.deepgram.com/v1/listen"

# Deepgram's own endpointing needs a real gap to trigger `speech_final` — a
# value in the same neighborhood as vad.py's SILENCE_TO_FLUSH_MS (500ms),
# not a separate unverified guess. `UtteranceEnd` (enabled via vad_events)
# is a second, independent safety net using this same window in case a
# chunk boundary ever lands such that speech_final doesn't fire on its own
# — see _read_loop/_finalize_utterance.
UTTERANCE_END_MS = "1000"


class DeepgramStreamError(Exception):
    """Raised only by connect() — a failure to even open the socket. Once
    open, later failures are reported through the on_closed callback
    instead (fire-and-forget audio frames can't raise back to the caller
    that queued them).
    """


@dataclass
class StreamResult:
    text: str
    is_final: bool
    # Utterance-relative seconds (matching whisper.py's WhisperWord contract
    # exactly, so app/workers/tasks.py:diarize_utterance's words_in_range
    # doesn't need to know which engine produced them) — only ever
    # populated on a final result; interim previews carry none.
    words: list[WhisperWord] = field(default_factory=list)
    start_s: float = 0.0  # stream-relative seconds — only meaningful when is_final
    duration_s: float = 0.0  # seconds — only meaningful when is_final


class DeepgramLiveStream:
    """One persistent Deepgram streaming connection for one live-session
    channel (Me or Them) — opened once at session start (or right after a
    fallback recovery, never mid-utterance) and fed raw PCM for the rest of
    the session; Deepgram does its own voice-activity endpointing, so no
    local VAD runs for a channel while its stream stays healthy (see
    app/ws/live_session.py's LiveSession.on_audio).

    `on_result` fires for every interim (is_final=False — a disposable,
    more-frequent preview, the same shape the local-whisper rolling preview
    produces) and final (is_final=True, speech_final-bounded) result.
    `on_closed` fires at most once, only if the stream fails or drops
    *after* connecting successfully — never on a deliberate close() — so the
    caller can fall this one channel back to local VAD/whisper for the rest
    of the session without touching the other channel's own stream (the
    same per-channel-independence property this project's earlier
    graceful-degradation paths already establish).
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        language: str,
        on_result: Callable[[StreamResult], Awaitable[None]],
        on_closed: Callable[[], Awaitable[None]],
    ):
        self._api_key = api_key
        self._model = model
        self._language = language
        self._on_result = on_result
        self._on_closed = on_closed

        self._ws: websockets.ClientConnection | None = None
        # bytes = an audio chunk, "__close__" = send the documented
        # CloseStream control message, None = stop the writer loop. Routed
        # through one queue/one writer task so audio and the close message
        # are always written to the socket strictly in order, and never
        # concurrently with each other — websockets' own send() isn't safe
        # to call from two coroutines at once.
        self._send_queue: asyncio.Queue[bytes | str | None] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._writer_task: asyncio.Task | None = None
        self._closing = False
        self._closed_fired = False

        # Current-utterance accumulation, reset in _finalize_utterance.
        # Deepgram doesn't hand back one message with the whole utterance's
        # text — with interim_results on, each *final* (is_final=true)
        # message only covers its own newest chunk; the real utterance
        # boundary is speech_final=true (or UtteranceEnd), at which point
        # every finalized chunk since the last boundary is joined together.
        self._final_chunks: list[str] = []
        self._final_words: list[WhisperWord] = []
        self._utterance_start_s: float | None = None
        self._utterance_end_s: float | None = None

    async def connect(self) -> None:
        params = {
            "model": self._model,
            "language": self._language,
            "encoding": "linear16",
            "sample_rate": "16000",
            "channels": "1",
            "interim_results": "true",
            "punctuate": "true",
            "smart_format": "true",
            "vad_events": "true",
            "utterance_end_ms": UTTERANCE_END_MS,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        try:
            self._ws = await websockets.connect(
                f"{WS_URL}?{query}", additional_headers={"Authorization": f"Token {self._api_key}"}
            )
        except Exception as e:
            raise DeepgramStreamError(f"Deepgram stream connect failed: {e}") from e
        self._reader_task = asyncio.create_task(self._read_loop())
        self._writer_task = asyncio.create_task(self._write_loop())

    def send(self, pcm: bytes) -> None:
        """Sync — called directly from LiveSession.on_audio, same calling
        convention as the local VAD detector's own feed(). Queued, not sent
        inline, so the actual socket write always happens on the one
        dedicated writer task below."""
        self._send_queue.put_nowait(pcm)

    async def close(self) -> None:
        """Sends the documented CloseStream message and gives Deepgram a
        real chance to flush and finalize any still-buffered utterance in
        response (the reader loop is what processes that flush) before
        tearing the connection down — a plain immediate disconnect would
        silently drop whatever the caller was mid-saying when the call
        ended.
        """
        self._closing = True
        self._send_queue.put_nowait("__close__")
        self._send_queue.put_nowait(None)
        if self._writer_task is not None:
            try:
                await asyncio.wait_for(self._writer_task, timeout=2.0)
            except Exception:
                pass
        if self._reader_task is not None:
            try:
                await asyncio.wait_for(self._reader_task, timeout=5.0)
            except Exception:
                pass
            self._reader_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _write_loop(self) -> None:
        try:
            while True:
                item = await self._send_queue.get()
                if item is None:
                    return
                try:
                    if item == "__close__":
                        await self._ws.send(json.dumps({"type": "CloseStream"}))
                    else:
                        await self._ws.send(item)
                except Exception:
                    await self._fail()
                    return
        except asyncio.CancelledError:
            pass

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = msg.get("type")
                if msg_type == "Results":
                    await self._handle_results(msg)
                elif msg_type == "UtteranceEnd":
                    await self._finalize_utterance()
            # The async iterator above ends cleanly (no exception) when
            # Deepgram closes the socket — whether that's the documented
            # response to our own CloseStream (self._closing already true,
            # _fail() below correctly no-ops) or an unexpected drop.
            if not self._closing:
                await self._fail()
        except asyncio.CancelledError:
            pass
        except Exception:
            if not self._closing:
                logger.exception("Deepgram live stream reader failed")
                await self._fail()

    async def _fail(self) -> None:
        if self._closed_fired or self._closing:
            return
        self._closed_fired = True
        try:
            await self._on_closed()
        except Exception:
            logger.exception("Deepgram stream on_closed callback failed")

    async def _handle_results(self, msg: dict) -> None:
        try:
            alt = msg["channel"]["alternatives"][0]
        except (KeyError, IndexError, TypeError):
            return
        text = (alt.get("transcript") or "").strip()
        is_final = bool(msg.get("is_final"))
        speech_final = bool(msg.get("speech_final"))
        start = float(msg.get("start") or 0.0)
        duration = float(msg.get("duration") or 0.0)

        if self._utterance_start_s is None:
            self._utterance_start_s = start
        self._utterance_end_s = start + duration

        if is_final:
            if text:
                base = self._utterance_start_s
                self._final_chunks.append(text)
                self._final_words.extend(
                    WhisperWord(
                        start=float(w.get("start") or 0.0) - base,
                        end=float(w.get("end") or 0.0) - base,
                        word=w.get("word") or "",
                    )
                    for w in (alt.get("words") or [])
                )
            if speech_final:
                await self._finalize_utterance()
            return

        # Interim — a disposable preview of the whole utterance so far:
        # already-finalized chunks plus this newest, still-revisable one.
        if text:
            preview = " ".join([*self._final_chunks, text]).strip()
            if preview:
                await self._on_result(StreamResult(text=preview, is_final=False))

    async def _finalize_utterance(self) -> None:
        text = " ".join(self._final_chunks).strip()
        words = self._final_words
        start_s = self._utterance_start_s or 0.0
        duration_s = max(0.0, (self._utterance_end_s or start_s) - start_s)
        self._final_chunks = []
        self._final_words = []
        self._utterance_start_s = None
        self._utterance_end_s = None
        if not text:
            return
        await self._on_result(
            StreamResult(text=text, is_final=True, words=words, start_s=start_s, duration_s=duration_s)
        )
