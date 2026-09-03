#!/usr/bin/env python
"""Real end-to-end verification of the shipped D+E fix (defer + mutual-
agreement promotion in app/workers/tasks.py / cluster.py) against the ACTUAL
production code path — real Postgres, real Redis, real Celery task function
called directly (not via .delay(), so no broker round-trip needed, but every
line of app.workers.tasks.diarize_utterance really runs).

Run inside the fv-worker throwaway container (see the isolated-infra setup
in chat), e.g.:

    docker exec fv-worker python scripts/verify_production_diarize.py \
        --audio audio-samples/audio-1.wav --cache scripts/.deepgram_cache/80ebadadb2a0dd0e.json

Creates a real throwaway User+Meeting, one TranscriptSegment per cached
Deepgram utterance (mirroring exactly what live_session.py does before
dispatching diarize_utterance), calls the real task function directly for
each in order, then reports the real resulting Speaker rows and Redis
cluster/pending state.
"""

import argparse
import base64
import json
import sys
import wave
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, "/srv")

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import SyncSessionLocal  # noqa: E402
from app.models.meeting import Channel, Meeting, MeetingStatus, Speaker, TranscriptSegment  # noqa: E402
from app.models.user import User  # noqa: E402
from app.workers.tasks import diarize_utterance  # noqa: E402

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2


def read_wav_mono16k(path: str) -> bytes:
    import os
    import subprocess
    import tempfile

    with wave.open(path, "rb") as wf:
        if wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == SAMPLE_RATE:
            return wf.readframes(wf.getnframes())
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", tmp],
            check=True,
            capture_output=True,
        )
        with wave.open(tmp, "rb") as wf:
            return wf.readframes(wf.getnframes())
    finally:
        os.unlink(tmp)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--cache", required=True, help="cached Deepgram utterances JSON")
    args = parser.parse_args()

    settings = get_settings()
    pcm = read_wav_mono16k(args.audio)
    data = json.loads(Path(args.cache).read_text())
    utterances = data["results"]["utterances"]
    print(f"{len(utterances)} utterances from {args.cache}")

    with SyncSessionLocal() as db:
        user = User(
            id=uuid4(),
            email=f"verify-{uuid4().hex[:8]}@example.com",
            hashed_password="x",
            full_name="Verify User",
        )
        db.add(user)
        db.flush()
        meeting = Meeting(id=uuid4(), owner_id=user.id, title="verify", status=MeetingStatus.RECORDING)
        db.add(meeting)
        db.commit()
        meeting_id = str(meeting.id)

        segment_ids = []
        for u in utterances:
            start_ms, end_ms = int(u["start"] * 1000), int(u["end"] * 1000)
            seg = TranscriptSegment(
                id=uuid4(),
                meeting_id=meeting.id,
                channel=Channel.ME,
                start_ms=start_ms,
                end_ms=end_ms,
                text=u["transcript"],
                is_partial=False,
            )
            db.add(seg)
            db.commit()
            segment_ids.append((str(seg.id), start_ms, end_ms, u["transcript"]))

    for seg_id, start_ms, end_ms, text in segment_ids:
        window_start_ms = max(0, end_ms - settings.diarization_context_window_ms)
        window_pcm = pcm[window_start_ms * SAMPLE_RATE // 1000 * SAMPLE_WIDTH : end_ms * SAMPLE_RATE // 1000 * SAMPLE_WIDTH]
        utterance_offset_ms = start_ms - window_start_ms
        utterance_duration_ms = end_ms - start_ms
        print(f"\n--- dispatching real diarize_utterance for [{start_ms}-{end_ms}ms] {text!r} ---")
        diarize_utterance(
            meeting_id=meeting_id,
            segment_id=seg_id,
            window_pcm_b64=base64.b64encode(window_pcm).decode(),
            utterance_offset_ms=utterance_offset_ms,
            utterance_duration_ms=utterance_duration_ms,
            words_json="[]",
        )

    with SyncSessionLocal() as db:
        segs = db.scalars(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting.id).order_by(TranscriptSegment.start_ms)
        ).all()
        speakers = db.scalars(select(Speaker).where(Speaker.meeting_id == meeting.id)).all()
        print(f"\n=== RESULT: {len(speakers)} real Speaker row(s) created ===")
        for s in segs:
            label = s.speaker.display_label if s.speaker else "UNRESOLVED"
            print(f"  [{s.start_ms:>6}-{s.end_ms:>6}ms] {label:<15} {s.text!r}")

    import redis

    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    cluster_raw = r.get(f"diar:{meeting_id}:me")
    pending_raw = r.get(f"diar-pending:{meeting_id}:me")
    print(f"\nRedis diar:{meeting_id}:me -> {cluster_raw}")
    print(f"Redis diar-pending:{meeting_id}:me -> {pending_raw}")


if __name__ == "__main__":
    main()
