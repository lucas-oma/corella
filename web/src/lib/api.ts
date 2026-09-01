const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
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
}

export interface AuthConfig {
  allow_public_registration: boolean;
}

export interface Meeting {
  id: string;
  title: string;
  status: "recording" | "processing" | "ready" | "failed";
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  has_audio: boolean;
  processing_error: string | null;
  created_at: string;
}

export interface TranscriptSegment {
  id: string;
  speaker_label: string | null;
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

export interface KBDocument {
  id: string;
  filename: string;
  content_type: string;
  status: "pending" | "processing" | "ready" | "failed";
  chunk_count: number | null;
  error: string | null;
  created_at: string;
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
  listMeetings: () => request<Meeting[]>("/api/meetings"),
  createMeeting: (title: string) =>
    request<Meeting>("/api/meetings", { method: "POST", body: JSON.stringify({ title }) }),
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
  listKBDocuments: () => request<KBDocument[]>("/api/kb/documents"),
  uploadKBDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<KBDocument>("/api/kb/documents", { method: "POST", body: form });
  },
  deleteKBDocument: (id: string) => request<void>(`/api/kb/documents/${id}`, { method: "DELETE" }),
};
