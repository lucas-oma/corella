import { useEffect, useId, useRef } from "react";

export type ConfirmVariant = "danger" | "default";

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  variant = "default",
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel: string;
  cancelLabel?: string;
  variant?: ConfirmVariant;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="card w-full max-w-sm p-6"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId} className="font-serif text-lg text-ink dark:text-ink-inverted">
          {title}
        </h2>
        {description && <p className="mt-1 text-sm text-ink-muted">{description}</p>}

        <div className="mt-6 flex justify-end gap-2">
          <button ref={cancelRef} type="button" onClick={onCancel} className="btn-secondary">
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={variant === "danger" ? "btn-danger" : "btn-primary"}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
