import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from uuid import UUID

import numpy as np
from sqlalchemy import func, select

from app.core import storage
from app.core.config import get_settings
from app.core.db import SessionLocal, engine, get_sync_db
from app.models.cost import UsageKind
from app.models.kb_document import KBDocument, KBDocumentStatus
from app.models.meeting import Channel, Meeting, MeetingStatus, Speaker, TranscriptSegment
from app.models.user import User
from app.models.voice_identity import VoiceIdentity
from app.services.admin.webhooks import dispatch_call_type_webhook
from app.services.alignment.align import align
from app.services.asr import deepgram
from app.services.asr.resolve import resolve_stt_provider
from app.services.asr.whisper import transcribe as whisper_transcribe
from app.services.audio.mixing import read_wav_pcm, slice_pcm, write_wav
from app.services.copilot.cost import add_meeting_cost
from app.services.copilot.json_parse import parse_json_response
from app.services.copilot.report import ReportError
from app.services.copilot.report import generate_report as run_generate_report
from app.services.diarization import events as diar_events
from app.services.diarization.cluster import (
    SIMILARITY_THRESHOLD,
    Cluster,
    PendingSegment,
    best_match,
    locked_state,
    meets_guest_floor,
    peek_clusters,
    update_centroid,
)
from app.services.diarization.embedding import embed_utterance
from app.services.diarization.pyannote import DiarizationUnavailable, diarize
from app.services.embeddings.chunking import chunk_text, chunk_transcript
from app.services.embeddings.embed import embed_texts
from app.services.embeddings.extract import extract_text
from app.services.embeddings.qdrant_store import (
    search_speaker_embeddings,
    upsert_chunks,
    upsert_meeting_chunks,
    upsert_speaker_embedding,
)
from app.services.llm.base import LLMError, LLMMessage, complete
from app.services.llm.pricing import estimate_cost_usd
from app.services.llm.resolve import resolve_provider
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


async def _resolve_and_maybe_transcribe_deepgram(owner_id: str, wav_path: str, word_timestamps: bool):
    """Resolves which STT engine this owner should use, and — if it's
    Deepgram — runs the actual transcription too, in the same short-lived
    async session/call. Returns (ResolvedStt, segments-or-None); None
    means "not Deepgram, or Deepgram failed" — the sync caller falls back
    to local whisper.transcribe() either way, so a Deepgram outage never
    breaks the meeting, same graceful-degradation spirit as the
    diarization skip right next to this call site.
    """
    async with SessionLocal() as db:
        stt = await resolve_stt_provider(db, UUID(owner_id))
    if stt.provider != "deepgram":
        return stt, None

    with open(wav_path, "rb") as f:
        wav_bytes = f.read()
    try:
        segments = await deepgram.transcribe(
            wav_bytes, stt.model, stt.api_key, word_timestamps, language=stt.language
        )
        return stt, segments
    except deepgram.SttError:
        logger.exception("Deepgram transcription failed for owner %s; falling back to local whisper", owner_id)
        return stt, None


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

                # Deepgram (if the owner has it configured) or local
                # faster-whisper otherwise — see
                # app/services/asr/resolve.py. Any Deepgram failure falls
                # back to local Whisper rather than failing the meeting,
                # same graceful-degradation spirit as the diarization skip
                # right below.
                _stt, asr_segments = asyncio.run(
                    _with_engine_cleanup(
                        _resolve_and_maybe_transcribe_deepgram(
                            str(meeting.owner_id), normalized_path, word_timestamps=False
                        )
                    )
                )
                if asr_segments is None:
                    asr_segments = whisper_transcribe(normalized_path)

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

                aligned = align(asr_segments, diarization_turns)

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

    try:
        celery_app.send_task("corella.generate_report", args=[meeting_id])
    except Exception:
        # Same reasoning as index_meeting_search above — the meeting is
        # already successfully transcribed either way; the existing manual
        # "Generate report" button is still there if this doesn't run.
        logger.exception("Failed to dispatch generate_report for meeting %s", meeting_id)


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


