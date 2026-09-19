export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "corella.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Turn FastAPI's `detail` (string, validation array, or object) into a
 * single human-readable message — validation 422s otherwise stringify to
 * "[object Object]" when shown in the UI. */
function formatApiDetail(detail: unknown): string {
  if (detail == null || detail === "") return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const row = item as { msg?: unknown; loc?: unknown[] };
        if (typeof row.msg === "string" && row.msg) return row.msg;
      }
      return "";
    }).filter(Boolean);
    return parts.join("; ");
  }
  if (typeof detail === "object") {
    const row = detail as { msg?: unknown; detail?: unknown; message?: unknown };
    if (typeof row.msg === "string" && row.msg) return row.msg;
    if (typeof row.detail === "string" && row.detail) return row.detail;
    if (typeof row.message === "string" && row.message) return row.message;
  }
  return "";
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  // Let the browser set Content-Type (with boundary) for multipart uploads —
  // overriding it manually breaks the boundary and the server can't parse it.
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(
      res.status,
      formatApiDetail((body as { detail?: unknown }).detail) || res.statusText || "Request failed",
    );
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Fetches a protected file (e.g. meeting audio) as a blob URL, since
 * <audio>/<img> src can't carry an Authorization header. Caller is
 * responsible for revoking the URL (URL.revokeObjectURL) when done with it.
 */
async function requestObjectUrl(path: string): Promise<string> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, { headers });
  if (!res.ok) throw new ApiError(res.status, res.statusText);

  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export interface Token {
  access_token: string;
  token_type: string;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "member";
  group_id: string | null;
  voice_enrolled: boolean;
}

export interface AuthConfig {
  allow_public_registration: boolean;
}

/** The lightweight, public shape — every authenticated user needs this to
 * create a meeting, not just admins. See CallTypeConfig below for the
 * full admin-managed shape (name/guidance/pre-post-call config). */
export interface CallTypeOption {
  id: string;
  name: string;
  slug: string;
  is_default: boolean;
}

export interface CallTypeConfig {
  id: string;
  name: string;
  slug: string;
  report_guidance: string | null;
  is_default: boolean;

  pre_call_enabled: boolean;
  pre_call_url: string | null;
  pre_call_method: string;
  pre_call_headers: string | null;
  pre_call_body_template: string | null;
  pre_call_use_as_context: boolean;
  pre_call_async: boolean;

  post_call_enabled: boolean;
  post_call_url: string | null;
  post_call_method: string;
  post_call_headers: string | null;
  post_call_body_template: string | null;
  post_call_send_full_payload: boolean;
  post_call_async: boolean;
}

export interface HookLog {
  id: string;
  phase: "pre" | "post" | string;
  outcome: "success" | "error" | string;
  method: string;
  url: string;
  request_headers: string | null;
  request_body: string | null;
  response_status: number | null;
  response_body: string | null;
  error: string | null;
  duration_ms: number | null;
  ran_async: boolean;
  created_at: string;
}

