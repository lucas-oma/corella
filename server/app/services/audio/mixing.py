import wave

import numpy as np

SAMPLE_RATE = 16000


def mix_channel_recordings(
    recordings: dict[str, list[tuple[int, bytes]]], sample_rate: int = SAMPLE_RATE
) -> bytes:
    """Overlay one or more channels' timestamped PCM16LE chunks into a
    single mono PCM16LE buffer — like a real call recording, both sides
    audible together, rather than separate tracks. `recordings` maps
    channel name -> list of (arrival_offset_ms, chunk_bytes) in the order
    received; offsets come from wall-clock arrival time, not a shared
    sample clock, so this doesn't assume perfectly gap-free channels.
    """
    if not any(chunks for chunks in recordings.values()):
        return b""

    total_ms = 0
    for chunks in recordings.values():
        for offset_ms, chunk in chunks:
            duration_ms = int(len(chunk) / 2 / sample_rate * 1000)
            total_ms = max(total_ms, offset_ms + duration_ms)

    total_samples = int(total_ms / 1000 * sample_rate) + sample_rate // 5  # +200ms rounding margin
    mixed = np.zeros(total_samples, dtype=np.int32)

    for chunks in recordings.values():
        for offset_ms, chunk in chunks:
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.int32)
            start = int(offset_ms / 1000 * sample_rate)
            end = start + len(samples)
            if end > len(mixed):
                samples = samples[: len(mixed) - start]
                end = len(mixed)
            mixed[start:end] += samples

    return np.clip(mixed, -32768, 32767).astype(np.int16).tobytes()


def extract_channel_window(
    chunks: list[tuple[int, bytes]], start_ms: int, end_ms: int, sample_rate: int = SAMPLE_RATE
) -> bytes:
    """A bounded slice of one channel's timestamped chunks as contiguous
    mono PCM16LE, [start_ms, end_ms) — silence-padded over any gaps.

    Used to give same-room diarization (app/workers/tasks.py:diarize_utterance)
    a wider window of *already-received* audio than just one VAD utterance —
    the pyannote pipeline needs several seconds of context to reliably place
    a speaker-change point (verified empirically: unreliable well under 10s).
    Reuses the same recordings buffer mix_channel_recordings() reads at
    session end, just for one channel and a bounded range instead of the
    whole session.
    """
    if end_ms <= start_ms:
        return b""

    total_samples = int((end_ms - start_ms) / 1000 * sample_rate)
    window = np.zeros(total_samples, dtype=np.int16)

    for offset_ms, chunk in chunks:
        chunk_duration_ms = int(len(chunk) / 2 / sample_rate * 1000)
        if offset_ms + chunk_duration_ms <= start_ms or offset_ms >= end_ms:
            continue
        samples = np.frombuffer(chunk, dtype=np.int16)
        dest_start = int((offset_ms - start_ms) / 1000 * sample_rate)
        src_start = max(0, -dest_start)
        dest_start = max(0, dest_start)
        dest_end = min(len(window), dest_start + len(samples) - src_start)
        if dest_end <= dest_start:
            continue
        window[dest_start:dest_end] = samples[src_start : src_start + (dest_end - dest_start)]

    return window.tobytes()


def slice_pcm(pcm: bytes, start_ms: int, duration_ms: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    """A [start_ms, start_ms + duration_ms) byte-offset slice of a mono
    PCM16LE buffer, clamped to the buffer's actual bounds."""
    start_sample = max(0, int(start_ms / 1000 * sample_rate))
    end_sample = max(start_sample, int((start_ms + duration_ms) / 1000 * sample_rate))
    start_byte = start_sample * 2
    end_byte = min(len(pcm), end_sample * 2)
    return pcm[start_byte:end_byte]


def write_wav(path: str, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def read_wav_pcm(path: str) -> bytes:
    """The inverse of write_wav — raw PCM16LE frames from a mono WAV file,
    for feeding into embed_utterance()/transcribe() style functions that
    take raw PCM rather than a file path. Used by voice enrollment
    (corella.enroll_voice), which normalizes an arbitrary upload to WAV via
    ffmpeg first, same as every other audio-ingestion path.
    """
    with wave.open(path, "rb") as wf:
        return wf.readframes(wf.getnframes())