_SPEAKER_LABEL_FORMAT = {
    # "Speaker N" is the original, already-shipped Me-side format —
    # unchanged, so nothing that already depends on it (frontend dot-color
    # parsing, existing meetings' persisted labels) breaks. Them gets its
    # own distinct prefix, not the same "Speaker N": MeetingDetail.tsx lists
    # every segment's speaker_label in one flat list with no channel
    # column, so two unrelated people (one from each pool) both reading as
    # "Speaker 1" would be a real, avoidable ambiguity.
    Channel.ME: "Speaker {n}",
    Channel.THEM: "Them {n}",
}


def _recognize_voice_identity(
    db, embedding, owner_group_id: UUID | None, owner_id: UUID
) -> VoiceIdentity | None:
    """Checks the durable, cross-meeting library (Phase O) before this
    meeting's own online clustering ever creates a fresh anonymous
    cluster — the meeting owner's own enrolled identity and their group's
    shared pool are searched together (see search_speaker_embeddings'
    docstring for why one combined call is enough). None if nobody's ever
    enrolled/been recognized as this voice.
    """
    matches = search_speaker_embeddings(
        embedding.tolist(),
        SIMILARITY_THRESHOLD,
        group_id=owner_group_id,
        linked_user_id=owner_id,
        top_k=1,
    )
    if not matches:
        return None
    return db.get(VoiceIdentity, UUID(matches[0]["voice_identity_id"]))


def _promote_new_speaker(
    db, meeting: Meeting, channel: Channel, embedding, identity: VoiceIdentity | None
) -> tuple[Speaker, bool]:
    """A brand-new real "Speaker N"/"Them N" row for a registry entry that
    just earned one — either a durably-recognized voice (Phase O) or a
    freshly-provisional one that crossed the guest floor (see
    cluster.meets_guest_floor). Numbered by how many speakers already exist
    on this channel/meeting so far, not by registry-list position — a
    provisional entry that never promotes shouldn't leave a numbering gap.
    """
    existing_count = (
        db.scalar(
            select(func.count())
            .select_from(Speaker)
            .where(Speaker.meeting_id == meeting.id, Speaker.channel == channel)
        )
        or 0
    )
    speaker = Speaker(
        owner_id=meeting.owner_id,
        meeting_id=meeting.id,
        label=_SPEAKER_LABEL_FORMAT[channel].format(n=existing_count + 1),
        channel=channel,
        voice_identity_id=identity.id if identity else None,
    )
    db.add(speaker)
    db.flush()  # assign speaker.id before the caller references it
    return speaker, identity is None


def _segment_payload(segment: TranscriptSegment, speaker_label: str) -> dict:
    return {
        "id": str(segment.id),
        "channel": segment.channel.value,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "text": segment.text,
        "speaker_label": speaker_label,
        "linked_user_id": str(segment.linked_user_id) if segment.linked_user_id else None,
    }


