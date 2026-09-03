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
from sqlalchemy import select

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
from app.services.audio.mixing import is_clipped, read_wav_pcm, slice_pcm, write_wav
from app.services.copilot.cost import add_meeting_cost
from app.services.copilot.json_parse import parse_json_response
from app.services.copilot.report import ReportError
from app.services.copilot.report import generate_report as run_generate_report
from app.services.diarization import events as diar_events
from app.services.diarization.cluster import (
    SIMILARITY_THRESHOLD,
    Cluster,
    PendingEmbedding,
    best_match,
    locked_state,
    peek_clusters,
    try_promote_mutual_pair,
    update_centroid,
)
from app.services.diarization.embedding import embed_utterance
from app.services.diarization.pyannote import DiarizationUnavailable, diarize
from app.services.diarization.text_split import words_in_range
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
from app.services.vad.vad import speech_ms, trailing_contiguous_ms
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


def _cluster_and_assign(
    db,
    meeting: Meeting,
    clusters: list[Cluster],
    embedding,
    channel: Channel,
    fallback_embedding=None,
) -> tuple[Speaker, bool, object]:
    """One pre-computed embedding -> one clustering decision -> that
    cluster's Speaker (plus whether it's a *newly created* cluster with no
    resolved identity yet — the caller uses that to decide whether to
    dispatch corella.identify_speaker_name, exactly once per cluster, not
    on every utterance that matches an already-decided one — and whichever
    embedding actually decided this call, so the caller can reuse *that*
    one, not blindly its own original, for anything downstream). Shared by
    both the simple (no-split) and split paths below. Takes embeddings,
    not raw PCM: extraction is slow on a cold model load (the first call
    in a worker process), and this runs inside the per-meeting-per-channel
    Redis lock (locked_state) — embedding *before* acquiring the lock, not
    during, is what keeps that lock's hold time short (verified this
    mattered: a cold-start extraction held inside the lock outlasted its
    10s timeout, so the lock auto-expired mid-hold and releasing it at the
    end raised redis.exceptions.LockNotOwnedError).

    `fallback_embedding` (the caller only computes and passes one when
    `embedding` came from a short utterance — see diarize_utterance) is a
    second, wider-window look at the same channel's already-received audio,
    tried only if `embedding` alone doesn't confidently match anything —
    a short utterance's own embedding is genuinely noisy (verified
    empirically: a real same-speaker 0.5s clip scored 0.53 against its own
    true speaker, just under SIMILARITY_THRESHOLD), so a lone "no match"
    from it shouldn't be trusted enough to permanently mint a new speaker.
    Whichever embedding actually produces a match — or `embedding` itself,
    if neither does — is what gets used consistently for the centroid
    update/new cluster and the durable voice-identity lookup below, so a
    weak primary embedding can't poison either of those once a better one
    was already computed anyway.
    """
    idx, similarity = best_match(clusters, embedding)
    used_embedding = embedding

    if (idx is None or similarity < SIMILARITY_THRESHOLD) and fallback_embedding is not None:
        fb_idx, fb_similarity = best_match(clusters, fallback_embedding)
        if fb_idx is not None and fb_similarity >= SIMILARITY_THRESHOLD:
            idx, similarity, used_embedding = fb_idx, fb_similarity, fallback_embedding

    if idx is not None and similarity >= SIMILARITY_THRESHOLD:
        cluster = clusters[idx]
        update_centroid(cluster, used_embedding)
        speaker = db.get(Speaker, UUID(cluster.speaker_id))
        return speaker, False, used_embedding

    speaker, needs_naming = _create_speaker_cluster(db, meeting, clusters, used_embedding, channel)
    return speaker, needs_naming, used_embedding


