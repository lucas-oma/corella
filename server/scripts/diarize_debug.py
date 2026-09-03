#!/usr/bin/env python
"""Standalone, non-Docker replay of the same-room diarization decision flow
(app/workers/tasks.py:diarize_utterance), against a real WAV file, for fast
local debugging of the "too many speakers" issue.

Run from server/ with the local venv (torch/pyannote installed there — see
`pip install -e ".[worker,dev]"`), e.g.:

    .venv/bin/python scripts/diarize_debug.py ../audio-samples/audio-3.wav
    .venv/bin/python scripts/diarize_debug.py ../audio-samples/audio-4.wav --chunker deepgram

No Docker, no Postgres, no Redis: clusters are tracked in a plain in-memory
list across utterances (exactly what locked_state's Redis-backed list holds
in production — the clustering math itself, best_match/update_centroid, is
reused unmodified from app.services.diarization.cluster). Only the DB-backed
bits (Speaker rows, VoiceIdentity lookup, WS event push, backfill against
Postgres-stored segments) are stubbed out — this is about diarization
QUALITY (how many real speakers get created and why), not the surrounding
persistence plumbing.

Two utterance-chunking modes, mirroring the two real production paths:
  --chunker vad       local webrtcvad UtteranceDetector (what local-whisper
                       sessions use) — SILENCE_TO_FLUSH_MS-gated pauses.
  --chunker deepgram   real Deepgram prerecorded /v1/listen call with
                       utterances=true (real network call, real API key from
                       the repo-root .env) — reproduces the actual shorter/
                       more-frequent chunking Deepgram's own endpointing
                       produces, which is what's actually running live today
                       and the presumed source of the over-segmentation bug.

For each utterance, prints: its own whole-embedding best-match score, the
skip-confidence/needs-second-look/corroboration decisions exactly as
diarize_utterance makes them, whether the (real) full diarize() split pass
ran, and the resulting cluster assignment — then a final summary of how many
speakers were created and, for each, what triggered its creation.
"""