@celery_app.task(name="corella.reconcile_diarization")
def reconcile_diarization(meeting_id: str, channel_value: str, window_pcm_b64: str, window_start_ms_abs: int) -> None:
    """Same-room live diarization: one periodic call per active channel,
    dispatched from a timed loop in live_session.py (not per-utterance —
    see app/core/config.py's diarization_reconcile_* settings docstring for
    why the old per-utterance design was replaced). `window_pcm_b64` is a
    rolling window of already-received per-channel audio ending "now";
    `window_start_ms_abs` locates its start on the session's absolute-ms
    timeline. Runs the *full* pyannote diarize() pipeline over that window
    (not a lone embedding), groups its turns by local pyannote label, and
    reconciles each local voice against this meeting/channel's persistent
    registry (app/services/diarization/cluster.py) — matching an existing
    entry, or registering a new one, claim-once per pass so two real
    speakers active in the same window can't both claim one stored voice.
    Segments are relabeled in place by turn overlap, never split/deleted —
    a real, accepted scope reduction from the old per-utterance design's
    mid-utterance splitting (Phase F-2): Deepgram's own aggressive
    endpointing already keeps individual committed segments short, so a
    single segment spanning a genuine speaker change is rare in practice
    with this STT path; if it turns out to matter, segment splitting on
    ambiguous multi-turn overlap is a natural, contained follow-up.

    Never touches Meeting.status — a failure here just leaves this pass's
    segments unlabeled (falls back to generic "Me"/"Them"), not a reason to
    fail the meeting; a later pass gets another chance at them.
    """
    window_pcm = base64.b64decode(window_pcm_b64)
    channel = Channel(channel_value)
    settings = get_settings()

    window_wav = None
    try:
        fd, window_wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        write_wav(window_wav, window_pcm)
        turns = diarize(window_wav)
    except DiarizationUnavailable:
        return
    except Exception:
        logger.exception("reconcile_diarization: diarize() failed for meeting %s channel %s", meeting_id, channel_value)
        return
    finally:
        if window_wav:
            try:
                os.unlink(window_wav)
            except OSError:
                pass

    merged = _merge_adjacent_same_speaker(turns)
    if not merged:
        return

    by_label: dict[str, list[tuple[float, float]]] = {}
    for start, end, label in merged:
        by_label.setdefault(label, []).append((start, end))

    # One mean embedding per local pyannote label, plus its turns converted
    # to absolute session ms and total duration (used only to order
    # claim-once matching below — busiest local voice claims first, so a
    # brief interjection can't steal the registry match a real turn
    # needed). Extraction happens here, outside any lock — the slow part
    # (a cold model load can take several seconds) doesn't need cross-task
    # exclusivity, only the actual registry read-decide-write below does
    # (Phase F-2's own hard-learned lesson: never hold locked_state's lock
    # across slow ML inference).
    local_candidates: list[tuple[str, np.ndarray, list[tuple[int, int]], int]] = []
    for label, spans in by_label.items():
        embeddings = []
        for start, end in spans:
            # pyannote's own segmentation stage can produce genuinely tiny
            # turns (a noise blip, a sliver at the window's edge) — too
            # short for the wespeaker embedding model's internal feature
            # extractor, which asserts a minimum window size rather than
            # returning a degenerate result. Reproduced live in production:
            # 6 of 10 real reconciliation passes crashed outright on
            # `AssertionError: choose a window size 400 that is [2, 272]`
            # with no try/except anywhere between here and the task
            # boundary, silently killing that entire pass — zero segments
            # labeled that round, live or in the final catch-up pass,
            # exactly the "no live tags" / "stuck at Identifying" reports
            # this fixed. `live_min_utterance_ms` (300ms) already exists
            # for the identical "too short to be worth trusting" judgment
            # elsewhere in this codebase — reused here rather than a new
            # constant. The try/except is defense in depth on top of that
            # floor, not a replacement for it: skip this one span rather
            # than crash the whole pass if the model still balks for some
            # other reason.
            if (end - start) * 1000 < settings.live_min_utterance_ms:
                continue
            span_pcm = slice_pcm(window_pcm, round(start * 1000), round((end - start) * 1000))
            if not span_pcm:
                continue
            try:
                embeddings.append(embed_utterance(span_pcm))
            except Exception:
                logger.exception(
                    "reconcile_diarization: embedding a %.3fs turn failed for meeting %s channel %s — skipping it",
                    end - start,
                    meeting_id,
                    channel_value,
                )
        if not embeddings:
            continue
        mean_embedding = np.mean(embeddings, axis=0)
        spans_abs = [
            (window_start_ms_abs + round(start * 1000), window_start_ms_abs + round(end * 1000))
            for start, end in spans
        ]
        total_dur_ms = sum(end - start for start, end in spans_abs)
        local_candidates.append((label, mean_embedding, spans_abs, total_dur_ms))

    if not local_candidates:
        return
    local_candidates.sort(key=lambda c: c[3], reverse=True)

    pending_name_inference: list[tuple[UUID, list]] = []

    try:
        with get_sync_db() as db:
            meeting = db.get(Meeting, UUID(meeting_id))
            if meeting is None:
                return

            with locked_state(meeting.id, channel) as (clusters, pending):
                resulting: list[tuple[TranscriptSegment, str]] = []
                label_to_idx: dict[str, int] = {}
                claimed: set[int] = set()

                for label, embedding, _spans_abs, _dur in local_candidates:
                    idx, sim = best_match(clusters, embedding, exclude=claimed)
                    if idx is not None and sim >= SIMILARITY_THRESHOLD:
                        claimed.add(idx)
                        update_centroid(clusters[idx], embedding)
                        label_to_idx[label] = idx
                        continue

                    # No existing registry entry claims this voice —
                    # register a new one. The very first entry ever seen on
                    # this channel is promoted outright (nothing to compare
                    # it against yet, same precedent the old per-utterance
                    # design used for a channel's first-ever cluster);
                    # everything after either needs a durable cross-meeting
                    # identity match (Phase O — real information, not
                    # noise, regardless of how little it's said so far) or
                    # has to clear the guest floor below before it earns a
                    # real label.
                    identity = _recognize_voice_identity(db, embedding, meeting.owner.group_id, meeting.owner_id)
                    is_first_ever = len(clusters) == 0
                    if identity is not None or is_first_ever:
                        speaker, needs_naming = _promote_new_speaker(db, meeting, channel, embedding, identity)
                        clusters.append(
                            Cluster(centroid=embedding.tolist(), count=1, weight_ms=0, speaker_id=str(speaker.id))
                        )
                        if needs_naming:
                            pending_name_inference.append((speaker.id, embedding.tolist()))
                    else:
                        clusters.append(Cluster(centroid=embedding.tolist(), count=1, weight_ms=0, speaker_id=None))
                    idx = len(clusters) - 1
                    claimed.add(idx)
                    label_to_idx[label] = idx

                # Assign currently-unlabeled, not-already-pending segments
                # in this window to whichever local turn overlaps them
                # most. A segment no turn covers at all is left alone, not
                # guessed at — the next pass's window will very likely
                # include a turn for it as more audio streams in (unlike
                # the old per-utterance design, this one gets retried
                # automatically, no special-case fallback needed).
                already_pending_ids = {p.segment_id for p in pending}
                window_end_ms_abs = window_start_ms_abs + round(len(window_pcm) / 2 / 16000 * 1000)
                spans_by_label = {label: spans_abs for label, _e, spans_abs, _d in local_candidates}
                candidates = db.scalars(
                    select(TranscriptSegment).where(
                        TranscriptSegment.meeting_id == meeting.id,
                        TranscriptSegment.channel == channel,
                        TranscriptSegment.speaker_id.is_(None),
                        TranscriptSegment.start_ms < window_end_ms_abs,
                        TranscriptSegment.end_ms > window_start_ms_abs,
                    )
                ).all()

                new_weight_by_idx: dict[int, int] = {}
                for segment in candidates:
                    if str(segment.id) in already_pending_ids:
                        continue
                    best_label, best_overlap = None, 0
                    for label, spans_abs in spans_by_label.items():
                        overlap = sum(
                            max(0, min(segment.end_ms, end) - max(segment.start_ms, start))
                            for start, end in spans_abs
                        )
                        if overlap > best_overlap:
                            best_overlap, best_label = overlap, label
                    if best_label is None:
                        continue

                    idx = label_to_idx[best_label]
                    duration_ms = segment.end_ms - segment.start_ms
                    new_weight_by_idx[idx] = new_weight_by_idx.get(idx, 0) + duration_ms

                    if clusters[idx].speaker_id is not None:
                        segment.speaker = db.get(Speaker, UUID(clusters[idx].speaker_id))
                        resulting.append((segment, segment.speaker.display_label))
                    else:
                        pending.append(PendingSegment(segment_id=str(segment.id), cluster_index=idx))

                for idx, added in new_weight_by_idx.items():
                    clusters[idx].weight_ms += added

                # Promote any provisional entry that just crossed the guest
                # floor — checked fresh every pass (not only the pass a
                # voice was first seen in) so a real second speaker
                # promotes the moment they've genuinely spoken enough.
                total_weight_ms = sum(c.weight_ms for c in clusters)
                for idx, cluster in enumerate(clusters):
                    if cluster.speaker_id is not None:
                        continue
                    if not meets_guest_floor(cluster, total_weight_ms, settings):
                        continue
                    embedding_arr = np.array(cluster.centroid)
                    identity = _recognize_voice_identity(db, embedding_arr, meeting.owner.group_id, meeting.owner_id)
                    speaker, needs_naming = _promote_new_speaker(db, meeting, channel, embedding_arr, identity)
                    cluster.speaker_id = str(speaker.id)
                    if needs_naming:
                        pending_name_inference.append((speaker.id, cluster.centroid))

                    still_pending: list[PendingSegment] = []
                    for entry in pending:
                        if entry.cluster_index == idx:
                            seg = db.get(TranscriptSegment, UUID(entry.segment_id))
                            if seg is not None:
                                seg.speaker = speaker
                                resulting.append((seg, speaker.display_label))
                        else:
                            still_pending.append(entry)
                    pending[:] = still_pending

                db.flush()  # assign ids/relationships before building the WS payload

                has_resolved_identity = any(s.speaker.voice_identity_id is not None for s, _ in resulting)
                promoted_count = sum(1 for c in clusters if c.speaker_id is not None)
                if resulting and (promoted_count >= 2 or has_resolved_identity):
                    if not diar_events.has_reported_anything(meeting.id, channel):
                        # First time the gate has ever opened for this
                        # meeting *on this channel* — a full authoritative
                        # snapshot, not an incremental diff, so the frontend
                        # can't miss an earlier label that happened before
                        # anything was ever reported on this channel.
                        all_labeled = list(
                            db.scalars(
                                select(TranscriptSegment).where(
                                    TranscriptSegment.meeting_id == meeting.id,
                                    TranscriptSegment.channel == channel,
                                    TranscriptSegment.speaker_id.is_not(None),
                                )
                            )
                        )
                        payload = [_segment_payload(s, s.speaker.display_label) for s in all_labeled]
                        diar_events.push_event(
                            meeting.id,
                            {"type": "diarization_update", "is_snapshot": True, "removed_segment_ids": [], "segments": payload},
                            [str(s.id) for s in all_labeled],
                            channel,
                        )
                    else:
                        payload = [_segment_payload(s, label) for s, label in resulting]
                        diar_events.push_event(
                            meeting.id,
                            {"type": "diarization_update", "is_snapshot": False, "removed_segment_ids": [], "segments": payload},
                            [str(s.id) for s, _ in resulting],
                            channel,
                        )
            db.commit()
    except Exception:
        logger.exception("reconcile_diarization: clustering failed for meeting %s channel %s", meeting_id, channel_value)
        return

    # Dispatched after the transaction commits, not inside it — a slow LLM
    # call has no business holding up diarization's own commit, and this
    # never needs to block the diarization result itself either.
    for pending_speaker_id, pending_embedding in pending_name_inference:
        try:
            celery_app.send_task(
                "corella.identify_speaker_name",
                args=[meeting_id, str(pending_speaker_id), json.dumps(pending_embedding)],
            )
        except Exception:
            logger.exception(
                "Failed to dispatch identify_speaker_name for speaker %s", pending_speaker_id
            )


