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

export type OrgRole = "owner" | "admin" | "member";

export interface OrgMembership {
  id: string;
  name: string;
  role: OrgRole;
  is_instance_org: boolean;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_super_admin: boolean;
  active_organization_id: string | null;
  organizations: OrgMembership[];
  group_ids: string[];
  voice_enrolled: boolean;
}

export interface AuthConfig {
  allow_public_registration: boolean;
  max_orgs_per_user: number;
  email_invites: boolean;
}

export interface Organization {
  id: string;
  name: string;
  is_instance_org: boolean;
  role: OrgRole;
  created_at: string;
}

export interface OrgMember {
  id: string;
  email: string;
  full_name: string;
  role: OrgRole;
  group_ids: string[];
  is_super_admin: boolean;
}

export interface OrgInvite {
  id: string;
  email: string;
  role: OrgRole;
  expires_at: string;
  created_at: string;
  token?: string | null;
  email_sent?: boolean | null;
}

export interface InvitePreview {
  organization_name: string;
  email: string;
  role: OrgRole;
  expires_at: string;
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
  // browser — null otherwise. Drives the "API: …" badge.
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

export interface MemberCreate {
  email: string;
  password: string;
  full_name: string;
  role: OrgRole;
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
  listOrgMeetings: () => request<GroupMeeting[]>("/api/meetings/org"),
  listAllMeetings: () => request<GroupMeeting[]>("/api/meetings/all"),
  searchMeetings: (query: string) =>
    request<MeetingSearchResult[]>(`/api/meetings/search?q=${encodeURIComponent(query)}`),
  searchOrgMeetings: (query: string) =>
    request<MeetingSearchResult[]>(`/api/meetings/search/org?q=${encodeURIComponent(query)}`),
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
  adminGetCostSummary: (period: CostPeriod = "30d") =>
    request<CostSummary>(`/api/admin/costs?period=${period}`),
  adminGetInstanceCostSummary: (period: CostPeriod = "30d") =>
    request<CostSummary>(`/api/admin/costs/instance?period=${period}`),
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
  listOrganizations: () => request<Organization[]>("/api/organizations"),
  createOrganization: (name: string) =>
    request<Organization>("/api/organizations", { method: "POST", body: JSON.stringify({ name }) }),
  switchOrganization: (organizationId: string) =>
    request<Organization>("/api/organizations/current", {
      method: "PUT",
      body: JSON.stringify({ organization_id: organizationId }),
    }),
  renameOrganization: (orgId: string, name: string) =>
    request<Organization>(`/api/organizations/${orgId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteOrganization: (orgId: string) =>
    request<void>(`/api/organizations/${orgId}`, { method: "DELETE" }),
  listOrgMembers: (orgId: string) => request<OrgMember[]>(`/api/organizations/${orgId}/members`),
  createOrgMember: (orgId: string, payload: MemberCreate) =>
    request<OrgMember>(`/api/organizations/${orgId}/members`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateOrgMember: (orgId: string, userId: string, role: OrgRole) =>
    request<OrgMember>(`/api/organizations/${orgId}/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
  removeOrgMember: (orgId: string, userId: string) =>
    request<void>(`/api/organizations/${orgId}/members/${userId}`, { method: "DELETE" }),
  transferOwnership: (orgId: string, userId: string) =>
    request<OrgMember>(`/api/organizations/${orgId}/transfer`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),
  leaveOrganization: (orgId: string) =>
    request<void>(`/api/organizations/${orgId}/leave`, { method: "POST" }),
  listOrgInvites: (orgId: string) => request<OrgInvite[]>(`/api/organizations/${orgId}/invites`),
  createOrgInvite: (orgId: string, payload: { email: string; role: OrgRole }) =>
    request<OrgInvite>(`/api/organizations/${orgId}/invites`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  resendOrgInvite: (orgId: string, inviteId: string) =>
    request<OrgInvite>(`/api/organizations/${orgId}/invites/${inviteId}/resend`, { method: "POST" }),
  revokeOrgInvite: (orgId: string, inviteId: string) =>
    request<void>(`/api/organizations/${orgId}/invites/${inviteId}`, { method: "DELETE" }),
  previewInvite: (token: string) => request<InvitePreview>(`/api/invites/${token}`),
  acceptInvite: (token: string, payload: { password?: string; full_name?: string } = {}) =>
    request<Token>(`/api/invites/${token}/accept`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listOrgGroups: (orgId: string) => request<Group[]>(`/api/organizations/${orgId}/groups`),
  createOrgGroup: (orgId: string, name: string) =>
    request<Group>(`/api/organizations/${orgId}/groups`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  deleteOrgGroup: (orgId: string, groupId: string) =>
    request<void>(`/api/organizations/${orgId}/groups/${groupId}`, { method: "DELETE" }),
  setOrgGroupMembers: (orgId: string, groupId: string, userIds: string[]) =>
    request<void>(`/api/organizations/${orgId}/groups/${groupId}/members`, {
      method: "PUT",
      body: JSON.stringify({ user_ids: userIds }),
    }),
  adminListOrganizations: () => request<Organization[]>("/api/admin/organizations"),
  adminListUsers: () =>
    request<{ id: string; email: string; full_name: string; is_super_admin: boolean }[]>(
      "/api/admin/users",
    ),
  adminSetSuperAdmin: (userId: string, isSuperAdmin: boolean) =>
    request<{ id: string; is_super_admin: boolean }>(`/api/admin/users/${userId}/super-admin`, {
      method: "PATCH",
      body: JSON.stringify({ is_super_admin: isSuperAdmin }),
    }),
};
