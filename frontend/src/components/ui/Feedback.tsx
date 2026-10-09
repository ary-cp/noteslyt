import type * as React from "react";
import { AlertTriangle, FileQuestion, Inbox, SearchX } from "lucide-react";

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "accent" | "danger" | "warning" }) {
  const tones: Record<string, string> = {
    neutral: "border-[var(--border)] bg-[var(--surface-secondary)] text-[var(--text-secondary)]",
    accent: "border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent)]",
    danger: "border-[var(--danger)]/30 bg-[var(--danger-soft)] text-[var(--danger)]",
    warning: "border-[var(--warning)]/30 bg-[var(--warning-soft)] text-[var(--warning)]",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-px text-[11px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function ProgressBar({ value, className = "" }: { value: number; className?: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className={`h-1 overflow-hidden rounded-full bg-[var(--border)] ${className}`} role="progressbar" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-200" style={{ width: `${v}%` }} />
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`animate-pulse rounded-md bg-[var(--surface-secondary)] ${className}`} />;
}

export function SkeletonRows({ n = 4, rowClass = "h-[52px]" }: { n?: number; rowClass?: string }) {
  return (
    <div className="grid gap-2" aria-label="Loading">
      {Array.from({ length: n }).map((_, i) => (
        <Skeleton key={i} className={rowClass} />
      ))}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-[8px] border border-[var(--border)] bg-[var(--surface-secondary)] text-[var(--text-muted)]">
        {icon ?? <Inbox size={18} />}
      </div>
      <p className="text-sm font-medium text-[var(--text-primary)]">{title}</p>
      <p className="mt-1 max-w-[280px] text-[13px] text-[var(--text-secondary)]">{body}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", body, onRetry }: { title?: string; body: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-[8px] border border-[var(--danger)]/30 bg-[var(--danger-soft)] text-[var(--danger)]">
        <AlertTriangle size={18} />
      </div>
      <p className="text-sm font-medium text-[var(--text-primary)]">{title}</p>
      <p className="mt-1 max-w-[320px] text-[13px] text-[var(--text-secondary)]">{body}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 h-8 cursor-pointer rounded-[7px] border border-[var(--border)] px-3 text-[13px] font-medium hover:bg-[var(--surface-secondary)]"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function NoResults({ query }: { query: string }) {
  return (
    <EmptyState
      icon={<SearchX size={18} />}
      title="No matches"
      body={query ? `Nothing matches “${query}”. Try a different term.` : "Nothing here yet."}
    />
  );
}

export function NotFoundArt() {
  return (
    <EmptyState icon={<FileQuestion size={18} />} title="Not in this notebook" body="The page you asked for does not exist or was deleted." />
  );
}