@celery_app.task(name="corella.quick_label_hint")
def quick_label_hint(meeting_id: str, segment_id: str, channel_value: str, utterance_pcm_b64: str) -> None:
    """Fast, read-only live-labeling shortcut, dispatched the instant a
    segment commits (app/ws/live_session.py:_commit_segment) — a real user
    report that live labeling "isn't live at all" traced to the periodic
    reconcile_diarization pass itself: even once it runs, it's a real
    diarize() pipeline call, measured at 6-33s of real worker CPU time in
    production, on top of however much of its own ~20-25s interval had
    already elapsed. This never replaces that pass — reconcile_diarization
    remains the *only* thing that ever creates a new speaker, promotes a
    provisional one, or writes anything to Postgres. This only ever
    recognizes a voice *already confirmed* by an earlier pass, and does so
    almost immediately: one cheap embedding (not a full diarize() call) on
    just this utterance's own audio, checked against the current registry
    with no lock (peek_clusters — a stale-by-one-pass read costs nothing
    worse than a slightly-delayed hint, never a wrong permanent write,
    since nothing here is permanent). If it doesn't confidently match an
    already-*promoted* cluster, or the "2+ confirmed speakers" gate
    (has_reported_anything) hasn't opened for this channel yet, it does
    nothing — silently defers to the real pass, exactly like every other
    case this codebase refuses to guess on. Also means it works best on
    longer utterances: a very short one's own single embedding can be too
    noisy to confidently match on its own (the same short-utterance
    unreliability Phase U/V/W already found and worked around for the
    authoritative pass, which has the luxury of real acoustic context this
    fast path deliberately doesn't try to replicate) — abstaining and
    falling back to the real pass in that case is correct, not a bug.

    The pushed event deliberately reuses the diarization_update wire shape
    (rather than a new one) so the frontend's existing handling needs no
    new state — see live.ts's speaker_hint branch.
    """
    utterance_pcm = base64.b64decode(utterance_pcm_b64)
    channel = Channel(channel_value)

    try:
        embedding = embed_utterance(utterance_pcm)
    except Exception:
        logger.exception("quick_label_hint: embedding failed for segment %s", segment_id)
        return

    # The "2+ confirmed speakers" gate (reconcile_diarization's own
    # has_reported_anything check) exists specifically so a genuinely
    # solo channel never flashes a needless "Speaker 1" — the very first
    # cluster on a channel auto-promotes immediately (real Speaker row,
    # speaker_id set) long before that's known to be true, so checking
    # speaker_id alone here isn't enough; verified live that skipping this
    # check let a hint reveal a label before the authoritative mechanism
    # ever had. Only ever hint once the real mechanism has already opened
    # the gate at least once for this channel — from then on every
    # subsequent utterance from either confirmed speaker is fair game.
    if not diar_events.has_reported_anything(UUID(meeting_id), channel):
        return

    clusters = peek_clusters(UUID(meeting_id), channel)
    idx, sim = best_match(clusters, embedding)
    if idx is None or sim < SIMILARITY_THRESHOLD:
        return
    cluster = clusters[idx]
    if cluster.speaker_id is None:
        return  # provisional, not yet a real confirmed speaker -- nothing to hint at

    with get_sync_db() as db:
        speaker = db.get(Speaker, UUID(cluster.speaker_id))
        segment = db.get(TranscriptSegment, UUID(segment_id))
        if speaker is None or segment is None:
            return
        payload = {
            "id": str(segment.id),
            "channel": channel.value,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "text": segment.text,
            "speaker_label": speaker.display_label,
            "linked_user_id": str(speaker.linked_user_id) if speaker.linked_user_id else None,
        }

    diar_events.push_event(
        UUID(meeting_id),
        {"type": "speaker_hint", "is_snapshot": False, "removed_segment_ids": [], "segments": [payload]},
        [],
        channel,
    )


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


