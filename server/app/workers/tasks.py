import base64
import json
import logging
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.core.db import get_sync_db
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.meeting import Channel, Meeting, MeetingStatus, Speaker, TranscriptSegment
from app.services.alignment.align import align
from app.services.asr.whisper import transcribe
from app.services.audio.mixing import slice_pcm, write_wav
from app.services.diarization import events as diar_events
from app.services.diarization.cluster import (
    SIMILARITY_THRESHOLD,
    Cluster,
    best_match,
    locked_state,
    update_centroid,
)
from app.services.diarization.embedding import embed_utterance
from app.services.diarization.pyannote import DiarizationUnavailable, diarize
from app.services.diarization.text_split import words_in_range
from app.services.embeddings.chunking import chunk_text, chunk_transcript
from app.services.embeddings.embed import embed_texts
from app.services.embeddings.extract import extract_text
from app.services.embeddings.qdrant_store import upsert_chunks, upsert_meeting_chunks
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _normalize_audio(source_path: str, dest_path: str) -> None:
    """ffmpeg -> mono 16kHz WAV, the format both faster-whisper and pyannote
    are fed. Raises with ffmpeg's own stderr on failure (surfaced to the user
    via Meeting.processing_error).
    """
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", source_path, "-ac", "1", "-ar", "16000", dest_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # ffmpeg's stderr opens with a long build-config banner; the actual
        # error is always the last few lines, so surface those rather than
        # whatever a flat character-count slice happens to land on.
        error_lines = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"Audio normalization failed:\n{error_lines}")


def _wav_duration_seconds(path: str) -> int:
    with wave.open(path, "rb") as wf:
        return round(wf.getnframes() / wf.getframerate())


@celery_app.task(name="corella.process_meeting_audio")
def process_meeting_audio(meeting_id: str) -> None:
    with get_sync_db() as db:
        meeting = db.get(Meeting, UUID(meeting_id))
        if meeting is None:
            logger.error("process_meeting_audio: meeting %s not found", meeting_id)
            return
        if not meeting.audio_path:
            logger.error("process_meeting_audio: meeting %s has no audio_path", meeting_id)
            return

        try:
            with tempfile.TemporaryDirectory() as tmp:
                normalized_path = str(Path(tmp) / "normalized.wav")
                _normalize_audio(meeting.audio_path, normalized_path)

                whisper_segments = transcribe(normalized_path)

                diarization_turns = []
                try:
                    diarization_turns = diarize(normalized_path)
                except DiarizationUnavailable as e:
                    logger.info("Skipping diarization for meeting %s: %s", meeting_id, e)
                except Exception:
                    logger.exception(
                        "Diarization failed for meeting %s; continuing without speaker labels",
                        meeting_id,
                    )

                aligned = align(whisper_segments, diarization_turns)

                # First-seen diarization labels become per-meeting Speaker rows,
                # in chronological order of first appearance.
                speakers_by_label: dict[str, Speaker] = {}
                for label in dict.fromkeys(a.speaker for a in aligned if a.speaker):
                    speaker = Speaker(
                        owner_id=meeting.owner_id,
                        meeting_id=meeting.id,
                        label=f"Speaker {len(speakers_by_label) + 1}",
                        channel=Channel.UNKNOWN,
                    )
                    db.add(speaker)
                    speakers_by_label[label] = speaker
                db.flush()  # assign speaker.id before TranscriptSegment rows reference it

                for a in aligned:
                    db.add(
                        TranscriptSegment(
                            meeting_id=meeting.id,
                            speaker_id=speakers_by_label[a.speaker].id if a.speaker else None,
                            channel=Channel.UNKNOWN,
                            start_ms=a.start_ms,
                            end_ms=a.end_ms,
                            text=a.text,
                            is_partial=False,
                        )
                    )

                meeting.duration_seconds = _wav_duration_seconds(normalized_path)
                meeting.status = MeetingStatus.READY
                meeting.processing_error = None
                db.commit()
        except Exception as e:
            logger.exception("process_meeting_audio failed for meeting %s", meeting_id)
            db.rollback()
            meeting.status = MeetingStatus.FAILED
            meeting.processing_error = str(e)[:2000]
            db.commit()
            return

    try:
        celery_app.send_task("corella.index_meeting_search", args=[meeting_id])
    except Exception:
        # Search indexing is a nice-to-have, not a reason to retroactively
        # mark an already-successfully-transcribed meeting as failed.
        logger.exception("Failed to dispatch index_meeting_search for meeting %s", meeting_id)


