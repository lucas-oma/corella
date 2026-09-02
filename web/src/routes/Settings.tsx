import { useEffect, useRef, useState } from "react";

import AppShell from "@/components/AppShell";
import { ApiError, api, type AiOverview, type Preferences, type ProviderStatus, type SttStatus } from "@/lib/api";
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
  const [sttStatus, setSttStatus] = useState<SttStatus | null>(null);
  const [aiOverview, setAiOverview] = useState<AiOverview | null>(null);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which "AI models in use" row is currently expanded into an edit form —
  // only one at a time, matching the rest of Settings' inline-edit pattern.
  const [editingRow, setEditingRow] = useState<"stt" | "llm" | null>(null);
  const [draftLlmProvider, setDraftLlmProvider] = useState<Exclude<Preferences["llm_provider"], null> | "">("");
  const [draftSttProvider, setDraftSttProvider] = useState<Exclude<Preferences["stt_provider"], null> | "">("");
  // Set right after a successful preference save, cleared a couple seconds
  // later — the actual "yes, that worked" feedback the buttons were
  // missing (busy -> idle alone looked identical whether it succeeded).
  const [savedFlash, setSavedFlash] = useState<"stt" | "llm" | null>(null);
  const savedFlashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    api.getSttStatus().then(setSttStatus);
    api.getAiOverview().then(setAiOverview);
    api.getPreferences().then(setPreferences);
  }, []);

  // Keeps the edit-form drafts in sync whenever the committed preferences
  // change (initial load, or right after a save) — separate from the
  // edit-form-only draft state below, which only moves on an explicit Edit.
  useEffect(() => {
    if (!preferences) return;
    setInputs((prev) => ({
      ...prev,
      llmModel: preferences.llm_model ?? "",
      sttModel: preferences.stt_model ?? "",
      sttLanguage: preferences.stt_language ?? "",
    }));
  }, [preferences]);

  useEffect(() => {
    return () => {
      if (savedFlashTimer.current) clearTimeout(savedFlashTimer.current);
    };
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

  async function onSaveStt() {
    const value = inputs.stt?.trim();
    if (!value) return;
    setError(null);
    setBusy("stt");
    try {
      setSttStatus(await api.saveSttCredential(value));
      setInputs((prev) => ({ ...prev, stt: "" }));
      api.getAiOverview().then(setAiOverview);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save");
    } finally {
      setBusy(null);
    }
  }

  async function onRemoveStt() {
    setError(null);
    setBusy("stt");
    try {
      setSttStatus(await api.removeSttCredential());
      api.getAiOverview().then(setAiOverview);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't remove");
    } finally {
      setBusy(null);
    }
  }

  function flashSaved(row: "stt" | "llm") {
    setSavedFlash(row);
    if (savedFlashTimer.current) clearTimeout(savedFlashTimer.current);
    savedFlashTimer.current = setTimeout(() => setSavedFlash(null), 2000);
  }

  async function savePrefs(payload: Partial<Preferences>, row: "stt" | "llm") {
    setError(null);
    // "-pref" suffix keeps this distinct from the credential save/remove
    // buttons above, which already use the bare "stt" busy key — sharing
    // one would make those and this row's Save button spuriously disable
    // together.
    setBusy(`${row}-pref`);
    try {
      setPreferences(await api.savePreferences(payload));
      api.getAiOverview().then(setAiOverview);
      setEditingRow(null);
      flashSaved(row);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save preference");
    } finally {
      setBusy(null);
    }
  }

  function onEditRow(row: "stt" | "llm") {
    setError(null);
    setSavedFlash(null);
    if (row === "llm") setDraftLlmProvider(preferences?.llm_provider ?? "");
    else setDraftSttProvider(preferences?.stt_provider ?? "");
    setEditingRow(row);
  }

  function onSaveLlmPrefs() {
    savePrefs(
      {
        llm_provider: (draftLlmProvider || null) as Preferences["llm_provider"],
        llm_model: draftLlmProvider ? inputs.llmModel?.trim() || null : null,
      },
      "llm",
    );
  }

  function onSaveSttPrefs() {
    const usingDeepgram =
      draftSttProvider === "deepgram" || (!draftSttProvider && sttStatus?.connected);
    savePrefs(
      {
        stt_provider: (draftSttProvider || null) as Preferences["stt_provider"],
        stt_model: usingDeepgram ? inputs.sttModel?.trim() || null : null,
        stt_language: usingDeepgram ? inputs.sttLanguage?.trim() || null : null,
      },
      "stt",
    );
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

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Speech-to-text</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Local faster-whisper is always the default, zero-config — connecting Deepgram here
          switches transcription (both live and uploaded recordings) to it whenever it's reachable,
          falling back to local automatically if it isn't.
        </p>

        {sttStatus === null ? (
          <p className="mt-5 text-sm text-ink-muted">Loading…</p>
        ) : (
          <div className="mt-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">Deepgram</p>
                <p className="text-xs text-ink-subtle">Cloud speech-to-text via your own API key</p>
              </div>
              <span
                className={`rounded-sm border px-2 py-0.5 text-xs ${
                  sttStatus.connected
                    ? "border-status-success/30 text-status-success"
                    : "border-border text-ink-subtle dark:border-border-dark"
                }`}
              >
                {!sttStatus.connected
                  ? "Not connected"
                  : sttStatus.source === "env"
                    ? "Connected via .env"
                    : "Connected"}
              </span>
            </div>

            {sttStatus.source === "user" ? (
              <div className="mt-2">
                <button
                  onClick={onRemoveStt}
                  disabled={busy === "stt"}
                  className="text-xs text-ink-subtle hover:text-status-danger"
                >
                  {busy === "stt" ? "Removing…" : "Remove your key"}
                </button>
              </div>
            ) : (
              <div className="mt-2 flex gap-2">
                <input
                  type="password"
                  placeholder="API key"
                  value={inputs.stt ?? ""}
                  onChange={(e) => setInputs((prev) => ({ ...prev, stt: e.target.value }))}
                  className="field flex-1 text-sm"
                />
                <button
                  onClick={onSaveStt}
                  disabled={busy === "stt" || !inputs.stt?.trim()}
                  className="btn-secondary shrink-0"
                >
                  {busy === "stt" ? "Saving…" : "Save"}
                </button>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">AI models in use</h2>
        <p className="mt-1 text-sm text-ink-muted">
          What's actually powering each part of the app for your account right now.
        </p>
        {aiOverview === null ? (
          <p className="mt-5 text-sm text-ink-muted">Loading…</p>
        ) : (
          <ul className="mt-5 space-y-3">
            <li>
              <div className="flex items-center justify-between">
                <p className="text-sm text-ink dark:text-ink-inverted">Speech-to-text</p>
                {editingRow === "stt" ? (
                  <button onClick={() => setEditingRow(null)} className="text-xs text-ink-subtle">
                    Cancel
                  </button>
                ) : (
                  <div className="flex items-center gap-2">
                    {savedFlash === "stt" && <span className="text-xs text-status-success">Saved ✓</span>}
                    <p className="text-right text-xs text-ink-subtle">
                      {aiOverview.speech_to_text.active === "deepgram" ? "Deepgram" : "Local (faster-whisper)"}
                      {" · "}
                      {aiOverview.speech_to_text.model}
                    </p>
                    <button onClick={() => onEditRow("stt")} className="text-xs text-accent hover:underline">
                      Edit
                    </button>
                  </div>
                )}
              </div>
              {editingRow === "stt" && (
                <div className="mt-2 space-y-2 border-l-2 border-border pl-3 dark:border-border-dark">
                  <select
                    value={draftSttProvider}
                    onChange={(e) =>
                      setDraftSttProvider(e.target.value as Exclude<Preferences["stt_provider"], null> | "")
                    }
                    className="field w-full text-sm"
                  >
                    <option value="">Auto (recommended — Deepgram if connected, else local)</option>
                    <option value="deepgram" disabled={!sttStatus?.connected}>
                      Deepgram{!sttStatus?.connected ? " (not connected)" : ""}
                    </option>
                    <option value="whisper">Local (faster-whisper)</option>
                  </select>
                  {(draftSttProvider === "deepgram" || (!draftSttProvider && sttStatus?.connected)) && (
                    <>
                      <input
                        type="text"
                        placeholder={aiOverview.speech_to_text.model || "nova-3"}
                        value={inputs.sttModel ?? ""}
                        onChange={(e) => setInputs((prev) => ({ ...prev, sttModel: e.target.value }))}
                        className="field w-full text-sm"
                      />
                      <input
                        type="text"
                        placeholder="multi (auto-detect — recommended)"
                        value={inputs.sttLanguage ?? ""}
                        onChange={(e) => setInputs((prev) => ({ ...prev, sttLanguage: e.target.value }))}
                        className="field w-full text-sm"
                      />
                    </>
                  )}
                  <button onClick={onSaveSttPrefs} disabled={busy === "stt-pref"} className="btn-secondary">
                    {busy === "stt-pref" ? "Saving…" : "Save"}
                  </button>
                </div>
              )}
            </li>

            <li>
              <div className="flex items-center justify-between">
                <p className="text-sm text-ink dark:text-ink-inverted">Copilot / reports</p>
                {editingRow === "llm" ? (
                  <button onClick={() => setEditingRow(null)} className="text-xs text-ink-subtle">
                    Cancel
                  </button>
                ) : (
                  <div className="flex items-center gap-2">
                    {savedFlash === "llm" && <span className="text-xs text-status-success">Saved ✓</span>}
                    <p className="text-right text-xs text-ink-subtle">
                      {aiOverview.language_model.active
                        ? `${aiOverview.language_model.active} · ${aiOverview.language_model.model}`
                        : "Not connected"}
                    </p>
                    <button onClick={() => onEditRow("llm")} className="text-xs text-accent hover:underline">
                      Edit
                    </button>
                  </div>
                )}
              </div>
              {editingRow === "llm" && (
                <div className="mt-2 space-y-2 border-l-2 border-border pl-3 dark:border-border-dark">
                  <select
                    value={draftLlmProvider}
                    onChange={(e) =>
                      setDraftLlmProvider(e.target.value as Exclude<Preferences["llm_provider"], null> | "")
                    }
                    disabled={providers === null}
                    className="field w-full text-sm"
                  >
                    <option value="">Auto (recommended — first connected provider)</option>
                    {providers
                      ?.filter((p) => p.connected)
                      .map((p) => (
                        <option key={p.provider} value={p.provider}>
                          {PROVIDER_META[p.provider].name}
                        </option>
                      ))}
                  </select>
                  {draftLlmProvider && (
                    <input
                      type="text"
                      placeholder={aiOverview.language_model.model ?? "Model name"}
                      value={inputs.llmModel ?? ""}
                      onChange={(e) => setInputs((prev) => ({ ...prev, llmModel: e.target.value }))}
                      className="field w-full text-sm"
                    />
                  )}
                  <button onClick={onSaveLlmPrefs} disabled={busy === "llm-pref"} className="btn-secondary">
                    {busy === "llm-pref" ? "Saving…" : "Save"}
                  </button>
                </div>
              )}
            </li>

            <li className="flex items-center justify-between">
              <p className="text-sm text-ink dark:text-ink-inverted">Knowledge base / meeting search</p>
              <p className="text-right text-xs text-ink-subtle">{aiOverview.embeddings.model}</p>
            </li>
            <li className="flex items-center justify-between">
              <p className="text-sm text-ink dark:text-ink-inverted">Speaker diarization</p>
              <p className="text-right text-xs text-ink-subtle">
                {aiOverview.diarization.available ? aiOverview.diarization.pipeline : "Not configured (needs HF_TOKEN)"}
              </p>
            </li>
          </ul>
        )}
      </section>
    </AppShell>
  );
}
