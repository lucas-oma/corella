import webrtcvad

SAMPLE_RATE = 16000
FRAME_MS = 30
BYTES_PER_SAMPLE = 2
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * BYTES_PER_SAMPLE  # 960

# How long a pause has to last before we consider the utterance finished and
# flush it for transcription. Short enough to feel responsive, long enough
# not to chop a sentence on a normal breath.
SILENCE_TO_FLUSH_MS = 500


class UtteranceDetector:
    """Consumes an arbitrary stream of 16kHz mono PCM16LE bytes (chunks of
    any size — callers don't need to align to VAD frame boundaries) and
    decides when a spoken utterance is complete: either a detected pause,
    or a safety cap so continuous speech still gets transcribed
    periodically instead of buffering forever.
    """

    def __init__(self, aggressiveness: int, max_utterance_seconds: int, min_utterance_ms: int):
        self._vad = webrtcvad.Vad(aggressiveness)
        self._max_utterance_ms = max_utterance_seconds * 1000
        self._min_utterance_ms = min_utterance_ms
        self._leftover = b""
        self._utterance = bytearray()
        self._silence_ms = 0
        self._speech_ms = 0

    def feed(self, pcm: bytes) -> list[bytes]:
        """Feed more audio in; returns zero or more completed utterances
        (a chunk large enough to span multiple pauses can yield more than
        one)."""
        self._leftover += pcm
        flushed: list[bytes] = []

        while len(self._leftover) >= FRAME_BYTES:
            frame = self._leftover[:FRAME_BYTES]
            self._leftover = self._leftover[FRAME_BYTES:]

            if self._vad.is_speech(frame, SAMPLE_RATE):
                self._utterance += frame
                self._speech_ms += FRAME_MS
                self._silence_ms = 0
            elif self._speech_ms > 0:
                # Keep a little trailing silence in the clip — natural
                # cadence, and gives whisper's own VAD room to work with.
                self._utterance += frame
                self._silence_ms += FRAME_MS

            paused = self._speech_ms >= self._min_utterance_ms and (
                self._silence_ms >= SILENCE_TO_FLUSH_MS
            )
            capped = self._speech_ms >= self._max_utterance_ms
            if paused or capped:
                flushed.append(bytes(self._utterance))
                self._reset()

        return flushed

    def peek_current_utterance(self) -> bytes | None:
        """A copy of the in-progress (not yet flushed) utterance buffer, for
        a live rolling-preview decode (app/ws/live_session.py) — a read
        only, never consumes or resets any state, unlike feed()/
        flush_remaining(). None if there isn't yet enough speech to be worth
        decoding, same live_min_utterance_ms floor the real flush uses.
        """
        if self._speech_ms < self._min_utterance_ms:
            return None
        return bytes(self._utterance)

    def flush_remaining(self) -> bytes | None:
        """Called at session end — return any in-progress utterance even
        without trailing silence, as long as it's long enough to bother
        transcribing."""
        result = bytes(self._utterance) if self._speech_ms >= self._min_utterance_ms else None
        self._reset()
        return result

    def _reset(self) -> None:
        self._utterance = bytearray()
        self._silence_ms = 0
        self._speech_ms = 0
