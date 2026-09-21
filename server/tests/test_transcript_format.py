"""Me vs Speaker N labeling for hooks / report / live copilot."""

from uuid import uuid4

from app.models.meeting import CaptureMode, Channel
from app.services.transcript_format import format_transcript, speaker_share


class _Seg:
    def __init__(
        self,
        text,
        *,
        channel=Channel.ME,
        speaker_label=None,
        linked_user_id=None,
        speaker_id=None,
        start_ms=0,
        end_ms=0,
    ):
        self.text = text
        self.channel = channel
        self.speaker_label = speaker_label
        self.linked_user_id = linked_user_id
        self.speaker_id = speaker_id
        self.start_ms = start_ms
        self.end_ms = end_ms


def test_open_mic_unlabeled_is_speaker_not_me():
    owner = uuid4()
    text = format_transcript(
        [_Seg("Hello there."), _Seg("How are you?")],
        owner_id=owner,
        capture_mode=CaptureMode.OPEN_MIC,
    )
    assert text == "Speaker 1: Hello there.\nSpeaker 1: How are you?"
    assert "Me:" not in text


def test_open_mic_owner_voice_is_me_others_are_speakers():
    owner = uuid4()
    text = format_transcript(
        [
            _Seg("I'll send it.", speaker_label="Lucas", linked_user_id=owner, speaker_id=uuid4()),
            _Seg("Thanks.", speaker_label="Speaker 2", speaker_id=uuid4()),
            _Seg("And legal.", speaker_label="Them 1", speaker_id=uuid4()),
        ],
        owner_id=owner,
        capture_mode=CaptureMode.OPEN_MIC,
    )
    assert text == "Me: I'll send it.\nSpeaker 1: Thanks.\nSpeaker 2: And legal."


def test_meeting_tab_unlabeled_mic_is_me():
    owner = uuid4()
    text = format_transcript(
        [
            _Seg("Can you hear me?", channel=Channel.ME),
            _Seg("Yes.", channel=Channel.THEM),
            _Seg("Also Ana.", channel=Channel.THEM, speaker_label="Ana", speaker_id=uuid4()),
        ],
        owner_id=owner,
        capture_mode=CaptureMode.MEETING_TAB,
    )
    assert text == "Me: Can you hear me?\nSpeaker 1: Yes.\nAna: Also Ana."


def test_speaker_share_none_on_meeting_tab():
    owner = uuid4()
    share = speaker_share(
        [
            _Seg("Hi.", channel=Channel.ME, start_ms=0, end_ms=1000),
            _Seg("Hello.", channel=Channel.THEM, start_ms=1000, end_ms=2000),
        ],
        owner_id=owner,
        capture_mode=CaptureMode.MEETING_TAB,
    )
    assert share is None


def test_speaker_share_open_mic_by_display_name():
    owner = uuid4()
    share = speaker_share(
        [
            _Seg("I'll send it.", speaker_label="Lucas", linked_user_id=owner, speaker_id=uuid4(), start_ms=0, end_ms=4000),
            _Seg("Thanks.", speaker_label="Speaker 2", speaker_id=uuid4(), start_ms=4000, end_ms=6000),
            _Seg("And legal.", speaker_label="Them 1", speaker_id=uuid4(), start_ms=6000, end_ms=8000),
        ],
        owner_id=owner,
        capture_mode=CaptureMode.OPEN_MIC,
    )
    assert share == [
        {"label": "Me", "pct": 50},
        {"label": "Speaker 1", "pct": 25},
        {"label": "Speaker 2", "pct": 25},
    ]
    assert sum(row["pct"] for row in share) == 100


def test_speaker_share_none_without_durations():
    owner = uuid4()
    share = speaker_share(
        [_Seg("Hello there."), _Seg("How are you?")],
        owner_id=owner,
        capture_mode=CaptureMode.OPEN_MIC,
    )
    assert share is None
