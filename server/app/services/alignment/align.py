from dataclasses import dataclass

from app.services.asr.whisper import WhisperSegment
from app.services.diarization.pyannote import DiarizationTurn


@dataclass
class AlignedSegment:
    start_ms: int
    end_ms: int
    text: str
    speaker: str | None  # diarization label (e.g. "SPEAKER_00"); None if unavailable


def align(
    whisper_segments: list[WhisperSegment], diarization_turns: list[DiarizationTurn]
) -> list[AlignedSegment]:
    """Assign each Whisper segment the diarization speaker it overlaps with
    the most. Segments with no positive overlap (or when diarization didn't
    run at all) get `speaker=None`.
    """
    return [
        AlignedSegment(
            start_ms=round(seg.start * 1000),
            end_ms=round(seg.end * 1000),
            text=seg.text,
            speaker=_best_overlapping_speaker(seg, diarization_turns),
        )
        for seg in whisper_segments
    ]


def _best_overlapping_speaker(seg: WhisperSegment, turns: list[DiarizationTurn]) -> str | None:
    best_speaker: str | None = None
    best_overlap = 0.0
    for turn in turns:
        overlap = min(seg.end, turn.end) - max(seg.start, turn.start)
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn.speaker
    return best_speaker
