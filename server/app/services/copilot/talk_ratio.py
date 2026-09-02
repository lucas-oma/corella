from collections.abc import Sequence

from app.models.meeting import Channel, TranscriptSegment


def talk_ratio(segments: Sequence[TranscriptSegment]) -> dict[str, int]:
    """Real Me-vs-Them talk ratio, computed deterministically from
    persisted segment durations — not an LLM guess. {} channels (e.g. an
    uploaded/diarized meeting with no Me/Them split) yield 0/0.
    """
    me_ms = sum(s.end_ms - s.start_ms for s in segments if s.channel == Channel.ME)
    them_ms = sum(s.end_ms - s.start_ms for s in segments if s.channel == Channel.THEM)
    total = me_ms + them_ms
    if total == 0:
        return {"me": 0, "them": 0}
    return {"me": round(me_ms / total * 100), "them": round(them_ms / total * 100)}