import argparse
import json
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader — the real .env lives at the repo root, not
    server/ (where pydantic-settings' env_file=".env" would look, relative
    to cwd), so this project's own Settings() would silently see nothing
    unless we push the repo-root file's values into os.environ first.
    Never overrides an already-set env var.
    """
    import os

    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(SERVER_DIR))

import numpy as np  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.audio.mixing import is_clipped, slice_pcm  # noqa: E402
from app.services.diarization.cluster import (  # noqa: E402
    SIMILARITY_THRESHOLD,
    Cluster,
    best_match,
    update_centroid,
)
from app.services.diarization.embedding import embed_utterance  # noqa: E402
from app.services.diarization.pyannote import DiarizationUnavailable, diarize  # noqa: E402
from app.services.vad.vad import (  # noqa: E402
    FRAME_BYTES,
    SAMPLE_RATE,
    UtteranceDetector,
    speech_ms,
    trailing_contiguous_ms,
)

SAMPLE_WIDTH = 2  # bytes, int16 mono


def read_wav(path: str) -> bytes:
    """Mono 16kHz PCM16LE frames — auto-converts via ffmpeg (same tool the
    real upload pipeline already normalizes with) if the source file isn't
    already in that shape, so any real sample can be passed as-is."""
    with wave.open(path, "rb") as wf:
        needs_convert = wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != SAMPLE_RATE
        if not needs_convert:
            return wf.readframes(wf.getnframes())

    import subprocess
    import tempfile

    print(f"Converting {path} to mono {SAMPLE_RATE}Hz PCM16 via ffmpeg...", file=sys.stderr)
    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    import os

    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", tmp_path],
            check=True,
            capture_output=True,
        )
        with wave.open(tmp_path, "rb") as wf:
            return wf.readframes(wf.getnframes())
    finally:
        os.unlink(tmp_path)


def ms_to_byte_offset(ms: int) -> int:
    return int(ms / 1000 * SAMPLE_RATE) * SAMPLE_WIDTH


@dataclass
class Utterance:
    start_ms: int
    end_ms: int


def chunk_via_vad(pcm: bytes, settings) -> list[Utterance]:
    """Feed the whole file through the real UtteranceDetector in small
    (~200ms) increments, same as live_session.py's on_audio does per
    incoming chunk — produces the same boundaries a local-whisper live
    session would."""
    detector = UtteranceDetector(
        aggressiveness=settings.live_vad_aggressiveness,
        max_utterance_seconds=settings.live_max_utterance_seconds,
        min_utterance_ms=settings.live_min_utterance_ms,
    )
    step_bytes = FRAME_BYTES * 7  # ~210ms per feed, arbitrary but small
    utterances: list[Utterance] = []
    cursor_ms = 0
    for i in range(0, len(pcm), step_bytes):
        chunk = pcm[i : i + step_bytes]
        chunk_ms = len(chunk) // SAMPLE_WIDTH / SAMPLE_RATE * 1000
        for flushed in detector.feed(chunk):
            flushed_ms = len(flushed) // SAMPLE_WIDTH / SAMPLE_RATE * 1000
            end_ms = int(cursor_ms + chunk_ms)
            start_ms = int(end_ms - flushed_ms)
            utterances.append(Utterance(start_ms=max(0, start_ms), end_ms=end_ms))
        cursor_ms += chunk_ms
    remaining = detector.flush_remaining()
    if remaining:
        remaining_ms = len(remaining) // SAMPLE_WIDTH / SAMPLE_RATE * 1000
        end_ms = int(cursor_ms)
        utterances.append(Utterance(start_ms=max(0, int(end_ms - remaining_ms)), end_ms=end_ms))
    return utterances


def chunk_via_deepgram(path: str, settings) -> list[Utterance]:
    """A real prerecorded Deepgram call with utterances=true — the actual
    service driving live production today, so its utterance boundaries are
    the most faithful reproduction of the real over-segmentation reports
    available without a live WS session."""
    import hashlib

    import httpx

    with open(path, "rb") as f:
        audio_bytes = f.read()

    # Cache by content hash — Deepgram's utterance boundaries are
    # deterministic for the same audio+params, and hitting the real API on
    # every fix-hypothesis re-run while iterating is pure waste.
    cache_dir = Path(__file__).resolve().parent / ".deepgram_cache"
    cache_dir.mkdir(exist_ok=True)
    cache_key = hashlib.sha256(audio_bytes).hexdigest()[:16]
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        print(f"Using cached Deepgram response ({cache_path.name})...", file=sys.stderr)
        data = json.loads(cache_path.read_text())
    else:
        api_key = settings.deepgram_api_key
        if not api_key:
            print(
                "ERROR: DEEPGRAM_API_KEY not set (checked repo-root .env) — can't use --chunker deepgram",
                file=sys.stderr,
            )
            sys.exit(1)
        print("Calling real Deepgram prerecorded API (utterances=true)...", file=sys.stderr)
        resp = httpx.post(
            "https://api.deepgram.com/v1/listen",
            params={
                "model": settings.default_model_deepgram,
                "language": "multi",
                "smart_format": "true",
                "punctuate": "true",
                "utterances": "true",
            },
            headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"},
            content=audio_bytes,
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        cache_path.write_text(json.dumps(data))
    utterances_json = data["results"].get("utterances", [])
    utterances = [Utterance(start_ms=int(u["start"] * 1000), end_ms=int(u["end"] * 1000)) for u in utterances_json]
    for u, raw in zip(utterances, utterances_json, strict=False):
        print(f"  deepgram utterance [{u.start_ms:>6}ms-{u.end_ms:>6}ms]: {raw['transcript']!r}", file=sys.stderr)
    return utterances


def _merge_adjacent_same_speaker(turns) -> list[tuple[float, float, str]]:
    merged: list[tuple[float, float, str]] = []
    for t in turns:
        if merged and merged[-1][2] == t.speaker:
            merged[-1] = (merged[-1][0], t.end, t.speaker)
        else:
            merged.append((t.start, t.end, t.speaker))
    return merged


def cluster_and_assign(
    clusters: list[Cluster],
    embedding,
    fallback_embedding=None,
    last_speaker_idx: int | None = None,
    last_speaker_threshold: float | None = None,
) -> tuple[int, bool, str]:
    """Mirrors app/workers/tasks.py:_cluster_and_assign, minus the
    Speaker/VoiceIdentity DB rows — returns (cluster_index, is_new,
    reason_label). `last_speaker_idx`/`last_speaker_threshold` are the
    fix-hypothesis-B addition — see run()'s docstring."""
    idx, similarity = best_match(clusters, embedding)
    used_embedding = embedding
    reason = f"primary sim={similarity:.3f}"
    matched = idx is not None and similarity >= SIMILARITY_THRESHOLD

    if not matched and fallback_embedding is not None:
        fb_idx, fb_similarity = best_match(clusters, fallback_embedding)
        if fb_idx is not None and fb_similarity >= SIMILARITY_THRESHOLD:
            idx, similarity, used_embedding = fb_idx, fb_similarity, fallback_embedding
            reason = f"corroboration sim={similarity:.3f}"
            matched = True

    if (
        not matched
        and last_speaker_idx is not None
        and last_speaker_threshold is not None
        and last_speaker_idx < len(clusters)
    ):
        centroid = np.array(clusters[last_speaker_idx].centroid)
        ls_similarity = float(np.dot(embedding, centroid) / (np.linalg.norm(embedding) * np.linalg.norm(centroid)))
        if ls_similarity >= last_speaker_threshold:
            idx, similarity, used_embedding = last_speaker_idx, ls_similarity, embedding
            reason = f"last-speaker sim={similarity:.3f}"
            matched = True

    if matched:
        update_centroid(clusters[idx], used_embedding)
        return idx, False, reason

    clusters.append(Cluster(centroid=used_embedding.tolist(), count=1, speaker_id=f"speaker-{len(clusters) + 1}"))
    return len(clusters) - 1, True, reason


