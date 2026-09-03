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


def trailing_contiguous_ms(
    pcm: bytes, aggressiveness: int, silence_gap_ms: int = SILENCE_TO_FLUSH_MS, max_ms: int = 3000
) -> int:
    """How many ms, walking backward from the *end* of `pcm`, count as one
    continuous stretch of speech — natural pauses shorter than
    `silence_gap_ms` are tolerated (folded in as ordinary breathing room,
    same spirit as UtteranceDetector's own SILENCE_TO_FLUSH_MS), but the
    first real gap at least that long stops the count there. Capped at
    `max_ms` regardless.

    Used by app/workers/tasks.py:diarize_utterance to bound how far back a
    short utterance's speaker-identification embedding is allowed to widen
    into already-received same-channel audio — a wider window helps when
    it's genuinely more of the *same* speaker's speech, but reproduced live
    that a naive fixed-duration window can widen straight across a real
    pause into a *different* speaker's turn, blending two voices into one
    bad embedding. A detected silence gap is a much more direct signal for
    "this is where the previous speaker's turn likely was" than either a
    blind duration or the previous committed segment's own boundary (which
    collapses to zero extra context whenever utterances are dispatched
    back-to-back with no gap at all — the common case this whole widening
    exists for in the first place).
    """
    vad = webrtcvad.Vad(aggressiveness)
    max_frames = min(len(pcm) // FRAME_BYTES, max_ms // FRAME_MS)
    consecutive_silence_frames = 0
    usable_frames = 0
    for i in range(1, max_frames + 1):
        frame = pcm[len(pcm) - i * FRAME_BYTES : len(pcm) - (i - 1) * FRAME_BYTES]
        if vad.is_speech(frame, SAMPLE_RATE):
            consecutive_silence_frames = 0
        else:
            consecutive_silence_frames += 1
            if consecutive_silence_frames * FRAME_MS >= silence_gap_ms:
                break
        usable_frames = i
    return usable_frames * FRAME_MS
