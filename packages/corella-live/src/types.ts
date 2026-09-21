export type LiveChannel = "me" | "them";

export const CHANNEL_BYTE: Record<LiveChannel, number> = { me: 0, them: 1 };

/** WebSocket close codes the Corella live endpoint uses. */
export const CLOSE = {
  AUTH: 4401,
  NOT_FOUND: 4404,
  BUSY_OR_ENDED: 4409,
  DURATION_LIMIT: 4410,
} as const;

export type CloseCode = (typeof CLOSE)[keyof typeof CLOSE] | number;

export interface TranscriptLine {
  id: string;
  channel: LiveChannel | "unknown";
  start_ms: number;
  end_ms: number;
  text: string;
  /** Resolved name, or the channel (`me`/`them`) if none yet. */
  speakerLabel: string;
  linkedUserId: string | null;
}

export const SENTIMENTS = [
  "Hostile",
  "Tense",
  "Frustrated",
  "Skeptical",
  "Neutral",
  "Engaged",
  "Positive",
  "Enthusiastic",
] as const;

export type Sentiment = (typeof SENTIMENTS)[number];

export function parseSentiment(raw: unknown): Sentiment | null {
  if (typeof raw !== "string") return null;
  const needle = raw.trim().toLowerCase();
  return SENTIMENTS.find((value) => value.toLowerCase() === needle) ?? null;
}

/** Discrete fill 0..1 — Hostile empty, Enthusiastic full. */
export function sentimentFill(value: Sentiment): number {
  return SENTIMENTS.indexOf(value) / (SENTIMENTS.length - 1);
}

export interface SpeakerShareSlice {
  label: string;
  pct: number;
}

export interface CopilotEvent {
  suggestion: string | null;
  blockers: string[];
  action_items: string[];
  coach_score: number | null;
  sentiment: Sentiment | null;
  speaker_share: SpeakerShareSlice[] | null;
}

export interface CloseEvent {
  code: CloseCode;
  reason: string;
}

export interface ApiAuthOptions {
  apiBase: string;
  apiKey: string;
}

export type CaptureMode = "open_mic" | "meeting_tab" | "upload";
export type CaptureApp = "meet" | "teams" | "zoom" | "other";

export interface CreateMeetingOptions {
  apiBase: string;
  apiKey: string;
  title?: string;
  callTypeId?: string | null;
  /** How audio arrives. Defaults to open_mic on the server if omitted. */
  captureMode?: CaptureMode;
  captureApp?: CaptureApp | null;
}

export interface ConnectOptions {
  apiBase: string;
  apiKey: string;
  meetingId: string;
}

export interface StartOptions extends CreateMeetingOptions {
  /** If set, skip create and attach to this meeting (must still be `recording`). */
  meetingId?: string;
}

export interface CallTypeOption {
  id: string;
  name: string;
  slug: string;
  is_default: boolean;
}

export interface MeetingCreated {
  id: string;
  title: string;
  status: string;
}

export interface LiveMessage {
  type?: string;
  segment?: {
    id: string;
    channel?: LiveChannel | "unknown";
    start_ms?: number;
    end_ms?: number;
    text?: string;
    speaker_label?: string | null;
  };
  channel?: LiveChannel;
  text?: string;
  suggestion?: string | null;
  blockers?: string[];
  action_items?: string[];
  coach_score?: number | null;
  sentiment?: string | null;
  speaker_share?: Array<{ label?: string; pct?: number }> | null;
  removed_segment_ids?: string[];
  segments?: Array<{
    id: string;
    channel?: LiveChannel | "unknown";
    start_ms?: number;
    end_ms?: number;
    text?: string;
    speaker_label?: string | null;
    linked_user_id?: string | null;
    speaker_id?: string | null;
  }>;
}
