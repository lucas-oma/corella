import AppShell from "@/components/AppShell";

const PROVIDERS = [
  { name: "Anthropic", hint: "Claude models via your own API key" },
  { name: "OpenAI", hint: "GPT models via your own API key" },
  { name: "Gemini", hint: "Google Gemini models via your own API key" },
  { name: "Ollama", hint: "A local model server you point Corella at" },
];

export default function Settings() {
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
          Connect the LLM(s) that power live suggestions and post-call reports. Coming soon.
        </p>
        <ul className="mt-5 divide-y divide-border dark:divide-border-dark">
          {PROVIDERS.map((provider) => (
            <li key={provider.name} className="flex items-center justify-between py-3">
              <div>
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                  {provider.name}
                </p>
                <p className="text-xs text-ink-subtle">{provider.hint}</p>
              </div>
              <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-subtle dark:border-border-dark">
                Not connected
              </span>
            </li>
          ))}
        </ul>
      </section>
    </AppShell>
  );
}
