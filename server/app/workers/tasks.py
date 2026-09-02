import base64
import logging
import subprocess
import tempfile
import wave
from pathlib import Path
from uuid import UUID

from app.core.db import get_sync_db
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.meeting import Channel, Meeting, MeetingStatus, Speaker, TranscriptSegment
from app.services.alignment.align import align
from app.services.asr.whisper import transcribe
from app.services.diarization.cluster import (
    SIMILARITY_THRESHOLD,
    Cluster,
    best_match,
    locked_state,
    update_centroid,
)
from app.services.diarization.embedding import embed_utterance
from app.services.diarization.pyannote import DiarizationUnavailable, diarize
from app.services.embeddings.chunking import chunk_text
from app.services.embeddings.embed import embed_texts
from app.services.embeddings.extract import extract_text
from app.services.embeddings.qdrant_store import upsert_chunks
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


@celery_app.task(name="corella.diarize_utterance")
def diarize_utterance(meeting_id: str, segment_id: str, pcm_b64: str) -> None:
    """Same-room live diarization: one call per "Me"-channel utterance,
    dispatched right after live_session.py persists its TranscriptSegment.
    Extracts a speaker embedding and does online cosine-similarity
    clustering scoped to this meeting (app/services/diarization/cluster.py)
    — a different, incremental building block than diarize()'s whole-file
    batch Pipeline, which has no notion of "here's one more utterance."
    Never touches Meeting.status — a failure here just leaves this one
    segment unlabeled (falls back to generic "Me"), not a reason to fail
    the meeting; live_session.py's own polling bridge is what decides when
    (and whether) to surface labels to the browser, not this task.
    """
    try:
        embedding = embed_utterance(base64.b64decode(pcm_b64))
    except Exception:
        logger.exception("diarize_utterance: embedding failed for segment %s", segment_id)
        return

    with get_sync_db() as db:
        meeting = db.get(Meeting, UUID(meeting_id))
        segment = db.get(TranscriptSegment, UUID(segment_id))
        if meeting is None or segment is None:
            return

        try:
            with locked_state(meeting.id) as clusters:
                idx, similarity = best_match(clusters, embedding)
                if idx is not None and similarity >= SIMILARITY_THRESHOLD:
                    cluster = clusters[idx]
                    update_centroid(cluster, embedding)
                    segment.speaker_id = UUID(cluster.speaker_id)
                else:
                    speaker = Speaker(
                        owner_id=meeting.owner_id,
                        meeting_id=meeting.id,
                        label=f"Speaker {len(clusters) + 1}",
                        channel=Channel.ME,
                    )
                    db.add(speaker)
                    db.flush()  # assign speaker.id before the cluster/segment reference it
                    clusters.append(
                        Cluster(centroid=embedding.tolist(), count=1, speaker_id=str(speaker.id))
                    )
                    segment.speaker_id = speaker.id
            db.commit()
        except Exception:
            logger.exception("diarize_utterance: clustering failed for segment %s", segment_id)
            db.rollback()
