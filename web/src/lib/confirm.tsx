import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

import ConfirmDialog, { type ConfirmVariant } from "@/components/ConfirmDialog";

export type ConfirmOptions = {
  title: string;
  description?: string;
  confirmLabel: string;
  cancelLabel?: string;
  variant?: ConfirmVariant;
};

type ConfirmRequest = ConfirmOptions & {
  resolve: (ok: boolean) => void;
};

const ConfirmContext = createContext<((opts: ConfirmOptions) => Promise<boolean>) | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const requestRef = useRef<ConfirmRequest | null>(null);

  const confirm = useCallback((opts: ConfirmOptions) => {
    requestRef.current?.resolve(false);
    return new Promise<boolean>((resolve) => {
      const next = { ...opts, resolve };
      requestRef.current = next;
      setRequest(next);
    });
  }, []);

  const settle = useCallback((ok: boolean) => {
    requestRef.current?.resolve(ok);
    requestRef.current = null;
    setRequest(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <ConfirmDialog
        open={request !== null}
        title={request?.title ?? ""}
        description={request?.description}
        confirmLabel={request?.confirmLabel ?? ""}
        cancelLabel={request?.cancelLabel}
        variant={request?.variant ?? "default"}
        onCancel={() => settle(false)}
        onConfirm={() => settle(true)}
      />
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): (opts: ConfirmOptions) => Promise<boolean> {
  const confirm = useContext(ConfirmContext);
  if (!confirm) throw new Error("useConfirm must be used within a ConfirmProvider");
  return confirm;
}
