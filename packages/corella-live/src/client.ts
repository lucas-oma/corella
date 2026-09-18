import { framePcm, startMic, type MicHandle } from "./pcm.js";
import { applyLiveMessage, createStore, snapshotTranscript, type LiveStore } from "./store.js";
import type {
  ApiAuthOptions,
  CallTypeOption,
  CloseEvent,
  ConnectOptions,
  CopilotEvent,
  CreateMeetingOptions,
  LiveChannel,
  LiveMessage,
  MeetingCreated,
  StartOptions,
  TranscriptLine,
} from "./types.js";

function trimBase(apiBase: string): string {
  return apiBase.replace(/\/$/, "");
}

async function apiFetch(opts: ApiAuthOptions, path: string, init?: RequestInit): Promise<Response> {
  const apiBase = trimBase(opts.apiBase);
  try {
    return await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${opts.apiKey}`,
        ...(init?.headers ?? {}),
      },
    });
  } catch (err) {
    const hint =
      "Could not reach the API. From a browser, this origin must be listed in the Corella instance's CORS_ORIGINS (or call from your server).";
    throw new Error(`${hint} ${err instanceof Error ? err.message : ""}`.trim());
  }
}

function wsUrl(apiBase: string, meetingId: string): string {
  return `${trimBase(apiBase).replace(/^http/, "ws")}/ws/meetings/${meetingId}/live`;
}

export async function listCallTypes(opts: ApiAuthOptions): Promise<CallTypeOption[]> {
  const response = await apiFetch(opts, "/api/call-types");
  const body = (await response.json().catch(() => [])) as CallTypeOption[] | { detail?: unknown };
  if (!response.ok) {
    const detail = typeof (body as { detail?: unknown }).detail === "string"
      ? (body as { detail: string }).detail
      : JSON.stringify(body);
    throw new Error(`List call types failed (${response.status}): ${detail}`);
  }
  return body as CallTypeOption[];
}

export async function createMeeting(opts: CreateMeetingOptions): Promise<MeetingCreated> {
  const response = await apiFetch(opts, "/api/meetings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: opts.title || "Untitled meeting",
      call_type_id: opts.callTypeId ?? null,
    }),
  });

  const body = (await response.json().catch(() => ({}))) as { id?: string; title?: string; status?: string; detail?: unknown };
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    throw new Error(`Create meeting failed (${response.status}): ${detail}`);
  }
  if (!body.id) throw new Error("Create meeting returned no id");
  return { id: body.id, title: body.title ?? "", status: body.status ?? "recording" };
}

export class CorellaLive {
  readonly meetingId: string;

  private _onTranscript: ((lines: TranscriptLine[]) => void) | null = null;
  private _onPartial: ((partials: { me: string; them: string }) => void) | null = null;
  private _onCopilot: ((event: CopilotEvent) => void) | null = null;
  private _onCopilotUnavailable: (() => void) | null = null;
  private _onClose: ((event: CloseEvent) => void) | null = null;

  private readonly store: LiveStore = createStore();
  private readonly ws: WebSocket;
  private readonly ready: Promise<void>;
  private mic: MicHandle | null = null;
  private resolveReady!: () => void;
  private rejectReady!: (err: Error) => void;
  private readySettled = false;

  private constructor(opts: ConnectOptions) {
    this.meetingId = opts.meetingId;
    this.ws = new WebSocket(wsUrl(opts.apiBase, opts.meetingId));
    this.ws.binaryType = "arraybuffer";

    this.ready = new Promise((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
    });

    this.ws.onopen = () => {
      this.ws.send(JSON.stringify({ type: "auth", api_key: opts.apiKey }));
    };
    this.ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      let msg: LiveMessage;
      try {
        msg = JSON.parse(event.data) as LiveMessage;
      } catch {
        return;
      }
      if (msg.type === "ready") {
        this.settleReady();
        return;
      }
      if (msg.type === "stopped") return;
      const beforePartial = `${this.store.partials.me}\0${this.store.partials.them}`;
      const beforeCopilot = this.store.copilot;
      const beforeAvail = this.store.copilotAvailable;
      applyLiveMessage(this.store, msg);
      if (msg.type === "copilot_unavailable" && beforeAvail && !this.store.copilotAvailable) {
        this._onCopilotUnavailable?.();
      }
      if (msg.type === "copilot" && this.store.copilot !== beforeCopilot) {
        this._onCopilot?.(this.store.copilot!);
      }
      const afterPartial = `${this.store.partials.me}\0${this.store.partials.them}`;
      if (afterPartial !== beforePartial) {
        this._onPartial?.({ ...this.store.partials });
      }
      if (
        msg.type === "transcript" ||
        msg.type === "diarization_update" ||
        msg.type === "speaker_hint"
      ) {
        this._onTranscript?.(snapshotTranscript(this.store));
      }
    };
    this.ws.onerror = () => {
      if (!this.readySettled) this.rejectReady(new Error("WebSocket connection failed"));
    };
    this.ws.onclose = (event) => {
      this.stopMic();
      const close: CloseEvent = { code: event.code, reason: event.reason || "" };
      if (!this.readySettled) {
        this.rejectReady(new Error(close.reason || `Connection closed (${close.code})`));
      }
      this._onClose?.(close);
    };
  }

  onTranscript(handler: (lines: TranscriptLine[]) => void): this {
    this._onTranscript = handler;
    return this;
  }

  onPartial(handler: (partials: { me: string; them: string }) => void): this {
    this._onPartial = handler;
    return this;
  }

  onCopilot(handler: (event: CopilotEvent) => void): this {
    this._onCopilot = handler;
    return this;
  }

  onCopilotUnavailable(handler: () => void): this {
    this._onCopilotUnavailable = handler;
    return this;
  }

  onClose(handler: (event: CloseEvent) => void): this {
    this._onClose = handler;
    return this;
  }

  private settleReady(): void {
    if (this.readySettled) return;
    this.readySettled = true;
    this.resolveReady();
  }

  /** Create a meeting (unless `meetingId` is set) and wait until the socket is `ready`. */
  static async start(opts: StartOptions): Promise<CorellaLive> {
    const meetingId = opts.meetingId ?? (await createMeeting(opts)).id;
    const session = new CorellaLive({
      apiBase: opts.apiBase,
      apiKey: opts.apiKey,
      meetingId,
    });
    await session.ready;
    return session;
  }

  /** Attach to an already-created meeting; wait until `ready`. */
  static async connect(opts: ConnectOptions): Promise<CorellaLive> {
    const session = new CorellaLive(opts);
    await session.ready;
    return session;
  }

  get transcript(): TranscriptLine[] {
    return snapshotTranscript(this.store);
  }

  get partials(): { me: string; them: string } {
    return { ...this.store.partials };
  }

  get copilot(): CopilotEvent | null {
    return this.store.copilot;
  }

  get copilotAvailable(): boolean {
    return this.store.copilotAvailable;
  }

  sendPcm(channel: LiveChannel, pcm: Int16Array): void {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(framePcm(channel, pcm));
  }

  /** Browser: capture the mic onto channel `me`. */
  async startMic(): Promise<void> {
    this.stopMic();
    this.mic = await startMic((pcm) => this.sendPcm("me", pcm));
  }

  stopMic(): void {
    this.mic?.stop();
    this.mic = null;
  }

  /** Graceful stop — server finalizes (audio, report, post-call hook). */
  stop(): void {
    this.stopMic();
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "stop" }));
    }
  }

  /** Drop the socket. The meeting still finalizes, same as `stop`. */
  close(): void {
    this.stopMic();
    this.ws.close();
  }
}
