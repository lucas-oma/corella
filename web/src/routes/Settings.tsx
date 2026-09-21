import { useEffect, useRef, useState } from "react";

import AppShell from "@/components/AppShell";
import UserAvatar from "@/components/UserAvatar";
import {
  ApiError,
  api,
  type AiOverview,
  type ApiKey,
  type ApiKeyCreated,
  type Preferences,
  type ProviderStatus,
  type SttStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { type CaptureHandle, pcmToWavBlob, startCapture } from "@/lib/live";

// Verified empirically against real voice enrollment (see the plan's Phase
// O verification): a ~6s sample gave two real enrolled voices only a thin
// 0.635-vs-0.602 similarity margin — enough to misrecognize one as the
// other. A longer sample (the full ~22s real test clips) resolved it
// cleanly. 10s is a practical floor short of a full clip.
const MIN_VOICE_SAMPLE_SECONDS = 10;

// Per-API-key live-session cap (server/app/models/api_key.py). Same
// bounds the API itself enforces — a key with no explicit value gets 60.
const DEFAULT_KEY_DURATION_MINUTES = 60;
const MIN_KEY_DURATION_MINUTES = 1;
const MAX_KEY_DURATION_MINUTES = 480;

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

// Deepgram's documented pre-recorded-transcription language codes (Nova-3's
// list, the broader of the two models this app offers — a code Nova-2
// doesn't support just gets rejected by Deepgram itself, same as an admin
// typing an unsupported code ever could before this became a dropdown).
// One entry per language (base code, not every regional variant) — a
// regional code can still be set via the model field's neighboring API if
// ever needed, but the base code is what the overwhelming majority of users
// want. "multi" is Deepgram's own automatic multi-language/code-switching
// mode (English, Spanish, French, German, Hindi, Russian, Portuguese,
// Japanese, Italian, Dutch) — also this app's own default when no language
// preference is set at all (app/services/asr/resolve.py).
const DEEPGRAM_LANGUAGES: { code: string; label: string }[] = [
  { code: "multi", label: "Multi (auto-detect, code-switching)" },
  { code: "af", label: "Afrikaans" },
  { code: "ar", label: "Arabic" },
  { code: "hy", label: "Armenian" },
  { code: "as", label: "Assamese" },
  { code: "be", label: "Belarusian" },
  { code: "bn", label: "Bengali" },
  { code: "bs", label: "Bosnian" },
  { code: "bg", label: "Bulgarian" },
  { code: "ca", label: "Catalan" },
  { code: "zh", label: "Chinese (Mandarin)" },
  { code: "zh-HK", label: "Chinese (Cantonese)" },
  { code: "hr", label: "Croatian" },
  { code: "cs", label: "Czech" },
  { code: "da", label: "Danish" },
  { code: "nl", label: "Dutch" },
  { code: "en", label: "English" },
  { code: "et", label: "Estonian" },
  { code: "fi", label: "Finnish" },
  { code: "nl-BE", label: "Flemish" },
  { code: "fr", label: "French" },
  { code: "ka", label: "Georgian" },
  { code: "de", label: "German" },
  { code: "el", label: "Greek" },
  { code: "gu", label: "Gujarati" },
  { code: "he", label: "Hebrew" },
  { code: "hi", label: "Hindi" },
  { code: "hu", label: "Hungarian" },
  { code: "id", label: "Indonesian" },
  { code: "it", label: "Italian" },
  { code: "ja", label: "Japanese" },
  { code: "kn", label: "Kannada" },
  { code: "ko", label: "Korean" },
  { code: "lv", label: "Latvian" },
  { code: "lt", label: "Lithuanian" },
  { code: "mk", label: "Macedonian" },
  { code: "ms", label: "Malay" },
  { code: "mr", label: "Marathi" },
  { code: "mn", label: "Mongolian" },
  { code: "ne", label: "Nepali" },
  { code: "no", label: "Norwegian" },
  { code: "ps", label: "Pashto" },
  { code: "fa", label: "Persian" },
  { code: "pl", label: "Polish" },
  { code: "pt", label: "Portuguese" },
  { code: "pa", label: "Punjabi" },
  { code: "ro", label: "Romanian" },
  { code: "ru", label: "Russian" },
  { code: "sr", label: "Serbian" },
  { code: "sk", label: "Slovak" },
  { code: "sl", label: "Slovenian" },
  { code: "es", label: "Spanish" },
  { code: "sv", label: "Swedish" },
  { code: "tl", label: "Tagalog" },
  { code: "ta", label: "Tamil" },
  { code: "te", label: "Telugu" },
  { code: "th", label: "Thai" },
  { code: "tr", label: "Turkish" },
  { code: "uk", label: "Ukrainian" },
  { code: "ur", label: "Urdu" },
  { code: "vi", label: "Vietnamese" },
];

export default function Settings() {
  const { user, refreshUser } = useAuth();
  const confirm = useConfirm();
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

  const [apiKeys, setApiKeys] = useState<ApiKey[] | null>(null);
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyDuration, setNewKeyDuration] = useState(DEFAULT_KEY_DURATION_MINUTES);
  const [creatingKey, setCreatingKey] = useState(false);
  const [deletingKeyId, setDeletingKeyId] = useState<string | null>(null);
  const [durationDrafts, setDurationDrafts] = useState<Record<string, number>>({});
  const [savingDurationId, setSavingDurationId] = useState<string | null>(null);
  // The one and only time a real key is ever visible — cleared on
  // dismiss, and never recoverable again after that (only its hash is
  // stored server-side).
  const [revealedKey, setRevealedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const newKeyNameRef = useRef<HTMLInputElement>(null);
  const [keyNameNeeded, setKeyNameNeeded] = useState(false);

  const [fullName, setFullName] = useState("");
  const [avatarSeed, setAvatarSeed] = useState("");
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
    api.listApiKeys().then(setApiKeys);
  }, []);

  function onCreateApiKeyClick() {
    if (!newKeyName.trim()) {
      setKeyNameNeeded(true);
      newKeyNameRef.current?.focus();
      return;
    }
    setKeyNameNeeded(false);
    onCreateApiKey();
  }

  async function onCreateApiKey() {
    const name = newKeyName.trim();
    if (!name) return;
    if (newKeyDuration < MIN_KEY_DURATION_MINUTES || newKeyDuration > MAX_KEY_DURATION_MINUTES) {
      setError(`Max duration must be between ${MIN_KEY_DURATION_MINUTES} and ${MAX_KEY_DURATION_MINUTES} minutes`);
      return;
    }
    setError(null);
    setCreatingKey(true);
    try {
      const created = await api.createApiKey(name, newKeyDuration);
      setApiKeys((prev) => [...(prev ?? []), created]);
      setRevealedKey(created);
      setNewKeyName("");
      setNewKeyDuration(DEFAULT_KEY_DURATION_MINUTES);
      setCopied(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create API key");
    } finally {
      setCreatingKey(false);
    }
  }

  async function onSaveKeyDuration(key: ApiKey) {
    const minutes = durationDrafts[key.id] ?? key.max_duration_minutes;
    if (minutes === key.max_duration_minutes) return;
    if (minutes < MIN_KEY_DURATION_MINUTES || minutes > MAX_KEY_DURATION_MINUTES) {
      setError(`Max duration must be between ${MIN_KEY_DURATION_MINUTES} and ${MAX_KEY_DURATION_MINUTES} minutes`);
      return;
    }
    setError(null);
    setSavingDurationId(key.id);
    try {
      const updated = await api.updateApiKey(key.id, { max_duration_minutes: minutes });
      setApiKeys((prev) => prev?.map((row) => (row.id === key.id ? updated : row)) ?? null);
      setDurationDrafts((prev) => {
        const next = { ...prev };
        delete next[key.id];
        return next;
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update API key");
    } finally {
      setSavingDurationId(null);
    }
  }

  async function onDeleteApiKey(key: ApiKey) {
    const ok = await confirm({
      title: `Delete "${key.name}"?`,
      description: "Anything still using this key will stop being able to authenticate immediately.",
      confirmLabel: "Delete key",
      variant: "danger",
    });
    if (!ok) return;
    setError(null);
    setDeletingKeyId(key.id);
    try {
      await api.deleteApiKey(key.id);
      setApiKeys((prev) => prev?.filter((k) => k.id !== key.id) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete API key");
    } finally {
      setDeletingKeyId(null);
    }
  }

  async function onCopyRevealedKey() {
    if (!revealedKey) return;
    try {
      await navigator.clipboard.writeText(revealedKey.key);
      setCopied(true);
    } catch {
      // Clipboard access can be denied by the browser — the key text is
      // still selectable/visible either way, so this is a soft failure.
    }
  }

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
    if (user) {
      setFullName(user.full_name);
      setAvatarSeed((prev) => prev || user.email);
    }
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
    if (user?.voice_enrolled) {
      const ok = await confirm({
        title: "Replace your voice sample?",
        description: "The existing enrollment will be overwritten once you finish recording.",
        confirmLabel: "Re-record",
        variant: "danger",
      });
      if (!ok) return;
    }
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
    const ok = await confirm({
      title: "Remove your voice sample?",
      description: "Speaker matching will stop using this enrollment until you record a new one.",
      confirmLabel: "Remove sample",
      variant: "danger",
    });
    if (!ok) return;
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
    const ok = await confirm({
      title: "Remove this API key?",
      description: "This provider will fall back to an instance-wide key if one is set, or disconnect.",
      confirmLabel: "Remove key",
      variant: "danger",
    });
    if (!ok) return;
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
    const ok = await confirm({
      title: "Remove this API key?",
      description: "Transcription will fall back to local whisper, or an instance-wide Deepgram key if one is set.",
      confirmLabel: "Remove key",
      variant: "danger",
    });
    if (!ok) return;
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

      {/* TEMP: avatar seed playground — remove once the bird style is locked. */}
      <section className="card mb-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Avatar tester</h2>
        <p className="mt-1 text-sm text-ink-muted">Temporary. Type a seed (email) and the bird updates.</p>
        <div className="mt-5 flex items-center gap-4">
          <UserAvatar
            email={avatarSeed || "preview"}
            size={72}
            className="h-[72px] w-[72px] shrink-0 rounded border border-border dark:border-border-dark"
          />
          <div className="min-w-0 flex-1">
            <label className="label" htmlFor="avatar-seed">
              Seed
            </label>
            <input
              id="avatar-seed"
              type="text"
              value={avatarSeed}
              onChange={(e) => setAvatarSeed(e.target.value)}
              placeholder="email@example.com"
              className="field text-sm"
            />
          </div>
        </div>
      </section>

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
                      {aiOverview.speech_to_text.active === "deepgram" && (
                        <>
                          {" · "}
                          {aiOverview.speech_to_text.language}
                        </>
                      )}
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
                      <select
                        value={inputs.sttLanguage ?? ""}
                        onChange={(e) => setInputs((prev) => ({ ...prev, sttLanguage: e.target.value }))}
                        className="field w-full text-sm"
                      >
                        <option value="">Default (auto-detect — recommended)</option>
                        {DEEPGRAM_LANGUAGES.map((lang) => (
                          <option key={lang.code} value={lang.code}>
                            {lang.label}
                          </option>
                        ))}
                      </select>
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

      <section className="card mt-6 p-6">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">API keys</h2>
            <p className="mt-1 text-xs text-ink-subtle">
              Let an external system create/read meetings and stream a live recording as your account —
              see <code className="text-[11px]">API.md</code> for the full reference. Keys are bound to
              the organization you&apos;re in when you create them and don&apos;t follow the switcher.
              Each key has a max live-session length (default {DEFAULT_KEY_DURATION_MINUTES} min) so a
              hung integration can&apos;t record forever.
            </p>
          </div>
        </div>

        {revealedKey && (
          <div className="mb-4 rounded-sm border border-accent/30 bg-black/[0.02] p-3 dark:bg-white/[0.04]">
            <p className="text-xs font-medium text-ink dark:text-ink-inverted">
              "{revealedKey.name}" created — copy it now, you won't be able to see it again.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code className="flex-1 overflow-x-auto rounded-sm border border-border bg-surface-raised px-2 py-1.5 text-xs dark:border-border-dark dark:bg-surface-dark-raised">
                {revealedKey.key}
              </code>
              <button onClick={onCopyRevealedKey} className="btn-secondary shrink-0 text-xs">
                {copied ? "Copied ✓" : "Copy"}
              </button>
            </div>
            <button
              onClick={() => setRevealedKey(null)}
              className="mt-2 text-xs text-ink-subtle hover:text-ink dark:hover:text-ink-inverted"
            >
              Done, I've saved it
            </button>
          </div>
        )}

        <form
          className="mb-4"
          onSubmit={(e) => {
            e.preventDefault();
            onCreateApiKeyClick();
          }}
        >
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={newKeyNameRef}
              type="text"
              placeholder="Key name, e.g. Zapier integration"
              value={newKeyName}
              onChange={(e) => {
                setNewKeyName(e.target.value);
                if (e.target.value.trim()) setKeyNameNeeded(false);
              }}
              className={`field min-w-48 flex-1 text-sm ${
                keyNameNeeded ? "border-status-danger focus:border-status-danger" : ""
              }`}
            />
            <label className="flex items-center gap-1.5 text-xs text-ink-subtle">
              <input
                type="number"
                min={MIN_KEY_DURATION_MINUTES}
                max={MAX_KEY_DURATION_MINUTES}
                value={newKeyDuration}
                onChange={(e) => setNewKeyDuration(Number(e.target.value) || DEFAULT_KEY_DURATION_MINUTES)}
                className="field w-16 text-sm"
              />
              min max
            </label>
            <button
              type="submit"
              disabled={creatingKey}
              aria-disabled={!newKeyName.trim()}
              className={`btn-secondary shrink-0 ${newKeyName.trim() ? "" : "opacity-40"}`}
            >
              {creatingKey ? "Creating…" : "Create key"}
            </button>
          </div>
          {keyNameNeeded && (
            <p className="mt-1.5 text-xs text-status-danger">Give the key a name first.</p>
          )}
        </form>

        {apiKeys === null && <p className="text-sm text-ink-muted">Loading…</p>}
        {apiKeys?.length === 0 && <p className="text-sm text-ink-muted">No API keys yet.</p>}
        {apiKeys && apiKeys.length > 0 && (
          <ul className="divide-y divide-border dark:divide-border-dark">
            {apiKeys.map((key) => (
              <li key={key.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-sm text-ink dark:text-ink-inverted">{key.name}</p>
                  <p className="text-xs text-ink-subtle">
                    <code>{key.key_prefix}</code> · created {new Date(key.created_at).toLocaleDateString()}
                    {key.last_used_at
                      ? ` · last used ${new Date(key.last_used_at).toLocaleDateString()}`
                      : " · never used"}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <label className="flex items-center gap-1.5 text-xs text-ink-subtle">
                    <input
                      type="number"
                      min={MIN_KEY_DURATION_MINUTES}
                      max={MAX_KEY_DURATION_MINUTES}
                      value={durationDrafts[key.id] ?? key.max_duration_minutes}
                      onChange={(e) =>
                        setDurationDrafts((prev) => ({
                          ...prev,
                          [key.id]: Number(e.target.value) || MIN_KEY_DURATION_MINUTES,
                        }))
                      }
                      onBlur={() => onSaveKeyDuration(key)}
                      disabled={savingDurationId === key.id}
                      className="field w-16 text-sm"
                    />
                    min
                  </label>
                  <button
                    onClick={() => onDeleteApiKey(key)}
                    disabled={deletingKeyId === key.id}
                    className="shrink-0 text-xs text-ink-subtle hover:text-status-danger"
                  >
                    {deletingKeyId === key.id ? "…" : "Delete"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </AppShell>
  );
}