def _create_speaker_cluster(
    db, meeting: Meeting, clusters: list[Cluster], embedding, channel: Channel
) -> tuple[Speaker, bool]:
    """A genuinely new cluster for this meeting — before falling back to a
    fresh anonymous "Speaker N"/"Them N", check whether this voice is
    already durably recognized. Shared by _cluster_and_assign's own "no
    match" tail and diarize_utterance's mutual-pending-agreement promotion
    (see try_promote_mutual_pair) — both are "mint a brand-new speaker from
    one embedding" decisions, just reached via different evidence.
    """
    identity = _recognize_voice_identity(db, embedding, meeting.owner.group_id, meeting.owner_id)

    speaker = Speaker(
        owner_id=meeting.owner_id,
        meeting_id=meeting.id,
        label=_SPEAKER_LABEL_FORMAT[channel].format(n=len(clusters) + 1),
        channel=channel,
        voice_identity_id=identity.id if identity else None,
    )
    db.add(speaker)
    db.flush()  # assign speaker.id before the cluster references it
    clusters.append(Cluster(centroid=embedding.tolist(), count=1, speaker_id=str(speaker.id)))
    return speaker, identity is None


def _last_active_speaker(db, meeting_id: UUID, channel: Channel, before_start_ms: int) -> Speaker | None:
    """The most recently resolved speaker on this channel, strictly before
    `before_start_ms` — the "probably still whoever was just talking"
    default for an utterance with genuinely too little reliable signal to
    make its own identity decision (diarize_utterance's insufficient_signal
    handling). None if nothing's been resolved on this channel yet (e.g.
    this is the very first utterance) — there's nothing to default to, so
    the caller leaves the segment unresolved instead, to be picked up by
    the backfill pass below once a real cluster exists.
    """
    return db.scalar(
        select(Speaker)
        .join(TranscriptSegment, TranscriptSegment.speaker_id == Speaker.id)
        .where(
            TranscriptSegment.meeting_id == meeting_id,
            TranscriptSegment.channel == channel,
            TranscriptSegment.start_ms < before_start_ms,
        )
        .order_by(TranscriptSegment.start_ms.desc())
        .limit(1)
    )


def _backfill_unresolved_segments(
    db,
    meeting: Meeting,
    clusters: list[Cluster],
    channel: Channel,
    current_segment: TranscriptSegment,
    window_pcm: bytes,
    window_start_ms_abs: int,
    settings,
) -> list[TranscriptSegment]:
    """Right after a brand-new cluster is created, give any earlier
    unresolved segment on this same channel — one that couldn't be reliably
    placed at the time (diarize_utterance's insufficient_signal, or the
    channel's very first utterance with no prior speaker to default to) —
    one retroactive shot at matching it against whatever clusters exist
    *now*, using diarization_backfill_similarity_threshold rather than the
    stricter bar creating a new speaker requires: the risk here is
    different — worst case a backfilled label is only roughly right, not a
    confidently wrong brand-new identity, and it only ever touches a
    segment that had no label at all.

    Deliberately does not special-case clipped candidates — the similarity
    check itself is the only filter, so a distorted clip that still happens
    to genuinely resemble an established cluster can still be picked up
    (verified live on the exact motivating case: a real clipped clip scored
    only 0.12 against the correct speaker, well under even this lenient
    bar — so it stays unresolved there, correctly, not because it was
    excluded outright but because it honestly didn't match).

    Only considers segments whose own audio still falls within `window_pcm`
    (the window just dispatched for `current_segment`) — an unresolved
    segment further back than diarization_context_window_ms isn't
    re-embeddable here without audio this task was never given; it settles
    to the frontend's own generic fallback instead (see MeetingDetail.tsx).
    """
    candidates = db.scalars(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.meeting_id == meeting.id,
            TranscriptSegment.channel == channel,
            TranscriptSegment.speaker_id.is_(None),
            TranscriptSegment.id != current_segment.id,
            TranscriptSegment.start_ms >= window_start_ms_abs,
            TranscriptSegment.start_ms < current_segment.start_ms,
        )
        .order_by(TranscriptSegment.start_ms)
    ).all()

    backfilled: list[TranscriptSegment] = []
    for candidate in candidates:
        candidate_pcm = slice_pcm(
            window_pcm, candidate.start_ms - window_start_ms_abs, candidate.end_ms - candidate.start_ms
        )
        if not candidate_pcm:
            continue
        embedding = embed_utterance(candidate_pcm)
        idx, similarity = best_match(clusters, embedding)
        if idx is None or similarity < settings.diarization_backfill_similarity_threshold:
            continue
        cluster = clusters[idx]
        update_centroid(cluster, embedding)
        candidate.speaker = db.get(Speaker, UUID(cluster.speaker_id))
        backfilled.append(candidate)
    return backfilled


