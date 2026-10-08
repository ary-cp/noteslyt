import type * as React from "react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/* Custom tooltip: every icon-only button gets one via data-tooltip or this wrapper.
   Small delay, theme-aware, no native title reliance. */
export function TooltipHost() {
  return null;
}

export function Tooltip({ label, children }: { label: string; children: React.ReactElement }) {
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const timer = useRef<number | null>(null);
  const anchor = useRef<HTMLElement | null>(null);

  const show = () => {
    timer.current = window.setTimeout(() => {
      const el = anchor.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setPos({ x: r.left + r.width / 2, y: r.bottom + 6 });
      setVisible(true);
    }, 350);
  };
  const hide = () => {
    if (timer.current) window.clearTimeout(timer.current);
    setVisible(false);
  };

  useEffect(() => () => hide(), []);

  const child = (
    <span
      ref={(n) => {
        anchor.current = n as unknown as HTMLElement;
      }}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      className="inline-flex"
    >
      {children}
    </span>
  );

  return (
    <>
      {child}
      {visible &&
        createPortal(
          <div
            role="tooltip"
            className="anim-fade pointer-events-none fixed z-[80] max-w-[240px] rounded-md border border-[var(--border)] bg-[var(--surface-raised)] px-2 py-1 text-xs text-[var(--text-primary)]"
            style={{ left: pos.x, top: pos.y, transform: "translateX(-50%)", boxShadow: "var(--shadow-pop)", zIndex: 80 }}
          >
            {label}
          </div>,
          document.body,
        )}
    </>
  );
}

/* Global CSS fallback: [data-tooltip] shows a pure-CSS tooltip (no JS).
   Used by IconButton so every icon button is covered even without a wrapper. */
export const tooltipStyles = `
.tooltip-host { position: relative; }
.tooltip-host[data-tooltip]:hover::after {
  content: attr(data-tooltip);
  position: absolute;
  top: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  white-space: nowrap;
  background: var(--surface-raised);
  border: 1px solid var(--border);
  color: var(--text-primary);
  font-size: 12px;
  padding: 3px 8px;
  border-radius: 6px;
  z-index: 80;
  pointer-events: none;
  box-shadow: var(--shadow-pop);
}
`;
