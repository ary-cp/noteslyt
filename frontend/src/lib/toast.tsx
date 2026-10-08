import type * as React from "react";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

export type ToastTone = "success" | "error" | "warning" | "info";

export interface Toast {
  id: number;
  title: string;
  detail?: string;
  tone: ToastTone;
}

interface ToastCtx {
  toasts: Toast[];
  push: (title: string, opts?: { detail?: string; tone?: ToastTone }) => void;
  dismiss: (id: number) => void;
}

const Ctx = createContext<ToastCtx>({ toasts: [], push: () => undefined, dismiss: () => undefined });

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (title: string, opts?: { detail?: string; tone?: ToastTone }) => {
      const id = seq.current++;
      const tone = opts?.tone ?? "info";
      setToasts((prev) => [...prev.slice(-4), { id, title, detail: opts?.detail, tone }]);
      window.setTimeout(() => dismiss(id), 4200);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toasts, push, dismiss }), [toasts, push, dismiss]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useToast = () => useContext(Ctx);