def _resolve_pending(
    db, meeting: Meeting, clusters: list[Cluster], pending: list[PendingEmbedding], channel: Channel, settings
) -> tuple[list[tuple[TranscriptSegment, str]], list[tuple[UUID, list]]]:
    """Every utterance's turn to try clearing the deferred queue (see
    PendingEmbedding's own docstring) — not just the utterance that just
    deferred, or only right after a brand-new cluster forms: an ordinary
    match on this round can move a centroid close enough to resolve a much
    older pending embedding, and two pending embeddings can mutually
    corroborate each other even without a fresh cluster update at all.

    Two passes, run to a fixed point (mutual promotion can make more
    matches possible, which is worth one more matching pass before this
    task ends): (1) match each pending embedding against current clusters
    at the lenient backfill bar, same as _backfill_unresolved_segments; (2)
    mint a brand-new cluster from any pair of pending embeddings that
    mutually agree with each other (try_promote_mutual_pair) — the only way
    a second cluster can ever form once a first one exists under this
    scheme; see that function's own docstring for why it's required, not
    just an enhancement.

    Returns (resolved segments for the WS payload, newly-created speakers
    needing corella.identify_speaker_name).
    """
    resolved: list[tuple[TranscriptSegment, str]] = []
    newly_named: list[tuple[UUID, list]] = []

    changed = True
    while changed:
        changed = False

        still_pending: list[PendingEmbedding] = []
        for entry in pending:
            embedding = np.array(entry.embedding)
            idx, similarity = best_match(clusters, embedding)
            if idx is not None and similarity >= settings.diarization_backfill_similarity_threshold:
                cluster = clusters[idx]
                update_centroid(cluster, embedding)
                seg = db.get(TranscriptSegment, UUID(entry.segment_id))
                if seg is not None:
                    seg.speaker = db.get(Speaker, UUID(cluster.speaker_id))
                    resolved.append((seg, seg.speaker.display_label))
                changed = True
            else:
                still_pending.append(entry)
        pending[:] = still_pending

        pair = try_promote_mutual_pair(pending)
        if pair is not None:
            a, b = pair
            ea, eb = np.array(pending[a].embedding), np.array(pending[b].embedding)
            speaker, needs_naming = _create_speaker_cluster(db, meeting, clusters, ea, channel)
            update_centroid(clusters[-1], eb)
            if needs_naming:
                newly_named.append((speaker.id, eb.tolist()))
            for entry_idx in (a, b):
                seg = db.get(TranscriptSegment, UUID(pending[entry_idx].segment_id))
                if seg is not None:
                    seg.speaker = speaker
                    resolved.append((seg, speaker.display_label))
            pending[:] = [e for i, e in enumerate(pending) if i not in (a, b)]
            changed = True

    return resolved, newly_named


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

    with get_sync_db() as db:
        meeting = db.get(Meeting, UUID(meeting_id))
        segment = db.get(TranscriptSegment, UUID(segment_id))
        if meeting is None or segment is None:
            return
        # Read off the segment itself, not passed as a task arg — can't
        # drift from the real value. Captured before any possible deletion
        # below (the split path deletes `segment`).
        channel = segment.channel

        # Cheap whole-utterance embedding, extracted first and unconditionally
        # — before the per-meeting lock, and before deciding whether the
        # expensive diarize() pass below is even necessary. On its own this
        # is often already confident enough to place the utterance (the same
        # person is still talking, the overwhelmingly common case in
        # ordinary conversation), letting the full pipeline be skipped
        # entirely — see diarization_skip_confidence in app/core/config.py.
        # Reused below as-is when no split turns out to be needed, so this
        # is never redundant work even on the slow path.
        utterance_pcm = slice_pcm(window_pcm, utterance_offset_ms, utterance_duration_ms)
        whole_embedding = embed_utterance(utterance_pcm)

        settings = get_settings()

        # window_pcm always ends exactly at this utterance's own end (that's
        # how live_session.py builds it — extract_channel_window is called
        # with the utterance's own end_ms as the window's end), and
        # utterance_offset_ms/utterance_duration_ms are relative to
        # window_pcm's own start rather than absolute — this converts that
        # relative frame back to the session's real absolute-ms timeline,
        # needed below to scope the backfill pass to segments this window
        # actually has audio for.
        window_start_ms_abs = segment.end_ms - utterance_offset_ms - utterance_duration_ms

        # best_match returns (None, -1.0) when there are no clusters yet for
        # this channel — correctly never confident, so a meeting's very
        # first utterance on a channel always gets the careful full pass.
        _best_idx, best_sim = best_match(peek_clusters(meeting.id, channel), whole_embedding)
        confident_single_speaker = best_sim >= settings.diarization_skip_confidence

        # A lone "doesn't match anything confidently" verdict from this
        # utterance's own embedding doesn't get to mint a new speaker
        # outright — try a second, wider-window look first (computed here,
        # before the lock, same discipline as whole_embedding itself —
        # never do slow model inference while holding locked_state's
        # lock). `best_sim` above is a lock-free peek (may be a cluster or
        # two stale by the time _cluster_and_assign runs for real), so this
        # is a heuristic trigger, not a guarantee — worst case, an
        # unnecessary embedding gets computed, or a genuinely-warranted one
        # doesn't; _cluster_and_assign's own real decision still always
        # goes through the fresh, locked cluster state.
        #
        # Originally gated the *trigger* on content thinness (too little
        # real speech, e.g. a clip that's mostly silence padding — verified
        # empirically that raw wall-clock duration alone is NOT the right
        # signal here) rather than any miss — reproduced live, against real
        # Deepgram-chunked ground-truth audio (not just synthetic short
        # chops), that this was too narrow: a genuinely NOT-thin utterance
        # can still score just under SIMILARITY_THRESHOLD from ordinary
        # embedding variance (real examples: 0.524, 0.544, both against the
        # correct matching speaker), and a thinness-only trigger never gave
        # those a second look at all — an instant, permanent new speaker on
        # a single narrow miss. Clipping (verified empirically — see
        # is_clipped's docstring; a distorted clip scored 0.12 against the
        # same real speaker's own clean speech) still always warrants a
        # second look too, independent of the match score.
        #
        # Raw similarity score decides whether to *attempt* a second look
        # (any miss against an existing cluster), but deliberately still
        # does NOT decide whether the utterance's own audio quality can be
        # *trusted* once that second look actually runs (see is_clipped
        # below) — tried a purely score-based trust check too and rejected
        # it after a real counter-example: two different genuinely-short
        # real utterances — one a different speaker, one the *same*
        # speaker caught by a nearby real gap — scored the identical 0.141
        # against their respective closest cluster. Raw score alone cannot
        # tell those apart at this duration.
        fallback_embedding = None
        # True only when the utterance is clipped and even the wider look
        # couldn't rescue it — clipped audio isn't merely weak evidence,
        # it's been shown to actively mismatch everything it's compared
        # against (0.12 against the correct speaker in the case that
        # motivated this), so it's left unresolved rather than either
        # inventing a new identity from it or guessing whose it is. A
        # thin-but-clean clip that similarly can't be rescued is NOT routed
        # here — see the "falls through unchanged" branch below for why.
        insufficient_signal = False

        utterance_clipped = is_clipped(utterance_pcm)
        has_existing_clusters = best_sim > -1.0  # -1.0 sentinel: no clusters on this channel at all yet
        # Originally gated on content thinness (speech_ms < diarization_short_
        # utterance_ms) as well as clipping — reproduced live, against real
        # Deepgram-chunked ground-truth audio (not just synthetic short
        # chops), that this was too narrow: a genuinely NOT-thin utterance
        # (1.5-2s of real speech) can still score just under
        # SIMILARITY_THRESHOLD from ordinary embedding variance (real
        # examples: 0.524, 0.544, both against the correct matching
        # speaker) — and since "too little content" never fires for it, no
        # second look is ever attempted; a single narrow miss mints a
        # permanent new speaker. The fix: any miss against an existing
        # cluster warrants a second look, not only a thin/clipped one — a
        # channel's ordinary first-ever utterance still never triggers this
        # (has_existing_clusters is False, nothing to have failed to match
        # against yet), matching how this has always worked.
        needs_second_look = utterance_clipped or (has_existing_clusters and best_sim < SIMILARITY_THRESHOLD)
        if needs_second_look and best_sim < SIMILARITY_THRESHOLD:
            # A blind fixed-duration trailing window is NOT safe here —
            # reproduced live: widening straight into audio that actually
            # belongs to the *previous* speaker's own turn (across the very
            # silence gap that caused this utterance's own boundary in the
            # first place) blended two real people's voices into one
            # embedding, which then confidently matched the WRONG existing
            # cluster (0.78 similarity to the wrong speaker, verified on
            # real audio) instead of correctly finding no match. Capping the
            # window at the previous *committed segment's* own boundary
            # instead was tried and also rejected — verified live that it
            # collapses to zero extra context whenever utterances are
            # dispatched back-to-back with no gap at all, which is the
            # common case this whole mechanism exists for. A real detected
            # silence gap (trailing_contiguous_ms, the same
            # SILENCE_TO_FLUSH_MS-based signal local VAD itself uses to
            # decide "this is a real pause") is the actual right signal:
            # widen freely through genuinely continuous speech, stop at the
            # first real pause. Cheap — no ML model involved, unlike the
            # embedding extraction it's gating.
            contiguous_ms = trailing_contiguous_ms(
                window_pcm,
                settings.live_vad_aggressiveness,
                max_ms=settings.diarization_corroboration_window_ms,
            )
            utterance_end_within_window_ms = utterance_offset_ms + utterance_duration_ms
            corroboration_start_ms = max(0, utterance_end_within_window_ms - contiguous_ms)
            corroboration_pcm = slice_pcm(
                window_pcm, corroboration_start_ms, utterance_end_within_window_ms - corroboration_start_ms
            )
            # Only usable if widening actually gives more (real, clean)
            # audio than the utterance's own span already was — early in a
            # session, or right after a real speaker change, there may be
            # little to no extra *same-speaker* context available yet, and
            # if the utterance itself was clipped rather than short, a
            # corroboration window with too little of its own real speech
            # or that's *also* clipped is no better than what we started
            # with.
            corroboration_usable = (
                len(corroboration_pcm) > len(utterance_pcm)
                and speech_ms(corroboration_pcm, settings.live_vad_aggressiveness)
                >= settings.diarization_corroboration_min_speech_ms
                and not is_clipped(corroboration_pcm)
            )
            if corroboration_usable:
                fallback_embedding = embed_utterance(corroboration_pcm)
            elif utterance_clipped:
                insufficient_signal = True
            # else: thin-but-clean content, no rescue available, not
            # clipped — falls through unchanged to _cluster_and_assign
            # below with fallback_embedding still None, same as this
            # codebase has always done. Deliberately does NOT default to
            # "probably still whoever was last talking" here: verified
            # live that doing so misattributed a genuinely different
            # speaker's own utterance (the exact 0.141-score
            # counter-example above) to the wrong existing speaker — a
            # false merge, which this codebase has never risked before and
            # is a worse failure than the spurious-extra-speaker case this
            # mechanism exists to reduce. Creating a new speaker here can
            # occasionally still be wrong (a real trailing utterance with
            # no rescuable context, verified live on one such case), but
            # never merges two different real people's words together —
            # the safer of the two error types.

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
        if confident_single_speaker or window_duration_ms < 9000:
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

        # Per-turn embedding extraction still happens here, before the
        # per-meeting lock below is acquired — it's the slow part (a cold
        # model load can take several seconds) and doesn't need cross-task
        # exclusivity; only the actual cluster-decision-and-write does.
        # Verified this ordering matters: a cold-start extraction held
        # *inside* the lock outlasted its 10s timeout, so Redis auto-expired
        # it mid-hold and releasing it at the end raised
        # redis.exceptions.LockNotOwnedError.
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
        # silently dropping it. whole_embedding was already computed above
        # unconditionally, so the non-split path below just reuses it.
        did_split = len(split_turns) >= 2

        # (speaker_id, embedding) pairs to dispatch corella.identify_speaker_name
        # for, once outside the lock/transaction — a brand-new cluster this
        # round with no durable identity resolved for it yet. The embedding
        # rides along rather than being re-extracted later, so a resolved
        # name can be upserted straight into the durable library.
        pending_name_inference: list[tuple[UUID, list]] = []

        # True if any speaker decided on this round already has a resolved
        # cross-meeting identity — checked at the Speaker level (not
        # TranscriptSegment, which has no voice_identity_id of its own) as
        # each one is decided below.
        has_resolved_identity = False

        try:
            with locked_state(meeting.id, channel) as (clusters, pending):
                resulting: list[tuple[TranscriptSegment, str]] = []

                if not did_split and insufficient_signal:
                    # Neither this utterance's own audio nor a wider look
                    # had enough reliable signal to make any identity
                    # decision at all — inventing a brand-new speaker from
                    # that would be worse than not deciding (verified live,
                    # see insufficient_signal's docstring above). Default to
                    # whoever was last talking on this channel; if nobody
                    # has been resolved on this channel yet (this segment is
                    # itself the very first utterance, or every earlier one
                    # was *also* unreliable), there's nothing to default to
                    # — leave it unresolved, to be picked up by the backfill
                    # pass below once a real cluster exists to check it
                    # against.
                    speaker = _last_active_speaker(db, meeting.id, channel, segment.start_ms)
                    if speaker is not None:
                        segment.speaker = speaker
                        resulting.append((segment, speaker.display_label))
                        has_resolved_identity = has_resolved_identity or speaker.voice_identity_id is not None
                elif not did_split:
                    # Dry-run the exact same primary+corroboration match
                    # _cluster_and_assign would do, to decide BEFORE
                    # committing whether it's about to mint a new cluster
                    # from one weak embedding — the actual mechanism behind
                    # over-segmentation that survived the broadened
                    # second-look trigger above (a genuinely thin or
                    # cross-a-real-pause utterance whose corroboration
                    # attempt still doesn't clear SIMILARITY_THRESHOLD).
                    # `clusters` non-empty is required — a channel's very
                    # first-ever cluster is still created outright, nothing
                    # to defer against yet.
                    dry_idx, dry_sim = best_match(clusters, whole_embedding)
                    would_match = dry_idx is not None and dry_sim >= SIMILARITY_THRESHOLD
                    if not would_match and fallback_embedding is not None:
                        fb_dry_idx, fb_dry_sim = best_match(clusters, fallback_embedding)
                        would_match = fb_dry_idx is not None and fb_dry_sim >= SIMILARITY_THRESHOLD

                    if not would_match and clusters:
                        # Defer rather than mint a new cluster outright —
                        # verified live that doing so DIRECTLY (no
                        # corroborating second opinion) causes real
                        # over-segmentation; see PendingEmbedding's own
                        # docstring for what happens to it from here.
                        pending.append(
                            PendingEmbedding(segment_id=str(segment.id), embedding=whole_embedding.tolist())
                        )
                    else:
                        speaker, needs_naming, used_embedding = _cluster_and_assign(
                            db, meeting, clusters, whole_embedding, channel, fallback_embedding
                        )
                        # Assign the relationship object, not just the FK
                        # column — segment.speaker was already loaded (as
                        # None) when it was fetched above, and setting
                        # speaker_id alone doesn't refresh that
                        # already-cached relationship. Matters here because
                        # the backfill query below can re-select this exact
                        # row later in the same session/identity map.
                        segment.speaker = speaker
                        resulting.append((segment, speaker.display_label))
                        if needs_naming:
                            pending_name_inference.append((speaker.id, used_embedding.tolist()))
                        has_resolved_identity = has_resolved_identity or speaker.voice_identity_id is not None

                        # A brand-new (non-deferred) cluster — see if any
                        # earlier segment on this same channel that
                        # couldn't be reliably placed at the time
                        # (insufficient_signal above, on some earlier
                        # utterance) can now be backfilled against it, or
                        # against any other cluster it might genuinely
                        # match — using a more lenient bar than creating a
                        # new speaker requires (see
                        # diarization_backfill_similarity_threshold).
                        backfilled = _backfill_unresolved_segments(
                            db, meeting, clusters, channel, segment, window_pcm, window_start_ms_abs, settings
                        )
                        resulting.extend((s, s.speaker.display_label) for s in backfilled)
                        has_resolved_identity = has_resolved_identity or any(
                            s.speaker.voice_identity_id is not None for s in backfilled
                        )

                    # Every utterance gets a chance to clear the deferred
                    # queue, not just the one that just deferred — an
                    # ordinary match above can move a centroid close enough
                    # to resolve a much older pending embedding, and two
                    # pending embeddings can mutually corroborate each
                    # other into a brand-new cluster (see
                    # try_promote_mutual_pair — the only way a 2nd cluster
                    # can ever form once a 1st exists under this scheme).
                    pending_resolved, pending_named = _resolve_pending(
                        db, meeting, clusters, pending, channel, settings
                    )
                    resulting.extend(pending_resolved)
                    pending_name_inference.extend(pending_named)
                    has_resolved_identity = has_resolved_identity or any(
                        s.speaker.voice_identity_id is not None for s, _ in pending_resolved
                    )
                else:
                    base_start_ms = segment.start_ms
                    for rel_start, rel_end, text, embedding in split_turns:
                        speaker, needs_naming, used_embedding = _cluster_and_assign(
                            db, meeting, clusters, embedding, channel
                        )
                        new_row = TranscriptSegment(
                            meeting_id=meeting.id,
                            speaker_id=speaker.id,
                            channel=channel,
                            start_ms=base_start_ms + round(rel_start * 1000),
                            end_ms=base_start_ms + round(rel_end * 1000),
                            text=text,
                            is_partial=False,
                        )
                        db.add(new_row)
                        resulting.append((new_row, speaker.display_label))
                        if needs_naming:
                            pending_name_inference.append((speaker.id, embedding.tolist()))
                        has_resolved_identity = (
                            has_resolved_identity or speaker.voice_identity_id is not None
                        )
                    db.delete(segment)
                    # Recorded regardless of whether the gate is open yet —
                    # if it opens later, the backfill snapshot below still
                    # needs to know this id is gone, not just future ones.
                    diar_events.record_removed(meeting.id, str(segment.id), channel)

                db.flush()  # assign ids to any new rows before building the WS payload

                # The anonymous "2+ speakers" gate exists so a lone number
                # never shows for a single-person channel — but a *resolved*
                # identity (recognized durably, on even the very first
                # utterance) is real information, not noise, so it bypasses
                # that gate entirely rather than waiting for a second voice
                # that may never come.
                if len(clusters) >= 2 or has_resolved_identity:
                    if not diar_events.has_reported_anything(meeting.id, channel):
                        # First time the gate has ever opened for this
                        # meeting *on this channel* — a full authoritative
                        # snapshot, not an incremental diff, so the frontend
                        # can't miss an earlier split that happened before
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
                            {
                                "type": "diarization_update",
                                "is_snapshot": True,
                                "removed_segment_ids": diar_events.all_removed(meeting.id, channel),
                                "segments": payload,
                            },
                            [str(s.id) for s in all_labeled],
                            channel,
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
                            channel,
                        )
            db.commit()
        except Exception:
            logger.exception("diarize_utterance: clustering failed for segment %s", segment_id)
            db.rollback()
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
    cluster is first created (see diarize_utterance's
    pending_name_inference), never on every subsequent utterance that
    matches an already-decided cluster. Reuses the meeting owner's
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
