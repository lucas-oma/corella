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

export interface CopilotEvent {
  suggestion: string | null;
  blockers: string[];
  action_items: string[];
  coach_score: number | null;
}

export interface CloseEvent {
  code: CloseCode;
  reason: string;
}

export interface ApiAuthOptions {
  apiBase: string;
  apiKey: string;
}

export interface CreateMeetingOptions {
  apiBase: string;
  apiKey: string;
  title?: string;
  callTypeId?: string | null;
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
  removed_segment_ids?: string[];
  segments?: Array<{
    id: string;
    channel?: LiveChannel | "unknown";
    start_ms?: number;
    end_ms?: number;
    text?: string;
    speaker_label?: string | null;
    linked_user_id?: string | null;
  }>;
}