@celery_app.task(name="corella.enroll_voice")
def enroll_voice(user_id: str) -> None:
    """Extracts a durable voice fingerprint from a user's self-recorded
    Profile sample (POST /api/auth/me/voice) — creates or updates their
    own VoiceIdentity, scoped to their current group (or private/NULL if
    ungrouped). Embedding extraction is torch-dependent, same
    lean-api/heavy-worker split as every other diarization/embedding path.
    Never raises past this function — a failed enrollment just leaves
    voice_enrolled False, not a reason to crash the worker.
    """
    source_path = storage.find_voice_sample_path(UUID(user_id))
    if source_path is None:
        logger.error("enroll_voice: no sample found for user %s", user_id)
        return

    try:
        with tempfile.TemporaryDirectory() as tmp:
            normalized_path = str(Path(tmp) / "normalized.wav")
            _normalize_audio(source_path, normalized_path)
            embedding = embed_utterance(read_wav_pcm(normalized_path))
    except Exception:
        logger.exception("enroll_voice: embedding extraction failed for user %s", user_id)
        return

    with get_sync_db() as db:
        user = db.get(User, UUID(user_id))
        if user is None:
            return

        identity = db.scalar(select(VoiceIdentity).where(VoiceIdentity.linked_user_id == user.id))
        if identity is None:
            identity = VoiceIdentity(linked_user_id=user.id)
            db.add(identity)
        identity.group_id = user.group_id
        identity.display_name = user.full_name
        db.flush()  # assign identity.id before it's used as the Qdrant point id

        try:
            upsert_speaker_embedding(
                identity.id, identity.group_id, identity.linked_user_id, embedding.tolist()
            )
        except Exception:
            logger.exception("enroll_voice: Qdrant upsert failed for user %s", user_id)
            db.rollback()
            return

        db.commit()


