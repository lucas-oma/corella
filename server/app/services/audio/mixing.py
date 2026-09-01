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


def write_wav(path: str, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
