import type * as React from "react";
import { useId } from "react";

export const fieldCls =
  "h-9 w-full rounded-[7px] border border-[var(--border)] bg-[var(--input)] px-2.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent)] transition-colors duration-150";

export function Field({ label, error, children, hint }: { label: string; error?: string; hint?: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-[13px] font-medium text-[var(--text-primary)]">
        {label}
      </label>
      <div id={id}>{children}</div>
      {hint && !error && <p className="mt-1 text-xs text-[var(--text-muted)]">{hint}</p>}
      {error && (
        <p role="alert" className="mt-1 text-xs text-[var(--danger)]">
          {error}
        </p>
      )}
    </div>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${fieldCls} ${props.className ?? ""}`} />;
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`min-h-[76px] w-full resize-y rounded-[7px] border border-[var(--border)] bg-[var(--input)] px-2.5 py-2 text-sm outline-none focus:border-[var(--accent)] ${props.className ?? ""}`} />;
}

export function Checkbox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-[var(--text-primary)]">
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        onKeyDown={(e) => {
          if (e.key === " " || e.key === "Enter") {
            e.preventDefault();
            onChange(!checked);
          }
        }}
        className={`flex h-[18px] w-[18px] items-center justify-center rounded-[5px] border text-[11px] transition-colors ${
          checked ? "border-[var(--accent)] bg-[var(--accent)] text-white" : "border-[var(--border-strong)] bg-transparent"
        }`}
      >
        {checked ? "✓" : ""}
      </button>
      {label}
    </label>
  );
}

export function RadioGroup<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel: string;
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className="flex flex-wrap gap-2">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={`h-8 cursor-pointer rounded-[7px] border px-3 text-[13px] font-medium transition-colors ${
              active
                ? "border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--text-primary)]"
                : "border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-[22px] w-[38px] shrink-0 cursor-pointer rounded-full border transition-colors duration-150 ${
        checked ? "border-[var(--accent)] bg-[var(--accent)]" : "border-[var(--border-strong)] bg-[var(--surface-secondary)]"
      }`}
    >
      <span
        className={`absolute top-[2px] h-[16px] w-[16px] rounded-full bg-white transition-[left] duration-150 ${checked ? "left-[18px]" : "left-[2px]"}`}
      />
    </button>
  );
}

export function Slider({ value, min, max, step = 1, onChange, ariaLabel }: { value: number; min: number; max: number; step?: number; onChange: (v: number) => void; ariaLabel: string }) {
  return (
    <input
      type="range"
      aria-label={ariaLabel}
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="study-slider w-full"
      style={{ "--slider-progress": `${((value - min) / Math.max(1, max - min)) * 100}%` } as React.CSSProperties}
    />
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div role="tablist" className="flex gap-1 rounded-[8px] border border-[var(--border)] bg-[var(--surface-secondary)] p-1">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={t.value === value}
          onClick={() => onChange(t.value)}
          className={`h-7 flex-1 cursor-pointer rounded-[6px] px-2 text-[13px] font-medium whitespace-nowrap transition-colors ${
            t.value === value ? "bg-[var(--surface)] text-[var(--text-primary)] shadow-sm" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