async def _disambiguate_display_name(db, base_name: str, group_id: UUID | None) -> str:
    """"Lucas" / "Lucas (2)" / ... — scoped the same way recognition
    itself is scoped (per group, or per-owner when group_id is None), not
    instance-wide: two people named "Lucas" who never share a meeting or
    group never need disambiguating from each other. Suffixes are assigned
    once, at creation, and never reshuffled later — a third "Lucas"
    showing up doesn't change the second one's already-assigned "(2)".
    Async — called only from _identify_speaker_name_async's AsyncSession;
    enroll_voice's sync path never needs disambiguation (an enrolled
    account's own name isn't deduplicated against anything)."""
    existing = set(
        await db.scalars(
            select(VoiceIdentity.display_name).where(VoiceIdentity.group_id == group_id)
        )
    )
    if base_name not in existing:
        return base_name
    n = 2
    while f"{base_name} ({n})" in existing:
        n += 1
    return f"{base_name} ({n})"


_NAME_INFERENCE_SYSTEM_PROMPT = """You are analyzing a short excerpt from a call transcript to figure out one specific speaker's name. Respond with ONLY a single JSON object, no other text:

{
  "name": "<the speaker's first name, ONLY if they clearly introduce themselves or are directly addressed by name in this excerpt, else null>"
}

Never guess. Only return a name you are confident about, stated directly in the transcript."""


