import type * as React from "react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";

/* ---------- Custom dropdown (keyboard: Esc / arrows / Enter) ---------- */

export interface MenuOption {
  value: string;
  label: string;
  hint?: string;
  disabled?: boolean;
}

export function Dropdown({
  value,
  options,
  onChange,
  placeholder = "Select…",
  ariaLabel,
  className = "",
}: {
  value: string;
  options: MenuOption[];
  onChange: (v: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open ]);

  const current = options.find((o) => o.value === value);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        setActive(0);
        return;
      }
      const enabled = options.map((o, i) => (!o.disabled ? i : -1)).filter((i) => i >= 0);
      if (!enabled.length) return;
      let idx = enabled.indexOf(active);
      idx = e.key === "ArrowDown" ? (idx + 1) % enabled.length : (idx - 1 + enabled.length) % enabled.length;
      setActive(enabled[idx]);
    } else if (e.key === "Enter" || e.key === " ") {
      if (open && active >= 0 && !options[active].disabled) {
        e.preventDefault();
        onChange(options[active].value);
        setOpen(false);
      } else if (!open) {
        e.preventDefault();
        setOpen(true);
      }
    }
  };

  return (
    <div ref={box} className={`relative ${className}`}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          setActive(options.findIndex((o) => o.value === value));
        }}
        onKeyDown={onKey}
        title={current?.label ?? undefined}
        className="flex h-8 w-full cursor-pointer items-center justify-between gap-2 rounded-[7px] border border-[var(--border)] bg-[var(--input)] px-2.5 text-[13px] text-[var(--text-primary)] hover:border-[var(--border-strong)]"
      >
        <span className="truncate">{current?.label ?? <span className="text-[var(--text-muted)]">{placeholder}</span>}</span>
        <ChevronDown size={14} className={`shrink-0 text-[var(--text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div
          role="listbox"
          className="anim-fade absolute z-40 mt-1 max-h-56 w-full min-w-[160px] overflow-y-auto rounded-[8px] border border-[var(--border)] bg-[var(--surface-raised)] p-1"
          style={{ boxShadow: "var(--shadow-pop)", zIndex: 40 }}
        >
          {options.map((o, i) => (
            <button
              key={o.value}
              role="option"
              aria-selected={o.value === value}
              disabled={o.disabled}
              title={o.label}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
              onMouseEnter={() => setActive(i)}
              className={`flex w-full cursor-pointer items-center justify-between gap-2 rounded-[6px] px-2.5 py-1.5 text-left text-[13px] disabled:cursor-not-allowed disabled:opacity-40 ${
                i === active ? "bg-[var(--surface-secondary)]" : ""
              } ${o.value === value ? "text-[var(--text-primary)]" : "text-[var(--text-secondary)]"}`}
            >
              <span className="min-w-0">
                <span className="block truncate">{o.label}</span>
                {o.hint && <span className="block truncate text-xs text-[var(--text-muted)]">{o.hint}</span>}
              </span>
              {o.value === value && <Check size={14} className="shrink-0 text-[var(--accent)]" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- Custom context menu ---------- */

export interface CtxItem {
  label: string;
  danger?: boolean;
  disabled?: boolean;
  onSelect: () => void;
}

export function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: CtxItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [focus, setFocus] = useState(0);

  useEffect(() => {
    ref.current?.focus();
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [onClose]);

  const w = 210;
  const left = Math.min(x, window.innerWidth - w - 8);
  const top = Math.min(y, window.innerHeight - items.length * 34 - 16);

  return createPortal(
    <div
      ref={ref}
      tabIndex={-1}
      role="menu"
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setFocus((f) => (f + 1) % items.length);
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          setFocus((f) => (f - 1 + items.length) % items.length);
        }
        if (e.key === "Enter") {
          const it = items[focus];
          if (it && !it.disabled) {
            it.onSelect();
            onClose();
          }
        }
      }}
      className="anim-fade fixed z-[70] rounded-[8px] border border-[var(--border)] bg-[var(--surface-raised)] p-1 outline-none"
      style={{ left, top, width: w, boxShadow: "var(--shadow-pop)", zIndex: 70 }}
    >
      {items.map((it, i) => (
        <button
          key={it.label}
          role="menuitem"
          disabled={it.disabled}
          onMouseEnter={() => setFocus(i)}
          onClick={() => {
            it.onSelect();
            onClose();
          }}
          className={`flex w-full cursor-pointer items-center rounded-[6px] px-2.5 py-[7px] text-left text-[13px] disabled:opacity-40 ${
            i === focus ? "bg-[var(--surface-secondary)]" : ""
          } ${it.danger ? "text-[var(--danger)]" : "text-[var(--text-primary)]"}`}
        >
          {it.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}

/* ---------- Custom popover ---------- */

export function Popover({
  open,
  onClose,
  anchor,
  children,
  width = 280,
}: {
  open: boolean;
  onClose: () => void;
  anchor: { x: number; y: number };
  children: React.ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const onDoc = () => onClose();
    document.addEventListener("keydown", onKey);
    const t = window.setTimeout(() => document.addEventListener("mousedown", onDoc), 0);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.clearTimeout(t);
      document.removeEventListener("mousedown", onDoc);
    };
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <div
      className="anim-fade fixed z-[50] rounded-[8px] border border-[var(--border)] bg-[var(--surface-raised)] p-2"
      style={{
        left: Math.min(anchor.x, window.innerWidth - width - 8),
        top: anchor.y,
        width,
        boxShadow: "var(--shadow-pop)",
        zIndex: 50,
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {children}
    </div>,
    document.body,
  );
}
