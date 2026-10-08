import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { useToast, type ToastTone } from "../../lib/toast";

const icons: Record<ToastTone, React.ReactNode> = {
  success: <CheckCircle2 size={16} className="text-[var(--accent)]" />,
  error: <AlertCircle size={16} className="text-[var(--danger)]" />,
  warning: <TriangleAlert size={16} className="text-[var(--warning)]" />,
  info: <Info size={16} className="text-[var(--text-secondary)]" />,
};

export function ToastViewport() {
  const { toasts, dismiss } = useToast();
  return (
    <div aria-live="polite" className="fixed right-4 bottom-4 z-[90] flex w-[320px] max-w-[calc(100vw-2rem)] flex-col gap-2" style={{ zIndex: 90 }}>
      {toasts.map((t) => (
        <div
          key={t.id}
          className="anim-toast flex items-start gap-2.5 rounded-[8px] border border-[var(--border)] bg-[var(--surface-raised)] px-3 py-2.5"
          style={{ boxShadow: "var(--shadow-pop)" }}
        >
          <span className="mt-0.5 shrink-0">{icons[t.tone]}</span>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium text-[var(--text-primary)]">{t.title}</p>
            {t.detail && <p className="mt-0.5 text-xs break-words text-[var(--text-secondary)]">{t.detail}</p>}
          </div>
          <button
            type="button"
            aria-label="Dismiss notification"
            onClick={() => dismiss(t.id)}
            className="cursor-pointer rounded p-0.5 text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          >
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}
