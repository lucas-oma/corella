import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { api, type ProviderStatus } from "@/lib/api";

const PROVIDER_META: Record<ProviderStatus["provider"], { name: string; hint: string }> = {
  anthropic: { name: "Anthropic", hint: "Claude models via your own API key" },
  openai: { name: "OpenAI", hint: "GPT models via your own API key" },
  gemini: { name: "Gemini", hint: "Google Gemini models via your own API key" },
  ollama: { name: "Ollama", hint: "A local model server you point Corella at" },
};

export default function Settings() {
  const [providers, setProviders] = useState<ProviderStatus[] | null>(null);

  useEffect(() => {
    api.getProviderStatus().then(setProviders);
  }, []);

  return (
    <AppShell>
      <div className="mb-8">
        <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">Settings</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Copilot providers, knowledge base, and call profiles.
        </p>
      </div>

      <section className="card p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Copilot providers</h2>
        <p className="mt-1 text-sm text-ink-muted">
          The LLM(s) that will power live suggestions and post-call reports. Saving your own key
          here is coming soon — for now, an instance admin can set one for everyone via the
          server's <code>.env</code>.
        </p>
        <ul className="mt-5 divide-y divide-border dark:divide-border-dark">
          {providers === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
          {providers?.map((status) => {
            const meta = PROVIDER_META[status.provider];
            return (
              <li key={status.provider} className="flex items-center justify-between py-3">
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
                  {status.connected
                    ? status.source === "env"
                      ? "Connected via .env"
                      : "Connected"
                    : "Not connected"}
                </span>
              </li>
            );
          })}
        </ul>
      </section>
    </AppShell>
  );
}
