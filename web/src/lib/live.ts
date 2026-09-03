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

/** A disposable, more-frequent preview of a not-yet-committed utterance —
 * replaced in place by the real `transcript` event once the pause/endpoint
 * actually lands. Never persisted, never has an id. */
export interface PartialTranscriptEvent {
  channel: LiveChannel;
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
  channel: LiveChannel;
  start_ms: number;
  end_ms: number;
  text: string;
  speaker_label: string;
  // Set only when speaker_label resolves to an enrolled account, not an
  // anonymous recognized-by-name guest — render "Me" only when this
  // equals the viewer's own id.
  linked_user_id: string | null;
}

/** Admin-only live debug panel event — see the plan's Phase R. Only ever
 * sent for a session where the connected user is an admin *and* has
 * toggled debug on for that session (LiveSessionClient.setDebug). */
export interface DebugEvent {
  stage: string;
  atMs: number;
  detail: Record<string, unknown>;
}

export interface DiarizationUpdateEvent {
  /** True the first time 2+ distinct speakers are confirmed *on one
   * channel* (Me and Them gate independently): a full authoritative
   * snapshot of every labeled segment on that channel so far, not just
   * this cycle's change (an earlier speaker-change split could have
   * happened before anything was ever reported on that channel). */
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

/** Wraps raw 16-bit mono PCM samples into a playable/uploadable WAV Blob —
 * used by Settings.tsx's voice enrollment recorder (the live WS protocol
 * streams raw PCM frames directly and never needs this; a real file
 * upload does).
 */
export function pcmToWavBlob(samples: Int16Array, sampleRate = 16000): Blob {
  const dataSize = samples.length * 2; // 16-bit mono
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  function writeString(offset: number, s: string) {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  }

  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeString(36, "data");
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    view.setInt16(offset, samples[i], true);
  }

  return new Blob([buffer], { type: "audio/wav" });
}

/** WebSocket client for /ws/meetings/{id}/live — does the auth handshake,
 * frames outgoing audio, and surfaces incoming transcript/stop/error events.
 */
export class LiveSessionClient {
  private ws: WebSocket;
  private readyPromise: Promise<void>;

  onTranscript: ((event: TranscriptEvent) => void) | null = null;
  onPartialTranscript: ((event: PartialTranscriptEvent) => void) | null = null;
  onCopilot: ((event: CopilotEvent) => void) | null = null;
  onCopilotUnavailable: (() => void) | null = null;
  onDiarizationUpdate: ((event: DiarizationUpdateEvent) => void) | null = null;
  onDebugEvent: ((event: DebugEvent) => void) | null = null;
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
          channel?: LiveChannel;
          text?: string;
          message?: string;
          suggestion?: string | null;
          blockers?: string[];
          action_items?: string[];
          coach_score?: number | null;
          is_snapshot?: boolean;
          removed_segment_ids?: string[];
          segments?: DiarizedSegment[];
          stage?: string;
          at_ms?: number;
          detail?: Record<string, unknown>;
        };
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg.type === "ready") resolve();
        else if (msg.type === "transcript" && msg.segment) this.onTranscript?.(msg.segment);
        else if (msg.type === "partial_transcript" && msg.channel && msg.text) {
          this.onPartialTranscript?.({ channel: msg.channel, text: msg.text });
        } else if (msg.type === "copilot") {
          this.onCopilot?.({
            suggestion: msg.suggestion ?? null,
            blockers: msg.blockers ?? [],
            action_items: msg.action_items ?? [],
            coach_score: msg.coach_score ?? null,
          });
        } else if (msg.type === "copilot_unavailable") this.onCopilotUnavailable?.();
        else if (msg.type === "diarization_update" || msg.type === "speaker_hint") {
          // A speaker_hint is a fast, read-only "already recognized this
          // voice" guess (corella.quick_label_hint) — same wire shape as
          // the authoritative diarization_update on purpose, so it's
          // handled identically here; a later real diarization_update for
          // the same segment simply overwrites it, self-correcting on the
          // rare case a hint and the real pass ever disagreed.
          this.onDiarizationUpdate?.({
            isSnapshot: msg.is_snapshot ?? false,
            removedSegmentIds: msg.removed_segment_ids ?? [],
            segments: msg.segments ?? [],
          });
        } else if (msg.type === "debug_event" && msg.stage) {
          this.onDebugEvent?.({ stage: msg.stage, atMs: msg.at_ms ?? 0, detail: msg.detail ?? {} });
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

  /** Toggles the admin-only live debug panel for this session — silently
   * ignored server-side unless the connected user is actually an admin. */
  setDebug(enabled: boolean): void {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "debug", enabled }));
    }
  }

  close(): void {
    this.ws.close();
  }
}