@celery_app.task(name="corella.identify_speaker_name")
def identify_speaker_name(meeting_id: str, speaker_id: str, embedding_json: str) -> None:
    """Live, on-the-fly name-spotting for a newly-created, not-yet-
    recognized speaker cluster — dispatched exactly once, right when the
    cluster is first created or promoted (see reconcile_diarization's
    pending_name_inference), never on every subsequent pass that matches an
    already-decided cluster. Reuses the meeting owner's
    resolved LLM provider (resolve_provider/complete(), the same ones the
    live copilot and reports already use — not a separate dedicated
    model), asked to extract a name only if the speaker plainly
    introduces themselves or is addressed by name. One attempt, not a
    retry loop — a real, accepted limitation: someone who doesn't say
    their name until later in the call won't retroactively get identified
    this way.

    First LLM call ever made from a Celery task rather than an API route
    — wraps the async resolve_provider()/complete() calls (and a
    short-lived async SessionLocal, since resolve_provider takes an
    AsyncSession) in asyncio.run(), a self-contained new pattern isolated
    to this one task. Never raises past this function — a failed/skipped
    inference just leaves the speaker anonymous, not a reason to crash
    the worker.
    """
    try:
        asyncio.run(
            _with_engine_cleanup(_identify_speaker_name_async(meeting_id, speaker_id, embedding_json))
        )
    except Exception:
        logger.exception(
            "identify_speaker_name failed for speaker %s in meeting %s", speaker_id, meeting_id
        )


async def _with_engine_cleanup(coro):
    """asyncio.run() gives each call its own fresh event loop — but
    SessionLocal's async engine (app/core/db.py) is a module-level
    singleton whose connection pool holds asyncpg connections bound to
    whichever loop first checked them out. Reused across a *second*
    asyncio.run() call in the same long-lived worker process, that pooled
    connection is bound to a now-dead loop — reproduced live:
    "RuntimeError: ... attached to a different loop" (identify_speaker_name
    was the first task to hit this; every other asyncio.run()-based task
    reuses the same fix rather than re-discovering it). Disposing the
    engine's pool here, at the end of every call (success or failure),
    means the next call always opens fresh connections against the
    current loop instead of reusing stale ones. Returns whatever `coro`
    returns — plain fire-and-forget callers just don't capture it.
    """
    try:
        return await coro
    finally:
        await engine.dispose()


