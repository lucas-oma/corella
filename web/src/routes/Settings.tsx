import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { ApiError, api, type ProviderStatus } from "@/lib/api";

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
  const [providers, setProviders] = useState<ProviderStatus[] | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getProviderStatus().then(setProviders);
  }, []);

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