@celery_app.task(name="corella.process_kb_document")
def process_kb_document(document_id: str) -> None:
    with get_sync_db() as db:
        document = db.get(KBDocument, UUID(document_id))
        if document is None:
            logger.error("process_kb_document: document %s not found", document_id)
            return

        document.status = KBDocumentStatus.PROCESSING
        db.commit()

        try:
            text = extract_text(document.storage_path)
            chunks = chunk_text(text)
            if not chunks:
                raise ValueError("No extractable text found in this document")

            embeddings = embed_texts(chunks)
            upsert_chunks(document.id, document.owner_id, chunks, embeddings)

            document.chunk_count = len(chunks)
            document.status = KBDocumentStatus.READY
            document.error = None
            db.commit()
        except Exception as e:
            logger.exception("process_kb_document failed for document %s", document_id)
            db.rollback()
            document.status = KBDocumentStatus.FAILED
            document.error = str(e)[:2000]
            db.commit()


def _merge_adjacent_same_speaker(turns: list) -> list[tuple[float, float, str]]:
    """diarize() can return several short turns for one continuous person
    (a brief internal pause isn't a speaker change) — collapse consecutive
    same-label turns into one span before deciding how many segments this
    utterance actually needs."""
    merged: list[tuple[float, float, str]] = []
    for t in turns:
        if merged and merged[-1][2] == t.speaker:
            merged[-1] = (merged[-1][0], t.end, t.speaker)
        else:
            merged.append((t.start, t.end, t.speaker))
    return merged


def _cluster_and_assign(db, meeting: Meeting, clusters: list[Cluster], embedding) -> str:
    """One pre-computed embedding -> one clustering decision -> that
    cluster's Speaker.label. Shared by both the simple (no-split) and split
    paths below. Takes an embedding, not raw PCM: extraction is slow on a
    cold model load (the first call in a worker process), and this runs
    inside the per-meeting Redis lock (locked_state) — embedding *before*
    acquiring the lock, not during, is what keeps that lock's hold time
    short (verified this mattered: a cold-start extraction held inside the
    lock outlasted its 10s timeout, so the lock auto-expired mid-hold and
    releasing it at the end raised redis.exceptions.LockNotOwnedError)."""
    idx, similarity = best_match(clusters, embedding)
    if idx is not None and similarity >= SIMILARITY_THRESHOLD:
        cluster = clusters[idx]
        update_centroid(cluster, embedding)
        speaker = db.get(Speaker, UUID(cluster.speaker_id))
    else:
        speaker = Speaker(
            owner_id=meeting.owner_id,
            meeting_id=meeting.id,
            label=f"Speaker {len(clusters) + 1}",
            channel=Channel.ME,
        )
        db.add(speaker)
        db.flush()  # assign speaker.id before the cluster references it
        clusters.append(Cluster(centroid=embedding.tolist(), count=1, speaker_id=str(speaker.id)))
    return speaker


def _segment_payload(segment: TranscriptSegment, speaker_label: str) -> dict:
    return {
        "id": str(segment.id),
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "text": segment.text,
        "speaker_label": speaker_label,
    }


