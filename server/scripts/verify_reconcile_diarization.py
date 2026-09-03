#!/usr/bin/env python
"""Real end-to-end verification of the periodic reconciliation redesign
(Phase W: app/workers/tasks.py:reconcile_diarization,
app/services/diarization/cluster.py's registry + guest-floor mechanism)
against the ACTUAL production code — real Postgres, real Redis, real Qdrant,
the real task function called directly (not via .delay(), so no broker
round-trip needed, but every line of app.workers.tasks.reconcile_diarization
really runs).

Simulates exactly the regression this phase was built to fix: real ground-
truth 2-speaker audio, chopped into many short (~0.5-0.85s) consecutive
TranscriptSegment rows — the aggressive-Deepgram-endpointing shape that
produced 8 spurious speakers for a real 2-person call before this fix. The
window handed to reconcile_diarization is a real, continuous slice of the
source WAV (not artificially spliced chops) — exactly what
_reconcile_diarization_loop would have sliced from session.recordings in
production.

Run inside the fv2-worker throwaway container:

    docker exec fv2-worker python scripts/verify_reconcile_diarization.py \
        --audio audio-samples/audio-1.wav --end-ms 6000 \
        --speaker-a 0-2440 --speaker-b 2920-5290
"""

import argparse
import base64
import wave
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SyncSessionLocal
from app.models.meeting import Channel, Meeting, MeetingStatus, Speaker, TranscriptSegment
from app.models.user import User
from app.workers.tasks import reconcile_diarization

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHOP_MS = 700  # short, consecutive, no gap — the aggressive-endpointing shape


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


def parse_range(s: str) -> tuple[int, int]:
    a, b = s.split("-")
    return int(a), int(b)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--end-ms", type=int, required=True, help="how much of the file to use as the window")
    parser.add_argument("--speaker-a", required=True, help="ground-truth range for speaker A, e.g. 0-2440")
    parser.add_argument("--speaker-b", required=True, help="ground-truth range for speaker B, e.g. 2920-5290")
    args = parser.parse_args()

    settings = get_settings()
    pcm = read_wav_mono16k(args.audio)
    a_start, a_end = parse_range(args.speaker_a)
    b_start, b_end = parse_range(args.speaker_b)

    with SyncSessionLocal() as db:
        user = User(
            id=uuid4(), email=f"verify-{uuid4().hex[:8]}@example.com", hashed_password="x", full_name="Verify User"
        )
        db.add(user)
        db.flush()
        meeting = Meeting(id=uuid4(), owner_id=user.id, title="verify-reconcile", status=MeetingStatus.RECORDING)
        db.add(meeting)
        db.commit()
        meeting_id = str(meeting.id)

        # Tile [0, end_ms) in short, gap-free chops — real ground-truth
        # ranges are respected (a chop never straddles the a_end/b_start
        # true speaker-change boundary), everything else is chopped blindly
        # every CHOP_MS regardless of what's really there, exactly
        # simulating aggressive endpointing that knows nothing about real
        # speaker turns.
        boundaries = sorted({0, a_start, a_end, b_start, b_end, args.end_ms})
        chops = []
        for lo, hi in zip(boundaries, boundaries[1:]):
            cur = lo
            while cur < hi:
                nxt = min(cur + CHOP_MS, hi)
                if nxt - cur >= 200:  # skip tiny slivers under 200ms
                    chops.append((cur, nxt))
                cur = nxt

        for start_ms, end_ms in chops:
            seg = TranscriptSegment(
                id=uuid4(),
                meeting_id=meeting.id,
                channel=Channel.ME,
                start_ms=start_ms,
                end_ms=end_ms,
                text=f"[{start_ms}-{end_ms}]",
                is_partial=False,
            )
            db.add(seg)
        db.commit()
        print(f"Created {len(chops)} short chopped TranscriptSegments over [0, {args.end_ms}ms):")
        for start_ms, end_ms in chops:
            truth = "A" if a_start <= start_ms < a_end else ("B" if b_start <= start_ms < b_end else "?")
            print(f"  [{start_ms:>6}-{end_ms:>6}ms] true={truth}")

    window_pcm = pcm[: args.end_ms * SAMPLE_RATE // 1000 * SAMPLE_WIDTH]
    print(f"\n--- calling real reconcile_diarization once, window=[0,{args.end_ms}ms] ---")
    reconcile_diarization(
        meeting_id=meeting_id,
        channel_value="me",
        window_pcm_b64=base64.b64encode(window_pcm).decode(),
        window_start_ms_abs=0,
    )

    with SyncSessionLocal() as db:
        segs = db.scalars(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting.id).order_by(TranscriptSegment.start_ms)
        ).all()
        speakers = db.scalars(select(Speaker).where(Speaker.meeting_id == meeting.id)).all()
        print(f"\n=== RESULT: {len(speakers)} real Speaker row(s) created ===")
        for s in segs:
            label = s.speaker.display_label if s.speaker else "UNRESOLVED"
            truth = "A" if a_start <= s.start_ms < a_end else ("B" if b_start <= s.start_ms < b_end else "?")
            print(f"  [{s.start_ms:>6}-{s.end_ms:>6}ms] true={truth} label={label:<15} {s.text!r}")

    import redis

    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    print(f"\nRedis diar:{meeting_id}:me -> {r.get(f'diar:{meeting_id}:me')}")
    print(f"Redis diar-pending:{meeting_id}:me -> {r.get(f'diar-pending:{meeting_id}:me')}")


if __name__ == "__main__":
    main()