export interface AppSecret {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface Meeting {
  id: string;
  title: string;
  status: "recording" | "processing" | "ready" | "failed";
  call_type: CallTypeOption | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  has_audio: boolean;
  processing_error: string | null;
  summary: string | null;
  key_topics: string[] | null;
  sentiment: string | null;
  notable_quotes: string[] | null;
  coach_score: number | null;
  estimated_cost_usd: number | null;
  created_at: string;
  owner_id: string;
  owner_name: string;
  // The integration's own admin-chosen label (Settings -> API keys) if
  // this meeting is/was actually streamed via an API key rather than the
  // browser — null otherwise. Drives the "Live via API"/"Recorded via
  // API" badge.
  api_key_name: string | null;
}

/** A group-mate's meeting, from the Dashboard's group-browsing tab — a
 * narrower shape than Meeting (report-only visibility: no summary/audio/
 * error yet, that's fetched — and re-checked server-side — on open). */
export interface GroupMeeting {
  id: string;
  title: string;
  status: Meeting["status"];
  summary: string | null;
  key_topics: string[] | null;
  created_at: string;
  owner_id: string;
  owner_name: string;
  api_key_name: string | null;
}

export interface ActionItem {
  id: string;
  text: string;
  status: "open" | "done";
}

export interface Report {
  title: string;
  summary: string;
  key_topics: string[];
  sentiment: string | null;
  notable_quotes: string[];
  coach_score: number | null;
  estimated_cost_usd: number | null;
  action_items: ActionItem[];
  talk_ratio: { me: number; them: number } | null;
}

export interface TranscriptSegment {
  id: string;
  speaker_label: string | null;
  // Set only when speaker_label resolves to an enrolled account — render
  // "Me" only when this equals the viewer's own id, the real name otherwise.
  linked_user_id: string | null;
  channel: "me" | "them" | "unknown";
  start_ms: number;
  end_ms: number;
  text: string;
}

/** One persisted live-copilot cycle (app/services/copilot/live.py:run_cycle)
 * — timestamp-anchored to the transcript, shown next to it on MeetingDetail. */
export interface CopilotInsight {
  id: string;
  at_ms: number;
  suggestion: string | null;
  blockers: string[];
  coach_score: number | null;
}

export interface ProviderStatus {
  provider: "anthropic" | "openai" | "gemini" | "ollama";
  connected: boolean;
  source: "user" | "env" | null;
}

export interface SttStatus {
  connected: boolean;
  source: "user" | "env" | null;
}

/** A self-service credential for external/machine access (Settings) — the
 * real key is only ever present on the response to createApiKey, never
 * again after that. `max_duration_minutes` caps a live WebSocket session
 * authenticated with this key (server-enforced; browser recordings are
 * uncapped). */
export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  max_duration_minutes: number;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface AiOverview {
  speech_to_text: {
    active: "deepgram" | "whisper";
    model: string;
    source: "user" | "env" | "local";
    language: string;
  };
  language_model: {
    active: ProviderStatus["provider"] | null;
    model: string | null;
    source: "user" | "env" | null;
  };
  embeddings: { model: string };
  diarization: { pipeline: string; speaker_embedding: string; available: boolean };
}

export interface Preferences {
  llm_provider: ProviderStatus["provider"] | null;
  llm_model: string | null;
  stt_provider: "deepgram" | "whisper" | null;
  stt_model: string | null;
  stt_language: string | null;
}

export interface MeetingSearchResult {
  meeting_id: string;
  title: string;
  status: Meeting["status"];
  created_at: string;
  snippet: string;
  start_ms: number;
  owner_id: string;
  owner_name: string;
}

export interface Group {
  id: string;
  name: string;
  created_at: string;
  member_count: number;
}

export interface AdminUserCreate {
  email: string;
  password: string;
  full_name: string;
  role: User["role"];
  group_id: string | null;
}

export interface AdminUserUpdate {
  role?: User["role"];
  group_id?: string | null;
  clear_group?: boolean;
}

export interface UserCostBreakdown {
  owner_id: string | null;
  owner_name: string;
  total_usd: number;
  call_count: number;
}

export interface ProviderCostBreakdown {
  provider: string;
  total_usd: number;
  call_count: number;
}

export interface DailyCost {
  day: string;
  total_usd: number;
}

export type CostPeriod = "7d" | "30d" | "month" | "year";

export interface CostSummary {
  total_usd: number;
  priced_call_count: number;
  total_call_count: number;
  avg_cost_per_call: number | null;
  total_input_tokens: number;
  total_output_tokens: number;
  by_user: UserCostBreakdown[];
  by_provider: ProviderCostBreakdown[];
  daily: DailyCost[];
  projected_next_7_days_usd: number | null;
  period: CostPeriod;
}

export interface KBDocument {
  id: string;
  filename: string;
  content_type: string;
  status: "pending" | "processing" | "ready" | "failed";
  chunk_count: number | null;
  error: string | null;
  created_at: string;
  owner_id: string;
  owner_name: string;
  group_id: string | null;
  group_name: string | null;
  keywords: string[] | null;
}

export const api = {
  authConfig: () => request<AuthConfig>("/api/auth/config"),
  register: (email: string, password: string, fullName: string) =>
    request<Token>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, full_name: fullName }),
    }),
  login: (email: string, password: string) =>
    request<Token>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<User>("/api/auth/me"),
  updateProfile: (fullName: string) =>
    request<User>("/api/auth/me", { method: "PATCH", body: JSON.stringify({ full_name: fullName }) }),
  enrollVoice: (wavBlob: Blob) => {
    const form = new FormData();
    form.append("file", wavBlob, "voice-sample.wav");
    return request<User>("/api/auth/me/voice", { method: "POST", body: form });
  },
  removeVoiceEnrollment: () => request<User>("/api/auth/me/voice", { method: "DELETE" }),
  listMeetings: () => request<Meeting[]>("/api/meetings"),
  listGroupMeetings: () => request<GroupMeeting[]>("/api/meetings/group"),
  listAllMeetings: () => request<GroupMeeting[]>("/api/meetings/all"),
  searchMeetings: (query: string) =>
    request<MeetingSearchResult[]>(`/api/meetings/search?q=${encodeURIComponent(query)}`),
  searchAllMeetings: (query: string) =>
    request<MeetingSearchResult[]>(`/api/meetings/search/all?q=${encodeURIComponent(query)}`),
  createMeeting: (title: string, callTypeId: string | null = null) =>
    request<Meeting>("/api/meetings", {
      method: "POST",
      body: JSON.stringify({ title, call_type_id: callTypeId }),
    }),
  getMeeting: (id: string) => request<Meeting>(`/api/meetings/${id}`),
  uploadMeetingAudio: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Meeting>(`/api/meetings/${id}/audio`, { method: "POST", body: form });
  },
  getTranscript: (id: string) => request<TranscriptSegment[]>(`/api/meetings/${id}/transcript`),
  getCopilotInsights: (id: string) => request<CopilotInsight[]>(`/api/meetings/${id}/insights`),
  getAudioObjectUrl: (id: string) => requestObjectUrl(`/api/meetings/${id}/audio`),
  deleteMeeting: (id: string) => request<void>(`/api/meetings/${id}`, { method: "DELETE" }),
  getProviderStatus: () => request<ProviderStatus[]>("/api/settings/providers"),
  saveProviderCredential: (provider: ProviderStatus["provider"], value: { api_key?: string; base_url?: string }) =>
    request<ProviderStatus>(`/api/settings/providers/${provider}`, {
      method: "PUT",
      body: JSON.stringify(value),
    }),
  removeProviderCredential: (provider: ProviderStatus["provider"]) =>
    request<ProviderStatus>(`/api/settings/providers/${provider}`, { method: "DELETE" }),
  getSttStatus: () => request<SttStatus>("/api/settings/stt"),
  saveSttCredential: (apiKey: string) =>
    request<SttStatus>("/api/settings/stt", { method: "PUT", body: JSON.stringify({ api_key: apiKey }) }),
  removeSttCredential: () => request<SttStatus>("/api/settings/stt", { method: "DELETE" }),
  listApiKeys: () => request<ApiKey[]>("/api/settings/api-keys"),
  createApiKey: (name: string, maxDurationMinutes: number) =>
    request<ApiKeyCreated>("/api/settings/api-keys", {
      method: "POST",
      body: JSON.stringify({ name, max_duration_minutes: maxDurationMinutes }),
    }),
  updateApiKey: (id: string, payload: { max_duration_minutes: number }) =>
    request<ApiKey>(`/api/settings/api-keys/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteApiKey: (id: string) => request<void>(`/api/settings/api-keys/${id}`, { method: "DELETE" }),
  getAiOverview: () => request<AiOverview>("/api/settings/ai-overview"),
  getPreferences: () => request<Preferences>("/api/settings/preferences"),
  savePreferences: (payload: Partial<Preferences>) =>
    request<Preferences>("/api/settings/preferences", { method: "PUT", body: JSON.stringify(payload) }),
  listKBDocuments: () => request<KBDocument[]>("/api/kb/documents"),
  uploadKBDocument: (file: File, groupId: string | null = null) => {
    const form = new FormData();
    form.append("file", file);
    if (groupId) form.append("group_id", groupId);
    return request<KBDocument>("/api/kb/documents", { method: "POST", body: form });
  },
  deleteKBDocument: (id: string) => request<void>(`/api/kb/documents/${id}`, { method: "DELETE" }),
  generateReport: (id: string) => request<Report>(`/api/meetings/${id}/report`, { method: "POST" }),
  getMeetingHookLogs: (id: string) => request<HookLog[]>(`/api/meetings/${id}/hook-logs`),
  listActionItems: (id: string) => request<ActionItem[]>(`/api/meetings/${id}/action-items`),
  updateActionItem: (meetingId: string, itemId: string, status: ActionItem["status"]) =>
    request<ActionItem>(`/api/meetings/${meetingId}/action-items/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  adminListUsers: () => request<User[]>("/api/admin/users"),
  adminCreateUser: (payload: AdminUserCreate) =>
    request<User>("/api/admin/users", { method: "POST", body: JSON.stringify(payload) }),
  adminUpdateUser: (id: string, payload: AdminUserUpdate) =>
    request<User>(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  adminListGroups: () => request<Group[]>("/api/admin/groups"),
  adminCreateGroup: (name: string) =>
    request<Group>("/api/admin/groups", { method: "POST", body: JSON.stringify({ name }) }),
  adminDeleteGroup: (id: string) => request<void>(`/api/admin/groups/${id}`, { method: "DELETE" }),
  adminGetCostSummary: (period: CostPeriod = "30d") =>
    request<CostSummary>(`/api/admin/costs?period=${period}`),
  getCallTypes: () => request<CallTypeOption[]>("/api/call-types"),
  adminListCallTypes: () => request<CallTypeConfig[]>("/api/admin/call-types"),
  adminCreateCallType: (payload: Partial<CallTypeConfig> & { name: string; slug: string }) =>
    request<CallTypeConfig>("/api/admin/call-types", { method: "POST", body: JSON.stringify(payload) }),
  adminUpdateCallType: (id: string, payload: Partial<CallTypeConfig>) =>
    request<CallTypeConfig>(`/api/admin/call-types/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  adminDeleteCallType: (id: string) => request<void>(`/api/admin/call-types/${id}`, { method: "DELETE" }),
  adminListSecrets: () => request<AppSecret[]>("/api/admin/secrets"),
  adminCreateSecret: (payload: { name: string; value: string }) =>
    request<AppSecret>("/api/admin/secrets", { method: "POST", body: JSON.stringify(payload) }),
  adminUpdateSecret: (id: string, payload: { name?: string; value?: string }) =>
    request<AppSecret>(`/api/admin/secrets/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  adminDeleteSecret: (id: string) => request<void>(`/api/admin/secrets/${id}`, { method: "DELETE" }),
};
