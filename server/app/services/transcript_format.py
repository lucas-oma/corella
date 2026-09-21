"""How a transcript line is labeled for hooks, the report prompt, and
the live copilot — Me vs Speaker 1/2/3, not the capture-channel byte.

Channel (me/them) is still how audio arrived. These names are who spoke.
"""

import re
from collections.abc import Sequence
from uuid import UUID

from app.models.meeting import CaptureMode, Channel, TranscriptSegment

_ANON_RE = re.compile(r"^(Speaker|Them) \d+$")


def _raw_label(segment: TranscriptSegment, owner_id: UUID, capture_mode: CaptureMode) -> str:
    if segment.linked_user_id is not None and segment.linked_user_id == owner_id:
        return "Me"
    if segment.speaker_label:
        return segment.speaker_label
    if capture_mode == CaptureMode.MEETING_TAB:
        if segment.channel == Channel.ME:
            return "Me"
        if segment.channel == Channel.THEM:
            return "Them"
    return "Unknown"


def _is_anonymous(label: str) -> bool:
    return label in {"Unknown", "Them", "Speaker"} or bool(_ANON_RE.match(label))


def display_labels(
    segments: Sequence[TranscriptSegment],
    *,
    owner_id: UUID,
    capture_mode: CaptureMode,
) -> list[str]:
    """Stable per-line names used by the transcript string and speaker share.

    The owner's enrolled voice is always Me. A meeting_tab mic line with
    no speaker yet is also Me (that pipe is known). Everyone else who
    only has a generic Speaker/Them/Unknown tag is remapped to Speaker 1,
    2, 3 in first-appearance order so two people on the same tab are not
    both "Them". Real names stay as-is. Open mic never falls back to Me
    from the channel alone.
    """
    aliases: dict[object, str] = {}
    next_n = 1
    labels: list[str] = []
    for segment in segments:
        raw = _raw_label(segment, owner_id, capture_mode)
        if raw == "Me":
            display = "Me"
        elif not _is_anonymous(raw):
            display = raw
        else:
            key: object = segment.speaker_id if segment.speaker_id is not None else (segment.channel.value, raw)
            if key not in aliases:
                aliases[key] = f"Speaker {next_n}"
                next_n += 1
            display = aliases[key]
        labels.append(display)
    return labels


def format_transcript(
    segments: Sequence[TranscriptSegment],
    *,
    owner_id: UUID,
    capture_mode: CaptureMode,
) -> str:
    """One `Name: text` line per segment, in the given order."""
    labels = display_labels(segments, owner_id=owner_id, capture_mode=capture_mode)
    return "\n".join(f"{label}: {segment.text}" for label, segment in zip(labels, segments, strict=True))


def speaker_share(
    segments: Sequence[TranscriptSegment],
    *,
    owner_id: UUID,
    capture_mode: CaptureMode,
) -> list[dict[str, str | int]] | None:
    """Talk share by display name (Me / real names / Speaker 1…N). None
    when there is no speech with a duration. Percents are rounded to
    integers and forced to sum to 100.
    """
    labels = display_labels(segments, owner_id=owner_id, capture_mode=capture_mode)
    ms_by_label: dict[str, int] = {}
    order: list[str] = []
    for segment, label in zip(segments, labels, strict=True):
        duration = max(0, segment.end_ms - segment.start_ms)
        if duration <= 0:
            continue
        if label not in ms_by_label:
            order.append(label)
            ms_by_label[label] = 0
        ms_by_label[label] += duration
    total = sum(ms_by_label.values())
    if total == 0 or not order:
        return None
    pcts = [round(ms_by_label[label] / total * 100) for label in order]
    pcts[-1] += 100 - sum(pcts)
    return [{"label": label, "pct": pct} for label, pct in zip(order, pcts, strict=True) if pct > 0]