async def _identify_speaker_name_async(meeting_id: str, speaker_id: str, embedding_json: str) -> None:
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, UUID(meeting_id))
        speaker = await db.get(Speaker, UUID(speaker_id))
        if meeting is None or speaker is None:
            return

        segments = list(
            await db.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.speaker_id == speaker.id)
                .order_by(TranscriptSegment.start_ms)
            )
        )
        if not segments:
            return
        transcript_text = "\n".join(s.text for s in segments)

        provider = await resolve_provider(db, meeting.owner_id)
        if provider is None:
            return

        try:
            response = await complete(
                provider.provider,
                provider.model,
                [
                    LLMMessage(role="system", content=_NAME_INFERENCE_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=f"Transcript excerpt:\n{transcript_text}"),
                ],
                provider.api_key,
                provider.base_url,
                max_tokens=60,
            )
        except LLMError as e:
            logger.info("identify_speaker_name: skipped for speaker %s: %s", speaker_id, e)
            return

        cost = estimate_cost_usd(
            provider.provider, provider.model, response.input_tokens, response.output_tokens
        )
        await add_meeting_cost(
            db,
            meeting.id,
            meeting.owner_id,
            provider.provider,
            provider.model,
            response.input_tokens,
            response.output_tokens,
            cost,
            UsageKind.LIVE_CYCLE,
        )
        await db.commit()

        try:
            parsed = parse_json_response(response.text)
        except ValueError as e:
            logger.info("identify_speaker_name: unparseable response for speaker %s: %s", speaker_id, e)
            return

        name = str(parsed.get("name") or "").strip()
        if not name:
            return

        owner_group_id = (await db.get(User, meeting.owner_id)).group_id
        display_name = await _disambiguate_display_name(db, name, owner_group_id)

        identity = VoiceIdentity(group_id=owner_group_id, display_name=display_name)
        db.add(identity)
        await db.flush()  # assign identity.id before it's used as the Qdrant point id / linked below

        embedding = json.loads(embedding_json)
        try:
            upsert_speaker_embedding(identity.id, owner_group_id, None, embedding)
        except Exception:
            logger.exception("identify_speaker_name: Qdrant upsert failed for speaker %s", speaker_id)
            await db.rollback()
            return

        speaker.voice_identity_id = identity.id
        await db.commit()

        # Relabel every segment already shown for this speaker, live — a
        # resolved name is real information worth surfacing immediately,
        # unlike the anonymous "Speaker N"/"Them N" gate (which
        # deliberately waits for 2+ confirmed speakers before showing
        # anything, since a lone number is noise). Reuses the same
        # diar_events queue _poll_diarization_updates already drains.
        await db.refresh(speaker, ["meeting_id"])
        segments = list(
            await db.scalars(
                select(TranscriptSegment).where(TranscriptSegment.speaker_id == speaker.id)
            )
        )
        payload = [
            {
                "id": str(s.id),
                "channel": s.channel.value,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "text": s.text,
                "speaker_label": display_name,
                "linked_user_id": None,
            }
            for s in segments
        ]
        diar_events.push_event(
            meeting.id,
            {
                "type": "diarization_update",
                "is_snapshot": False,
                "removed_segment_ids": [],
                "segments": payload,
            },
            [str(s.id) for s in segments],
            speaker.channel,
        )


@celery_app.task(name="corella.generate_report")
def generate_report_task(meeting_id: str) -> None:
    """Auto-generates the post-call report the moment a meeting reaches
    READY — dispatched fire-and-forget from both process_meeting_audio's
    success path and live_session.py's _finalize() success path, same
    two-call-sites pattern as corella.index_meeting_search. Skips
    silently, not a failure, if no LLM provider is connected or the
    transcript is empty — the existing manual "Generate report" button in
    MeetingDetail.tsx still works whenever the user wants to (re)run it,
    on this meeting or any other.
    """
    try:
        asyncio.run(_with_engine_cleanup(_generate_report_async(meeting_id)))
    except Exception:
        logger.exception("generate_report task failed for meeting %s", meeting_id)


async def _generate_report_async(meeting_id: str) -> None:
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, UUID(meeting_id))
        if meeting is None:
            return

        provider = await resolve_provider(db, meeting.owner_id)
        if provider is None:
            logger.info(
                "generate_report: no LLM provider connected for meeting %s, skipping auto-report",
                meeting_id,
            )
            return

        try:
            result = await run_generate_report(db, meeting, provider)
        except ReportError as e:
            logger.info("generate_report: skipped for meeting %s: %s", meeting_id, e)
            return

        # Only the automatic path fires this — never the manual "Regenerate
        # report" route (api/meetings.py:create_meeting_report), and only
        # after a *successful* report, since the template's placeholders
        # need real summary/report data. dispatch_call_type_webhook is a
        # no-op if this meeting's call type has no webhook configured, and
        # never raises — a broken webhook must never affect the meeting's
        # own success.
        await dispatch_call_type_webhook(db, meeting, result)
