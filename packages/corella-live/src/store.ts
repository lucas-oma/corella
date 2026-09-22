import { parseSentiment, type LiveChannel, type LiveMessage, type Sentiment, type SpeakerShareSlice, type TranscriptLine } from "./types.js";

export interface LiveStore {
  segments: Map<string, { id: string; channel: LiveChannel | "unknown"; start_ms: number; end_ms: number; text: string }>;
  speakerLabels: Map<string, string>;
  linkedUserIds: Map<string, string | null>;
  partials: Record<LiveChannel, string>;
  copilot: {
    suggestion: string | null;
    blockers: string[];
    action_items: string[];
    coach_score: number | null;
    sentiment: Sentiment | null;
    speaker_share: SpeakerShareSlice[] | null;
  } | null;
  copilotAvailable: boolean;
}

export function createStore(): LiveStore {
  return {
    segments: new Map(),
    speakerLabels: new Map(),
    linkedUserIds: new Map(),
    partials: { me: "", them: "" },
    copilot: null,
    copilotAvailable: true,
  };
}

export function resetStore(store: LiveStore): void {
  store.segments.clear();
  store.speakerLabels.clear();
  store.linkedUserIds.clear();
  store.partials.me = "";
  store.partials.them = "";
  store.copilot = null;
  store.copilotAvailable = true;
}

function parseSpeakerShare(raw: LiveMessage["speaker_share"]): SpeakerShareSlice[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const slices: SpeakerShareSlice[] = [];
  for (const row of raw) {
    if (!row || typeof row.label !== "string" || typeof row.pct !== "number") continue;
    slices.push({ label: row.label, pct: row.pct });
  }
  return slices.length > 0 ? slices : null;
}

/**
 * Apply one server text frame. Labels live off the transcript row —
 * `transcript` has no `speaker_label`, and on Deepgram it often arrives
 * *after* `diarization_update` for the same id. Storing the name on the
 * same object and then overwriting with the transcript event wipes every
 * split back to `me`.
 */
export function applyLiveMessage(store: LiveStore, msg: LiveMessage): void {
  switch (msg.type) {
    case "transcript": {
      const seg = msg.segment;
      if (!seg?.id) return;
      store.segments.set(seg.id, {
        id: seg.id,
        channel: seg.channel ?? "unknown",
        start_ms: seg.start_ms ?? 0,
        end_ms: seg.end_ms ?? 0,
        text: seg.text ?? "",
      });
      if (seg.channel === "me" || seg.channel === "them") {
        store.partials[seg.channel] = "";
      }
      break;
    }
    case "partial_transcript":
      if (msg.channel === "me" || msg.channel === "them") {
        store.partials[msg.channel] = msg.text ?? "";
      }
      break;
    case "copilot":
      store.copilot = {
        suggestion: msg.suggestion ?? null,
        blockers: msg.blockers ?? [],
        action_items: msg.action_items ?? [],
        coach_score: msg.coach_score ?? null,
        sentiment: parseSentiment(msg.sentiment),
        speaker_share: parseSpeakerShare(msg.speaker_share),
      };
      break;
    case "copilot_unavailable":
      store.copilotAvailable = false;
      break;
    case "diarization_update":
    case "speaker_hint":
      for (const id of msg.removed_segment_ids ?? []) {
        store.segments.delete(id);
        store.speakerLabels.delete(id);
        store.linkedUserIds.delete(id);
      }
      for (const seg of msg.segments ?? []) {
        store.segments.set(seg.id, {
          id: seg.id,
          channel: seg.channel ?? "unknown",
          start_ms: seg.start_ms ?? 0,
          end_ms: seg.end_ms ?? 0,
          text: seg.text ?? "",
        });
        if (seg.speaker_label) store.speakerLabels.set(seg.id, seg.speaker_label);
        if (seg.linked_user_id !== undefined) {
          store.linkedUserIds.set(seg.id, seg.linked_user_id);
        }
      }
      break;
    default:
      break;
  }
}

export function snapshotTranscript(store: LiveStore): TranscriptLine[] {
  return [...store.segments.values()]
    .sort((a, b) => a.start_ms - b.start_ms)
    .map((s) => ({
      id: s.id,
      channel: s.channel,
      start_ms: s.start_ms,
      end_ms: s.end_ms,
      text: s.text,
      speakerLabel: store.speakerLabels.get(s.id) || s.channel,
      linkedUserId: store.linkedUserIds.get(s.id) ?? null,
    }));
}
