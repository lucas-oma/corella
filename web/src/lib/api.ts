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
    throw new ApiError(res.status, body.detail ?? res.statusText);
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

export type CallType = "meeting" | "sales" | "support" | "interview" | "one_on_one";

export const CALL_TYPE_LABEL: Record<CallType, string> = {
  meeting: "Meeting",
  sales: "Sales call",
  support: "Support call",
  interview: "Interview",
  one_on_one: "1:1",
};

export interface Meeting {
  id: string;
  title: string;
  status: "recording" | "processing" | "ready" | "failed";
  call_type: CallType;
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

export interface ProviderStatus {
  provider: "anthropic" | "openai" | "gemini" | "ollama";
  connected: boolean;
  source: "user" | "env" | null;
}

export interface SttStatus {
  connected: boolean;
  source: "user" | "env" | null;
}

export interface AiOverview {
  speech_to_text: { active: "deepgram" | "whisper"; model: string; source: "user" | "env" | "local" };
  language_model: {
    active: ProviderStatus["provider"] | null;
    model: string | null;
    source: "user" | "env" | null;
  };
  embeddings: { model: string };
  diarization: { pipeline: string; speaker_embedding: string; available: boolean };
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

export interface DailyCost {
  day: string;
  total_usd: number;
}

export interface CostSummary {
  total_usd: number;
  priced_call_count: number;
  total_call_count: number;
  avg_cost_per_call: number | null;
  total_input_tokens: number;
  total_output_tokens: number;
  by_user: UserCostBreakdown[];
  daily: DailyCost[];
  projected_next_7_days_usd: number | null;
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
  createMeeting: (title: string, callType: CallType = "meeting") =>
    request<Meeting>("/api/meetings", {
      method: "POST",
      body: JSON.stringify({ title, call_type: callType }),
    }),
  getMeeting: (id: string) => request<Meeting>(`/api/meetings/${id}`),
  uploadMeetingAudio: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Meeting>(`/api/meetings/${id}/audio`, { method: "POST", body: form });
  },
  getTranscript: (id: string) => request<TranscriptSegment[]>(`/api/meetings/${id}/transcript`),
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
  getAiOverview: () => request<AiOverview>("/api/settings/ai-overview"),
  listKBDocuments: () => request<KBDocument[]>("/api/kb/documents"),
  uploadKBDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<KBDocument>("/api/kb/documents", { method: "POST", body: form });
  },
  deleteKBDocument: (id: string) => request<void>(`/api/kb/documents/${id}`, { method: "DELETE" }),
  generateReport: (id: string) => request<Report>(`/api/meetings/${id}/report`, { method: "POST" }),
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
  adminGetCostSummary: () => request<CostSummary>("/api/admin/costs"),
};