@celery_app.task(name="corella.diarize_utterance")
def diarize_utterance(
    meeting_id: str,
    segment_id: str,
    window_pcm_b64: str,
    utterance_offset_ms: int,
    utterance_duration_ms: int,
    words_json: str,
) -> None:
    """Same-room live diarization: one call per "Me"-channel utterance,
    dispatched right after live_session.py persists its TranscriptSegment.

    `window_pcm_b64` is not just this utterance's own audio — it's a wider
    window of already-received "Me"-channel audio ending at the utterance's
    end (app/services/audio/mixing.py:extract_channel_window, built in
    live_session.py). diarize()'s pipeline is unreliable well under ~10s
    (verified empirically — an isolated ~4s two-speaker clip missed the
    speaker change entirely); the window gives it enough context to reliably
    place a speaker-change point *within* this one utterance, something the
    whole-file batch Pipeline was never designed to do incrementally.
    `utterance_offset_ms`/`utterance_duration_ms` locate the utterance
    itself inside that window; `words_json` (faster-whisper word timestamps,
    utterance-relative) is used to attribute text if a split happens.

    Never touches Meeting.status — a failure here just leaves this one
    segment unlabeled (falls back to generic "Me"), not a reason to fail
    the meeting.
    """
    window_pcm = base64.b64decode(window_pcm_b64)
    words = json.loads(words_json)
    u_start_s = utterance_offset_ms / 1000
    u_end_s = u_start_s + utterance_duration_ms / 1000

    # diarize()'s pipeline is only reliable well above ~10s of audio
    # (verified empirically — an isolated ~4-6s clip either missed a real
    # speaker change entirely or produced garbage overlapping turns, both
    # reproduced live). live_session.py *asks* for settings.
    # diarization_context_window_ms (default 12000) of context, but early in
    # a session there may not be that much "Me" audio yet to satisfy it with
    # — checking the window it actually got, not the size requested, avoids
    # trusting a split decision the pipeline was never reliable enough to
    # make; falls back to the simple whole-utterance path instead.
    window_duration_ms = len(window_pcm) / 2 / 16000 * 1000
    if window_duration_ms < 9000:
        turns = []
    else:
        window_wav = None
        try:
            fd, window_wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            write_wav(window_wav, window_pcm)
            turns = diarize(window_wav)
        except DiarizationUnavailable:
            turns = []
        except Exception:
            logger.exception("diarize_utterance: within-utterance segmentation failed for %s", segment_id)
            turns = []
        finally:
            if window_wav:
                try:
                    os.unlink(window_wav)
                except OSError:
                    pass

    clipped: list[tuple[float, float, str]] = []
    for start, end, speaker in _merge_adjacent_same_speaker(turns):
        clip_start, clip_end = max(start, u_start_s), min(end, u_end_s)
        if clip_end > clip_start:
            clipped.append((clip_start - u_start_s, clip_end - u_start_s, speaker))

    distinct_local_speakers = {c[2] for c in clipped}

    with get_sync_db() as db:
        meeting = db.get(Meeting, UUID(meeting_id))
        segment = db.get(TranscriptSegment, UUID(segment_id))
        if meeting is None or segment is None:
            return

        # Embedding extraction happens here, before the per-meeting lock
        # below is acquired — it's the slow part (a cold model load can take
        # several seconds) and doesn't need cross-task exclusivity; only the
        # actual cluster-decision-and-write does. Verified this ordering
        # matters: a cold-start extraction held *inside* the lock outlasted
        # its 10s timeout, so Redis auto-expired it mid-hold and releasing
        # it at the end raised redis.exceptions.LockNotOwnedError.
        split_turns: list[tuple[float, float, str, object]] = []
        if len(distinct_local_speakers) > 1:
            for rel_start, rel_end, _local_label in clipped:
                text = words_in_range(words, rel_start, rel_end)
                if not text:
                    continue
                turn_pcm = slice_pcm(
                    window_pcm,
                    utterance_offset_ms + round(rel_start * 1000),
                    round((rel_end - rel_start) * 1000),
                )
                split_turns.append((rel_start, rel_end, text, embed_utterance(turn_pcm)))

        # A "split" that loses every turn but one to empty text-attribution
        # isn't a split — fall back to the whole utterance rather than
        # silently dropping it.
        did_split = len(split_turns) >= 2
        if not did_split:
            pcm = slice_pcm(window_pcm, utterance_offset_ms, utterance_duration_ms)
            whole_embedding = embed_utterance(pcm)

        try:
            with locked_state(meeting.id) as clusters:
                resulting: list[tuple[TranscriptSegment, str]] = []

                if not did_split:
                    speaker = _cluster_and_assign(db, meeting, clusters, whole_embedding)
                    # Assign the relationship object, not just the FK column
                    # — segment.speaker was already loaded (as None) when it
                    # was fetched above, and setting speaker_id alone doesn't
                    # refresh that already-cached relationship. Matters here
                    # because the backfill query below can re-select this
                    # exact row later in the same session/identity map.
                    segment.speaker = speaker
                    resulting.append((segment, speaker.label))
                else:
                    base_start_ms = segment.start_ms
                    for rel_start, rel_end, text, embedding in split_turns:
                        speaker = _cluster_and_assign(db, meeting, clusters, embedding)
                        new_row = TranscriptSegment(
                            meeting_id=meeting.id,
                            speaker_id=speaker.id,
                            channel=Channel.ME,
                            start_ms=base_start_ms + round(rel_start * 1000),
                            end_ms=base_start_ms + round(rel_end * 1000),
                            text=text,
                            is_partial=False,
                        )
                        db.add(new_row)
                        resulting.append((new_row, speaker.label))
                    db.delete(segment)
                    # Recorded regardless of whether the gate is open yet —
                    # if it opens later, the backfill snapshot below still
                    # needs to know this id is gone, not just future ones.
                    diar_events.record_removed(meeting.id, str(segment.id))

                db.flush()  # assign ids to any new rows before building the WS payload

                if len(clusters) >= 2:
                    if not diar_events.has_reported_anything(meeting.id):
                        # First time the gate has ever opened for this meeting —
                        # a full authoritative snapshot, not an incremental diff,
                        # so the frontend can't miss an earlier split that
                        # happened before anything was ever reported.
                        all_labeled = list(
                            db.scalars(
                                select(TranscriptSegment).where(
                                    TranscriptSegment.meeting_id == meeting.id,
                                    TranscriptSegment.channel == Channel.ME,
                                    TranscriptSegment.speaker_id.is_not(None),
                                )
                            )
                        )
                        payload = [_segment_payload(s, s.speaker.label) for s in all_labeled]
                        diar_events.push_event(
                            meeting.id,
                            {
                                "type": "diarization_update",
                                "is_snapshot": True,
                                "removed_segment_ids": diar_events.all_removed(meeting.id),
                                "segments": payload,
                            },
                            [str(s.id) for s in all_labeled],
                        )
                    else:
                        payload = [_segment_payload(s, label) for s, label in resulting]
                        removed = [str(segment.id)] if did_split else []
                        diar_events.push_event(
                            meeting.id,
                            {
                                "type": "diarization_update",
                                "is_snapshot": False,
                                "removed_segment_ids": removed,
                                "segments": payload,
                            },
                            [str(s.id) for s, _ in resulting],
                        )
            db.commit()
        except Exception:
            logger.exception("diarize_utterance: clustering failed for segment %s", segment_id)
            db.rollback()


@celery_app.task(name="corella.index_meeting_search")
def index_meeting_search(meeting_id: str) -> None:
    """Embeds this meeting's transcript into the meeting_chunks Qdrant
    collection for Dashboard semantic search — dispatched fire-and-forget
    whenever a meeting reaches READY, from both process_meeting_audio's
    success path and live_session.py's _finalize(). Never touches
    Meeting.status — a failure here just means this meeting won't show up
    in search yet, not a reason to fail the meeting itself.
    """
    with get_sync_db() as db:
        meeting = db.get(Meeting, UUID(meeting_id))
        if meeting is None:
            return

        segments = list(
            db.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == meeting.id)
                .order_by(TranscriptSegment.start_ms)
            )
        )
        chunks = chunk_transcript(segments)
        if not chunks:
            return

        try:
            embeddings = embed_texts([text for text, _start, _end in chunks])
            upsert_meeting_chunks(meeting.id, meeting.owner_id, chunks, embeddings)
        except Exception:
            logger.exception("index_meeting_search failed for meeting %s", meeting_id)
