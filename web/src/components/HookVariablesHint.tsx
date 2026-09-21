import { useEffect, useId, useRef, useState } from "react";

import type { AppSecret } from "@/lib/api";

type Phase = "pre" | "post";

type FieldKind = "string" | "json";

type FieldVar = {
  key: string;
  kind: FieldKind;
  hint: string;
};

const PRE_CALL_FIELDS: FieldVar[] = [
  { key: "meeting_id", kind: "string", hint: "Meeting UUID" },
  { key: "owner_id", kind: "string", hint: "Caller user UUID" },
  { key: "owner_name", kind: "string", hint: "Caller display name" },
  { key: "title", kind: "string", hint: "Meeting title at create time" },
  { key: "call_type", kind: "string", hint: "Call type name, or null" },
  { key: "capture_mode", kind: "string", hint: "open_mic, meeting_tab, or upload" },
  { key: "capture_app", kind: "string", hint: "meet / teams / zoom / other, or null" },
  { key: "status", kind: "string", hint: "Meeting status (usually pending)" },
  { key: "created_at", kind: "string", hint: "ISO-8601 create time" },
];

const POST_CALL_FIELDS: FieldVar[] = [
  { key: "meeting_id", kind: "string", hint: "Meeting UUID" },
  { key: "owner_id", kind: "string", hint: "Caller user UUID" },
  { key: "owner_name", kind: "string", hint: "Caller display name" },
  { key: "title", kind: "string", hint: "Report title — the LLM may rewrite this" },
  { key: "call_type", kind: "string", hint: "Call type name, or null" },
  { key: "capture_mode", kind: "string", hint: "open_mic, meeting_tab, or upload" },
  { key: "capture_app", kind: "string", hint: "meet / teams / zoom / other, or null" },
  { key: "status", kind: "string", hint: "Meeting status after the report" },
  { key: "summary", kind: "string", hint: "Report summary" },
  { key: "key_topics", kind: "json", hint: "Array of topic strings" },
  { key: "sentiment", kind: "string", hint: "Report sentiment" },
  { key: "notable_quotes", kind: "json", hint: "Array of quote strings" },
  { key: "coach_score", kind: "json", hint: "Number" },
  { key: "estimated_cost_usd", kind: "json", hint: "Number" },
  { key: "talk_ratio", kind: "json", hint: '{"me": <pct>, "them": <pct>}' },
  { key: "action_items", kind: "json", hint: "Report digest [{text, status}] — not the live pile" },
  { key: "copilot_insights", kind: "json", hint: "Live-copilot timeline array" },
  { key: "transcript", kind: "string", hint: "Me: / Speaker 1: / names — not channel Them" },
  { key: "created_at", kind: "string", hint: "ISO-8601 create time" },
  { key: "started_at", kind: "string", hint: "ISO-8601 start, or null" },
  { key: "ended_at", kind: "string", hint: "ISO-8601 end, or null" },
  { key: "duration_seconds", kind: "json", hint: "Number, or null" },
  { key: "full_payload", kind: "json", hint: "Entire post-call object at once" },
];

function token(key: string): string {
  return `{{corella.${key}}}`;
}

function secretToken(name: string): string {
  return `{{secret.${name}}}`;
}

function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(value);
  }
  return Promise.reject(new Error("clipboard unavailable"));
}

export default function HookVariablesHint({
  phase,
  secrets,
}: {
  phase: Phase;
  secrets: AppSecret[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <p className="text-xs text-ink-subtle">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="text-accent hover:underline"
        >
          See available variables
        </button>
      </p>
      {open && (
        <HookVariablesDialog
          phase={phase}
          secrets={secrets}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

function HookVariablesDialog({
  phase,
  secrets,
  onClose,
}: {
  phase: Phase;
  secrets: AppSecret[];
  onClose: () => void;
}) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const fields = phase === "pre" ? PRE_CALL_FIELDS : POST_CALL_FIELDS;

  useEffect(() => {
    closeRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="card flex max-h-[min(36rem,90vh)] w-full max-w-lg flex-col p-6"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId} className="font-serif text-lg text-ink dark:text-ink-inverted">
          Available variables
        </h2>
        <p className="mt-1 text-sm text-ink-muted">
          Click a token to copy it. Strings go inside quotes; arrays, objects, and
          numbers sit outside quotes.
        </p>

        <div className="mt-4 min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
              Body — {phase === "pre" ? "before the call" : "after the report"}
            </h3>
            <ul className="mt-1.5 divide-y divide-border dark:divide-border-dark">
              {fields.map((field) => (
                <CopyRow
                  key={field.key}
                  token={token(field.key)}
                  hint={field.hint}
                  extra={field.kind === "json" ? "no quotes" : undefined}
                />
              ))}
            </ul>
          </section>

          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
              Headers — secrets
            </h3>
            {secrets.length === 0 ? (
              <p className="mt-1.5 text-sm text-ink-muted">
                Add a secret in Secrets above, then use{" "}
                <code className="text-[11px]">{"{{secret.NAME}}"}</code> as a header
                value.
              </p>
            ) : (
              <ul className="mt-1.5 divide-y divide-border dark:divide-border-dark">
                {secrets.map((secret) => (
                  <CopyRow
                    key={secret.id}
                    token={secretToken(secret.name)}
                    hint="Organization secret"
                  />
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
              Always sent
            </h3>
            <p className="mt-1.5 text-sm text-ink-muted">
              X-Corella-App-Url, X-Corella-Meeting-Id, X-Corella-User-Id,
              X-Corella-Org-Id.
            </p>
          </section>
        </div>

        <div className="mt-5 flex justify-end">
          <button ref={closeRef} type="button" onClick={onClose} className="btn-secondary">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function CopyRow({
  token,
  hint,
  extra,
}: {
  token: string;
  hint: string;
  extra?: string;
}) {
  const [copied, setCopied] = useState(false);

  function onCopy() {
    void copyText(token).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    });
  }

  return (
    <li>
      <button
        type="button"
        onClick={onCopy}
        className="flex w-full items-baseline justify-between gap-3 py-1.5 text-left hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
      >
        <code className="text-[12px] text-status-info">{token}</code>
        <span className="min-w-0 text-right text-xs text-ink-subtle">
          {copied ? "Copied" : extra ? `${hint} · ${extra}` : hint}
        </span>
      </button>
    </li>
  );
}
