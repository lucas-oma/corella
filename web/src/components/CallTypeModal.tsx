import { useEffect, useState } from "react";

import { api, type CallTypeOption } from "@/lib/api";

/** Shown when "Record live" or "Upload recording" is clicked (Dashboard.tsx)
 * — call types are admin-managed now (Admin.tsx), not a fixed compile-time
 * list, so there's no static dropdown to put in the page header anymore;
 * the choice moves to the moment it's actually needed instead. */
export default function CallTypeModal({
  open,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: (callTypeId: string) => void;
}) {
  const [types, setTypes] = useState<CallTypeOption[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || types !== null) return;
    api
      .getCallTypes()
      .then((options) => {
        setTypes(options);
        setSelected(options.find((t) => t.is_default)?.id ?? options[0]?.id ?? null);
      })
      .catch(() => setError("Couldn't load call types"));
  }, [open, types]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="card w-full max-w-sm p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">What kind of call is this?</h2>
        <p className="mt-1 text-sm text-ink-muted">Steers what the post-call report focuses on.</p>

        {error && <p className="mt-4 text-sm text-status-danger">{error}</p>}

        {types === null && !error && <p className="mt-5 text-sm text-ink-muted">Loading…</p>}

        {types !== null && (
          <ul className="mt-5 max-h-64 space-y-1 overflow-y-auto">
            {types.map((type) => (
              <li key={type.id}>
                <label className="flex cursor-pointer items-center gap-2.5 rounded px-2 py-1.5 hover:bg-black/[0.03] dark:hover:bg-white/[0.04]">
                  <input
                    type="radio"
                    name="call-type"
                    checked={selected === type.id}
                    onChange={() => setSelected(type.id)}
                    className="accent-accent"
                  />
                  <span className="text-sm text-ink dark:text-ink-inverted">
                    {type.name}
                    {type.is_default && <span className="ml-1.5 text-xs text-ink-subtle">(default)</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button onClick={onCancel} className="btn-secondary">
            Cancel
          </button>
          <button
            onClick={() => selected && onConfirm(selected)}
            disabled={!selected}
            className="btn-primary"
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
