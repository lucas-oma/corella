import { useEffect, useRef, useState } from "react";

import AppShell from "@/components/AppShell";
import { ApiError, api, type ProviderStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { type CaptureHandle, pcmToWavBlob, startCapture } from "@/lib/live";

// Verified empirically against real voice enrollment (see the plan's Phase
// O verification): a ~6s sample gave two real enrolled voices only a thin
// 0.635-vs-0.602 similarity margin — enough to misrecognize one as the
// other. A longer sample (the full ~22s real test clips) resolved it
// cleanly. 10s is a practical floor short of a full clip.
const MIN_VOICE_SAMPLE_SECONDS = 10;

const PROVIDER_META: Record<ProviderStatus["provider"], { name: string; hint: string }> = {
  anthropic: { name: "Anthropic", hint: "Claude models via your own API key" },
  openai: { name: "OpenAI", hint: "GPT models via your own API key" },
  gemini: { name: "Gemini", hint: "Google Gemini models via your own API key" },
  ollama: { name: "Ollama", hint: "A local model server you point Corella at" },
};

function statusLabel(status: ProviderStatus): string {
  if (!status.connected) return "Not connected";
  return status.source === "env" ? "Connected via .env" : "Connected";
}

export default function Settings() {
  const { user, refreshUser } = useAuth();
  const [providers, setProviders] = useState<ProviderStatus[] | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [fullName, setFullName] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [recording, setRecording] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const recordingRef = useRef<{
    stream: MediaStream;
    capture: CaptureHandle;
    chunks: Int16Array[];
  } | null>(null);

  useEffect(() => {
    api.getProviderStatus().then(setProviders);
  }, []);

  useEffect(() => {
    if (user) setFullName(user.full_name);
  }, [user]);

  async function onSaveName() {
    const trimmed = fullName.trim();
    if (!trimmed) return;
    setError(null);
    setSavingName(true);
    try {
      await api.updateProfile(trimmed);
      await refreshUser();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update name");
    } finally {
      setSavingName(false);
    }
  }

  async function onStartRecording() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Int16Array[] = [];
      const capture = await startCapture(stream, (pcm) => chunks.push(pcm));
      recordingRef.current = { stream, capture, chunks };
      setRecording(true);
    } catch {
      setError("Couldn't access your microphone — check your browser's permission settings.");
    }
  }

  async function onStopRecording() {
    const rec = recordingRef.current;
    if (!rec) return;
    rec.capture.stop();
    rec.stream.getTracks().forEach((t) => t.stop());
    recordingRef.current = null;
    setRecording(false);

    const totalSamples = rec.chunks.reduce((sum, c) => sum + c.length, 0);
    if (totalSamples < 16000 * MIN_VOICE_SAMPLE_SECONDS) {
      setError(`Recording too short — say a sentence or two (at least ${MIN_VOICE_SAMPLE_SECONDS}s).`);
      return;
    }
    const merged = new Int16Array(totalSamples);
    let offset = 0;
    for (const chunk of rec.chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }

    setError(null);
    setEnrolling(true);
    try {
      await api.enrollVoice(pcmToWavBlob(merged));
      // Extraction is torch-dependent and runs in the worker (same split as
      // every other diarization path) — poll rather than assume it's done
      // by the time the upload request returns.
      for (let i = 0; i < 20; i++) {
        const fresh = await api.me();
        if (fresh.voice_enrolled) break;
        await new Promise((r) => setTimeout(r, 3000));
      }
      await refreshUser();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't enroll your voice");
    } finally {
      setEnrolling(false);
    }
  }

  async function onRemoveVoice() {
    setError(null);
    setEnrolling(true);
    try {
      await api.removeVoiceEnrollment();
      await refreshUser();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't remove your voice sample");
    } finally {
      setEnrolling(false);
    }
  }

  function updateStatus(next: ProviderStatus) {
    setProviders((prev) => prev?.map((p) => (p.provider === next.provider ? next : p)) ?? null);
  }

  async function onSave(provider: ProviderStatus["provider"]) {
    const value = inputs[provider]?.trim();
    if (!value) return;

    setError(null);
    setBusy(provider);
    try {
      const payload = provider === "ollama" ? { base_url: value } : { api_key: value };
      const next = await api.saveProviderCredential(provider, payload);
      updateStatus(next);
      setInputs((prev) => ({ ...prev, [provider]: "" }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save");
    } finally {
      setBusy(null);
    }
  }

  async function onRemove(provider: ProviderStatus["provider"]) {
    setError(null);
    setBusy(provider);
    try {
      const next = await api.removeProviderCredential(provider);
      updateStatus(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't remove");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell>
      <div className="mb-8">
        <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">Settings</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Copilot providers and knowledge base.
        </p>
      </div>

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

      <section className="card p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Profile</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Your name and voice — recording a sample lets Corella recognize you across meetings,
          showing your segments as "Me" to you and your real name to an admin viewing the same
          recording.
        </p>

        <div className="mt-5">
          <p className="label mb-1">Name</p>
          <div className="flex gap-2">
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="field flex-1 text-sm"
            />
            <button
              onClick={onSaveName}
              disabled={savingName || !fullName.trim() || fullName.trim() === user?.full_name}
              className="btn-secondary shrink-0"
            >
              {savingName ? "Saving…" : "Save"}
            </button>
          </div>
        </div>

        <div className="mt-5 border-t border-border pt-4 dark:border-border-dark">
          <div className="flex items-center justify-between">
            <p className="label">Voice sample</p>
            <span
              className={`rounded-sm border px-2 py-0.5 text-xs ${
                user?.voice_enrolled
                  ? "border-status-success/30 text-status-success"
                  : "border-border text-ink-subtle dark:border-border-dark"
              }`}
            >
              {user?.voice_enrolled ? "Enrolled" : "Not enrolled"}
            </span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            {!recording ? (
              <button onClick={onStartRecording} disabled={enrolling} className="btn-secondary">
                {enrolling
                  ? "Processing…"
                  : user?.voice_enrolled
                    ? "Re-record"
                    : "Record a sample"}
              </button>
            ) : (
              <button onClick={onStopRecording} className="btn-primary">
                Stop — {MIN_VOICE_SAMPLE_SECONDS}s+ recorded so far
              </button>
            )}
            {user?.voice_enrolled && !recording && (
              <button
                onClick={onRemoveVoice}
                disabled={enrolling}
                className="text-xs text-ink-subtle hover:text-status-danger"
              >
                Remove
              </button>
            )}
          </div>
        </div>
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Copilot providers</h2>
        <p className="mt-1 text-sm text-ink-muted">
          The LLM(s) that will power live suggestions and post-call reports once those land. Your
          key is stored encrypted and never shown again after saving.
        </p>
        <ul className="mt-5 divide-y divide-border dark:divide-border-dark">
          {providers === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
          {providers?.map((status) => {
            const meta = PROVIDER_META[status.provider];
            const isOllama = status.provider === "ollama";
            const isUserSet = status.source === "user";
            return (
              <li key={status.provider} className="py-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                      {meta.name}
                    </p>
                    <p className="text-xs text-ink-subtle">{meta.hint}</p>
                  </div>
                  <span
                    className={`rounded-sm border px-2 py-0.5 text-xs ${
                      status.connected
                        ? "border-status-success/30 text-status-success"
                        : "border-border text-ink-subtle dark:border-border-dark"
                    }`}
                  >
                    {statusLabel(status)}
                  </span>
                </div>

                {isUserSet ? (
                  <div className="mt-2">
                    <button
                      onClick={() => onRemove(status.provider)}
                      disabled={busy === status.provider}
                      className="text-xs text-ink-subtle hover:text-status-danger"
                    >
                      {busy === status.provider ? "Removing…" : "Remove your key"}
                    </button>
                  </div>
                ) : (
                  <div className="mt-2 flex gap-2">
                    <input
                      type={isOllama ? "text" : "password"}
                      placeholder={isOllama ? "http://localhost:11434" : "API key"}
                      value={inputs[status.provider] ?? ""}
                      onChange={(e) =>
                        setInputs((prev) => ({ ...prev, [status.provider]: e.target.value }))
                      }
                      className="field flex-1 text-sm"
                    />
                    <button
                      onClick={() => onSave(status.provider)}
                      disabled={busy === status.provider || !inputs[status.provider]?.trim()}
                      className="btn-secondary shrink-0"
                    >
                      {busy === status.provider ? "Saving…" : "Save"}
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </AppShell>
  );
}
