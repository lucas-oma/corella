from app.models.meeting import Channel

# "Speaker N" is the original, already-shipped Me-side format — unchanged,
# so nothing that already depends on it (frontend dot-color parsing,
# existing meetings' persisted labels) breaks. Them gets its own distinct
# prefix, not the same "Speaker N": MeetingDetail.tsx lists every segment's
# speaker_label in one flat list with no channel column, so two unrelated
# people (one from each pool) both reading as "Speaker 1" would be a real,
# avoidable ambiguity.
#
# Shared by app/workers/tasks.py (the pyannote-embedding registry path) and
# app/ws/live_session.py (Phase W3's Deepgram-native diarization path) —
# lives in its own tiny, zero-heavy-dependency module rather than either of
# those two so live_session.py (imported by the api process) never needs to
# import anything from app.workers.tasks (which pulls in the full worker
# dependency stack at module level).
SPEAKER_LABEL_FORMAT = {
    Channel.ME: "Speaker {n}",
    Channel.THEM: "Them {n}",
}
