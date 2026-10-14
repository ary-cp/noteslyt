import type * as React from "react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  width = 480,
  labelledBy,
  dismissable = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: number;
  labelledBy?: string;
  /** False: backdrop click + Escape don't close (the ✕ button still works).
   * Used for in-progress quizzes/exams so a misclick never loses the attempt. */
  dismissable?: boolean;
}) {
  const titleId = useRef(labelledBy ?? `modal-${Math.random().toString(36).slice(2)}`);

  useEffect(() => {
    if (!open) return;
    if (dismissable) {
      const onKey = (e: KeyboardEvent) => {
        if (e.key === "Escape") onClose();
      };
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }
  }, [open, onClose, dismissable]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div
      className="anim-fade fixed inset-0 z-[var(--z-modal)] flex items-center justify-center bg-black/60 p-4"
      style={{ ["--z-modal" as string]: 60, zIndex: 60 }}
      role="presentation"
      onMouseDown={(e) => {
        if (dismissable && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId.current}
        className="anim-modal flex max-h-[88vh] w-full flex-col overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--modal)]"
        style={{ maxWidth: width, boxShadow: "var(--shadow-modal)" }}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <h2 id={titleId.current} className="text-[15px] font-semibold text-[var(--text-primary)]">
              {title}
            </h2>
            {subtitle && <p className="mt-0.5 text-[13px] text-[var(--text-secondary)]">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]"
          >
            <X size={15} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3.5">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel = "Delete",
  busy,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  body: string;
  confirmLabel?: string;
  busy?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      width={420}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="h-8 cursor-pointer rounded-[7px] border border-[var(--border)] px-3 text-[13px] font-medium text-[var(--text-primary)] hover:bg-[var(--surface-secondary)]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="h-8 cursor-pointer rounded-[7px] bg-[var(--danger)] px-3 text-[13px] font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      <p className="text-sm text-[var(--text-secondary)]">{body}</p>
    </Modal>
  );
}
