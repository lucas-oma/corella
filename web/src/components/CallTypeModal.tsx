import { useEffect, useState } from "react";

import { api, type CallTypeOption, type CaptureApp, type CaptureMode } from "@/lib/api";

export type CaptureChoice = {
  mode: Extract<CaptureMode, "open_mic" | "meeting_tab">;
  app: CaptureApp | null;
};

/** Shown when "Record live" or "Upload recording" is clicked (Dashboard.tsx)
 * — call types are admin-managed now (Admin.tsx), not a fixed compile-time
 * list, so there's no static dropdown to put in the page header anymore;
 * the choice moves to the moment it's actually needed instead. Live also
 * picks capture mode here (open mic vs a shared-tab meeting). */
export default function CallTypeModal({
  open,
  showCapture,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  showCapture: boolean;
  onCancel: () => void;
  onConfirm: (callTypeId: string, capture: CaptureChoice) => void;
}) {
  const [types, setTypes] = useState<CallTypeOption[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [captureMode, setCaptureMode] = useState<CaptureChoice["mode"]>("open_mic");
  const [captureApp, setCaptureApp] = useState<CaptureApp>("meet");
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

  useEffect(() => {
    if (open) {
      setCaptureMode("open_mic");
      setCaptureApp("meet");
    }
  }, [open]);

  if (!open) return null;

  function confirm() {
    if (!selected) return;
    onConfirm(selected, {
      mode: captureMode,
      app: captureMode === "meeting_tab" ? captureApp : null,
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className={`card w-full p-6 ${showCapture ? "max-w-2xl" : "max-w-md"}`}>
        <div
          className={
            showCapture
              ? "grid gap-6 sm:grid-cols-2 sm:gap-0"
              : ""
          }
        >
          <div className={showCapture ? "sm:pr-8" : ""}>
            <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">
              What kind of call is this?
            </h2>
            <p className="mt-1 text-sm text-ink-muted">Steers what the post-call report focuses on.</p>

            {error && <p className="mt-4 text-sm text-status-danger">{error}</p>}

            {types === null && !error && <p className="mt-5 text-sm text-ink-muted">Loading…</p>}

            {types !== null && (
              <ul className="mt-5 max-h-40 space-y-1 overflow-y-auto">
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
          </div>

          {showCapture && (
            <div className="border-t border-border pt-5 dark:border-border-dark sm:border-l sm:border-t-0 sm:pl-8 sm:pt-0">
              <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">
                How are you recording?
              </h2>
              <p className="mt-1 text-sm text-ink-muted">
                Open mic is one microphone. Browser meeting is your mic plus a shared tab.
              </p>
              <ul className="mt-5 space-y-1">
                <li>
                  <label className="flex cursor-pointer items-center gap-2.5 rounded px-2 py-1.5 hover:bg-black/[0.03] dark:hover:bg-white/[0.04]">
                    <input
                      type="radio"
                      name="capture-mode"
                      checked={captureMode === "open_mic"}
                      onChange={() => setCaptureMode("open_mic")}
                      className="accent-accent"
                    />
                    <span className="text-sm text-ink dark:text-ink-inverted">
                      Open mic <span className="ml-1.5 text-xs text-ink-subtle">(default)</span>
                    </span>
                  </label>
                </li>
                <li>
                  <label className="flex cursor-pointer items-center gap-2.5 rounded px-2 py-1.5 hover:bg-black/[0.03] dark:hover:bg-white/[0.04]">
                    <input
                      type="radio"
                      name="capture-mode"
                      checked={captureMode === "meeting_tab"}
                      onChange={() => setCaptureMode("meeting_tab")}
                      className="accent-accent"
                    />
                    <span className="text-sm text-ink dark:text-ink-inverted">Browser meeting</span>
                  </label>
                </li>
              </ul>
              {captureMode === "meeting_tab" && (
                <ul className="mt-2 flex flex-wrap gap-1.5 px-2">
                  {(
                    [
                      ["meet", "Google Meet"],
                      ["teams", "Teams"],
                      ["zoom", "Zoom"],
                      ["other", "Other"],
                    ] as const
                  ).map(([id, label]) => (
                    <li key={id}>
                      <button
                        type="button"
                        onClick={() => setCaptureApp(id)}
                        className={`rounded-full border px-2.5 py-0.5 text-xs ${
                          captureApp === id
                            ? "border-accent text-accent"
                            : "border-border text-ink-muted dark:border-border-dark"
                        }`}
                      >
                        {label}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <button onClick={onCancel} className="btn-secondary">
            Cancel
          </button>
          <button onClick={confirm} disabled={!selected} className="btn-primary">
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
