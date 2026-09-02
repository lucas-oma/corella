import httpx

from app.services.asr.whisper import WhisperSegment, WhisperWord

API_URL = "https://api.deepgram.com/v1/listen"


class SttError(Exception):
    """Raised by any STT client on a request failure — auth, rate limit,
    network, or an unexpected response shape. Callers treat this the same
    way LLMError is already treated: log and fall back / skip, never crash
    the pipeline over one failed transcription call.
    """


async def transcribe(
    pcm_wav_bytes: bytes, model: str, api_key: str | None, word_timestamps: bool = False
) -> list[WhisperSegment]:
    """Deepgram's prerecorded /v1/listen REST endpoint — used for both
    upload processing and live per-utterance transcription (never their
    real-time streaming WS protocol): both call sites already only ever
    need "transcribe this one already-complete chunk of audio" (a whole
    uploaded file, or one VAD-flushed live utterance), exactly matching
    how whisper.transcribe() is already invoked, so the simpler one-shot
    REST endpoint is a real drop-in with no new integration risk from a
    second, more complex protocol.

    Hand-rolled against the documented REST shape rather than an SDK, same
    rationale as the OpenAI/Gemini LLM clients (app/services/llm/) — no
    live key to verify SDK specifics against in this environment. Returns
    the same WhisperSegment/WhisperWord shape whisper.py produces, so
    nothing downstream (align.py, live_session.py) needs to know which
    engine actually ran.
    """
    if not api_key:
        raise SttError("No Deepgram API key configured")

    params = {
        "model": model,
        "smart_format": "true",
        "punctuate": "true",
        "utterances": "true",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                API_URL,
                params=params,
                headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"},
                content=pcm_wav_bytes,
            )
    except httpx.RequestError as e:
        raise SttError(f"Deepgram connection error: {e}") from e

    if response.status_code == 401:
        raise SttError("Deepgram authentication failed")
    if response.status_code == 429:
        raise SttError("Deepgram rate limited")
    if response.status_code >= 400:
        raise SttError(f"Deepgram API error ({response.status_code}): {response.text[:500]}")

    try:
        data = response.json()
        alternative = data["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError, ValueError) as e:
        raise SttError(f"Unexpected Deepgram response shape: {e}") from e

    utterances = data.get("results", {}).get("utterances")
    if utterances:
        return [_segment_from_utterance(u, word_timestamps) for u in utterances if u.get("transcript")]

    # utterances=true is requested but not every plan/model combination
    # returns it — fall back to the one whole-file alternative as a
    # single segment, same shape a very short whisper clip would produce.
    text = (alternative.get("transcript") or "").strip()
    if not text:
        return []
    words = _words(alternative.get("words") or []) if word_timestamps else []
    start = words[0].start if words else 0.0
    end = words[-1].end if words else 0.0
    return [WhisperSegment(start=start, end=end, text=text, words=words)]


def _segment_from_utterance(utterance: dict, word_timestamps: bool) -> WhisperSegment:
    text = (utterance.get("transcript") or "").strip()
    words = _words(utterance.get("words") or []) if word_timestamps else []
    return WhisperSegment(
        start=float(utterance.get("start") or 0.0),
        end=float(utterance.get("end") or 0.0),
        text=text,
        words=words,
    )


def _words(raw_words: list[dict]) -> list[WhisperWord]:
    return [
        WhisperWord(start=float(w.get("start") or 0.0), end=float(w.get("end") or 0.0), word=w.get("word") or "")
        for w in raw_words
    ]
