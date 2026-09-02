import { API_URL, getToken } from "@/lib/api";

export type LiveChannel = "me" | "them";

const CHANNEL_BYTE: Record<LiveChannel, number> = { me: 0, them: 1 };

export interface TranscriptEvent {
  id: string;
  channel: LiveChannel | "unknown";
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface CopilotEvent {
  suggestion: string | null;
  blockers: string[];
  action_items: string[];
  coach_score: number | null;
}

export interface DiarizedSegment {
  id: string;
  start_ms: number;
  end_ms: number;
  text: string;
  speaker_label: string;
}

export interface DiarizationUpdateEvent {
  /** True the first time 2+ distinct same-room speakers are confirmed for
   * this meeting: a full authoritative snapshot of every labeled "Me"
   * segment so far, not just this cycle's change (an earlier speaker-change
   * split could have happened before anything was ever reported). */
  isSnapshot: boolean;
  removedSegmentIds: string[];
  segments: DiarizedSegment[];
}

export interface CaptureHandle {
  stop: () => void;
}

/** Wires a MediaStream through an AudioWorklet (public/pcm-worklet.js) that
 * downmixes + resamples to 16kHz mono PCM16, invoking `onChunk` with each
 * ~200ms chunk as an Int16Array.
 */
export async function startCapture(
  stream: MediaStream,
  onChunk: (pcm: Int16Array) => void,
): Promise<CaptureHandle> {
  const audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule("/pcm-worklet.js");

  const source = audioContext.createMediaStreamSource(stream);
  const worklet = new AudioWorkletNode(audioContext, "pcm-worklet", {
    processorOptions: { targetSampleRate: 16000, chunkMs: 200 },
  });
  worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
    onChunk(new Int16Array(event.data));
  };
  source.connect(worklet);
  // The worklet has no meaningful audio output, but some browsers only keep
  // a node processing while it's connected all the way to the destination —
  // route it through a silent gain node rather than the real output.
  const silence = audioContext.createGain();
  silence.gain.value = 0;
  worklet.connect(silence);
  silence.connect(audioContext.destination);

  return {
    stop: () => {
      source.disconnect();
      worklet.disconnect();
      silence.disconnect();
      void audioContext.close();
    },
  };
}

/** WebSocket client for /ws/meetings/{id}/live — does the auth handshake,
 * frames outgoing audio, and surfaces incoming transcript/stop/error events.
 */
export class LiveSessionClient {
  private ws: WebSocket;
  private readyPromise: Promise<void>;

  onTranscript: ((event: TranscriptEvent) => void) | null = null;
  onCopilot: ((event: CopilotEvent) => void) | null = null;
  onCopilotUnavailable: (() => void) | null = null;
  onDiarizationUpdate: ((event: DiarizationUpdateEvent) => void) | null = null;
  onStopped: (() => void) | null = null;
  onError: ((message: string) => void) | null = null;

  constructor(meetingId: string) {
    const wsUrl = API_URL.replace(/^http/, "ws");
    this.ws = new WebSocket(`${wsUrl}/ws/meetings/${meetingId}/live`);
    this.ws.binaryType = "arraybuffer";

    this.readyPromise = new Promise((resolve, reject) => {
      this.ws.onopen = () => {
        this.ws.send(JSON.stringify({ type: "auth", token: getToken() ?? "" }));
      };
      this.ws.onmessage = (event) => {
        if (typeof event.data !== "string") return;
        let msg: {
          type?: string;
          segment?: TranscriptEvent;
          message?: string;
          suggestion?: string | null;
          blockers?: string[];
          action_items?: string[];
          coach_score?: number | null;
          is_snapshot?: boolean;
          removed_segment_ids?: string[];
          segments?: DiarizedSegment[];
        };
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg.type === "ready") resolve();
        else if (msg.type === "transcript" && msg.segment) this.onTranscript?.(msg.segment);
        else if (msg.type === "copilot") {
          this.onCopilot?.({
            suggestion: msg.suggestion ?? null,
            blockers: msg.blockers ?? [],
            action_items: msg.action_items ?? [],
            coach_score: msg.coach_score ?? null,
          });
        } else if (msg.type === "copilot_unavailable") this.onCopilotUnavailable?.();
        else if (msg.type === "diarization_update") {
          this.onDiarizationUpdate?.({
            isSnapshot: msg.is_snapshot ?? false,
            removedSegmentIds: msg.removed_segment_ids ?? [],
            segments: msg.segments ?? [],
          });
        } else if (msg.type === "stopped") this.onStopped?.();
        else if (msg.type === "error") this.onError?.(msg.message ?? "Live session error");
      };
      this.ws.onerror = () => reject(new Error("WebSocket connection failed"));
      this.ws.onclose = (event) => {
        if (event.code >= 4000) reject(new Error(event.reason || "Connection rejected"));
      };
    });
  }

  waitUntilReady(): Promise<void> {
    return this.readyPromise;
  }

  sendAudio(channel: LiveChannel, pcm: Int16Array): void {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    const frame = new Uint8Array(1 + pcm.byteLength);
    frame[0] = CHANNEL_BYTE[channel];
    frame.set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength), 1);
    this.ws.send(frame);
  }

  stop(): void {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "stop" }));
    }
  }

  close(): void {
    this.ws.close();
  }
}