def run(
    pcm: bytes,
    utterances: list[Utterance],
    settings,
    label: str,
    legacy_narrow_trigger: bool = False,
    last_speaker_threshold: float | None = None,
) -> None:
    """`legacy_narrow_trigger`: reproduce the pre-fix, content-thinness-only
    second-look gate this project shipped and then replaced — for A/B
    comparison only. Default (False) matches current production exactly.

    `last_speaker_threshold`: an experimental fix for the still-open
    "thin utterance right after a real pause" gap — if neither the primary
    nor the corroboration embedding confidently matches anything, try ONE
    more comparison against specifically the most-recently-assigned cluster
    (not every cluster) at this lower, dedicated bar — temporal adjacency as
    weak-but-real evidence, per the user's original brainstorm proposal.
    **Verified UNSAFE as a pure-threshold approach**: on real audio-1.wav
    ground truth, a threshold that correctly rescued a real same-speaker
    utterance (0.409) also incorrectly merged two different real speakers
    at 0.403 — same-speaker-weak and different-speaker-weak aren't
    separable by raw score alone at this signal level. NOT shipped to
    production. None disables this (matches production).
    """
    clusters: list[Cluster] = []
    creation_log: list[dict] = []
    last_active_idx: int | None = None

    print(f"\n=== {label}: {len(utterances)} utterances ===")
    for i, u in enumerate(utterances):
        window_start_ms = max(0, u.end_ms - settings.diarization_context_window_ms)
        window_pcm = pcm[ms_to_byte_offset(window_start_ms) : ms_to_byte_offset(u.end_ms)]
        utterance_offset_ms = u.start_ms - window_start_ms
        utterance_duration_ms = u.end_ms - u.start_ms
        utterance_pcm = slice_pcm(window_pcm, utterance_offset_ms, utterance_duration_ms)

        if not utterance_pcm:
            print(f"[{i:>3}] {u.start_ms:>7}-{u.end_ms:>7}ms  SKIP (empty audio slice)")
            continue

        t0 = time.time()
        whole_embedding = embed_utterance(utterance_pcm)
        embed_s = time.time() - t0

        best_idx, best_sim = best_match(clusters, whole_embedding)
        confident_single_speaker = best_sim >= settings.diarization_skip_confidence

        utterance_clipped = is_clipped(utterance_pcm)
        has_existing_clusters = best_sim > -1.0
        content_ms = speech_ms(utterance_pcm, settings.live_vad_aggressiveness)
        if legacy_narrow_trigger:
            # Pre-fix behavior, for A/B comparison only — the content-
            # thinness-only gate this project shipped and then replaced
            # (removed from Settings entirely; 1500ms hardcoded here just
            # to reproduce the old gate's own former value).
            too_little_content = content_ms < 1500
            needs_second_look = utterance_clipped or (too_little_content and has_existing_clusters)
        else:
            # Current shipped behavior (app/workers/tasks.py) — any miss
            # against an existing cluster warrants a second look, not just
            # a thin one.
            needs_second_look = utterance_clipped or (has_existing_clusters and best_sim < SIMILARITY_THRESHOLD)

        fallback_embedding = None
        insufficient_signal = False
        corroboration_note = ""
        if needs_second_look and best_sim < SIMILARITY_THRESHOLD:
            contiguous_ms = trailing_contiguous_ms(
                window_pcm, settings.live_vad_aggressiveness, max_ms=settings.diarization_corroboration_window_ms
            )
            utterance_end_within_window_ms = utterance_offset_ms + utterance_duration_ms
            corroboration_start_ms = max(0, utterance_end_within_window_ms - contiguous_ms)
            corroboration_pcm = slice_pcm(
                window_pcm, corroboration_start_ms, utterance_end_within_window_ms - corroboration_start_ms
            )
            corroboration_usable = (
                len(corroboration_pcm) > len(utterance_pcm)
                and speech_ms(corroboration_pcm, settings.live_vad_aggressiveness)
                >= settings.diarization_corroboration_min_speech_ms
                and not is_clipped(corroboration_pcm)
            )
            if corroboration_usable:
                fallback_embedding = embed_utterance(corroboration_pcm)
                corroboration_note = f" corrob({contiguous_ms}ms)"
            elif utterance_clipped:
                insufficient_signal = True
                corroboration_note = " corrob=unusable+clipped->insufficient_signal"
            else:
                corroboration_note = " corrob=unusable(thin,no rescue)"

        window_duration_ms = len(window_pcm) / 2 / SAMPLE_RATE * 1000
        did_split = False
        split_count = 0
        if confident_single_speaker or window_duration_ms < 9000:
            pass  # skip full diarize(), matches production
        else:
            try:
                t0 = time.time()
                import os
                import tempfile

                from app.services.audio.mixing import write_wav

                fd, window_wav = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                write_wav(window_wav, window_pcm)
                try:
                    turns = diarize(window_wav)
                finally:
                    os.unlink(window_wav)
                diarize_s = time.time() - t0
                u_start_s, u_end_s = utterance_offset_ms / 1000, (utterance_offset_ms + utterance_duration_ms) / 1000
                clipped_turns = []
                for start, end, speaker in _merge_adjacent_same_speaker(turns):
                    cs, ce = max(start, u_start_s), min(end, u_end_s)
                    if ce > cs:
                        clipped_turns.append((cs - u_start_s, ce - u_start_s, speaker))
                distinct = {c[2] for c in clipped_turns}
                did_split = len(distinct) > 1
                split_count = len(distinct)
                print(
                    f"       -> ran full diarize() in {diarize_s:.1f}s: {len(turns)} raw turns, "
                    f"{split_count} distinct within this utterance{'  [SPLIT]' if did_split else ''}"
                )
            except DiarizationUnavailable as e:
                print(f"       -> diarize() unavailable: {e}")

        if insufficient_signal:
            outcome = "UNRESOLVED (insufficient_signal — left for backfill)"
            cluster_idx, is_new = None, False
        else:
            cluster_idx, is_new, reason = cluster_and_assign(
                clusters, whole_embedding, fallback_embedding, last_active_idx, last_speaker_threshold
            )
            last_active_idx = cluster_idx
            outcome = f"cluster #{cluster_idx} {'[NEW SPEAKER]' if is_new else ''} ({reason})"
            if is_new:
                creation_log.append(
                    {
                        "utterance_idx": i,
                        "start_ms": u.start_ms,
                        "end_ms": u.end_ms,
                        "best_sim_before": round(best_sim, 3),
                        "content_ms": content_ms,
                        "clipped": utterance_clipped,
                        "had_corroboration_attempt": bool(corroboration_note),
                    }
                )

        print(
            f"[{i:>3}] {u.start_ms:>7}-{u.end_ms:>7}ms (dur={utterance_duration_ms:>5}ms, "
            f"speech={content_ms:>5}ms, embed={embed_s:.2f}s)  best_sim={best_sim:.3f} "
            f"skip_conf={'Y' if confident_single_speaker else 'n'} "
            f"2nd_look={'Y' if needs_second_look else 'n'}{corroboration_note}  => {outcome}"
        )

    print(f"\n=== {label}: SUMMARY ===")
    print(f"Total speakers created: {len(clusters)}")
    for entry in creation_log:
        print(
            f"  speaker created at utterance #{entry['utterance_idx']} "
            f"[{entry['start_ms']}-{entry['end_ms']}ms] — "
            f"best_sim_before={entry['best_sim_before']}, content={entry['content_ms']}ms, "
            f"clipped={entry['clipped']}, corroboration_attempted={entry['had_corroboration_attempt']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", help="path to a mono 16kHz WAV file")
    parser.add_argument("--chunker", choices=["vad", "deepgram"], default="vad")
    parser.add_argument("--context-window-ms", type=int, default=None, help="override diarization_context_window_ms")
    parser.add_argument(
        "--legacy-narrow-trigger",
        action="store_true",
        help="reproduce the pre-fix, content-thinness-only second-look gate (A/B comparison only) — default matches current production",
    )
    parser.add_argument(
        "--last-speaker-threshold",
        type=float,
        default=None,
        help="EXPERIMENTAL, verified unsafe (see run()'s docstring) — try one more lenient comparison against the most-recently-assigned cluster at this threshold",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.context_window_ms:
        settings.diarization_context_window_ms = args.context_window_ms

    pcm = read_wav(args.audio)
    total_ms = len(pcm) // SAMPLE_WIDTH / SAMPLE_RATE * 1000
    print(f"Loaded {args.audio}: {total_ms / 1000:.1f}s")

    if args.chunker == "vad":
        utterances = chunk_via_vad(pcm, settings)
    else:
        utterances = chunk_via_deepgram(args.audio, settings)

    if not utterances:
        print("No utterances detected — nothing to do.")
        return

    run(
        pcm,
        utterances,
        settings,
        label=f"{Path(args.audio).name} [{args.chunker}]",
        legacy_narrow_trigger=args.legacy_narrow_trigger,
        last_speaker_threshold=args.last_speaker_threshold,
    )


if __name__ == "__main__":
    main()
