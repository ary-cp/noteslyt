/**
 * One row of the learner model: topic name, P(mastery) bar, attempts.
 * The bar is the single accent colour; the label chip stays monochrome.
 */

import type { TopicMastery } from "../lib/types";

interface Props {
  topic: TopicMastery;
  active?: boolean;
  onClick?: () => void;
}

export function MasteryBar({ topic, active, onClick }: Props) {
  const pct = Math.round(topic.p_mastery * 100);
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      title={topic.description}
      className={`w-full rounded-lg px-2.5 py-2 text-left transition-colors ${
        active ? "bg-accent-soft" : "hover:bg-surface-muted"
      } disabled:cursor-default`}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="truncate text-[13px] font-medium">{topic.name}</span>
        <span className="shrink-0 text-[11px] tabular-nums text-ink-muted">
          {pct}% · {topic.attempts} {topic.attempts === 1 ? "try" : "tries"}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-line">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-700 ease-out"
          style={{ width: `${pct}%`, opacity: topic.label === "weak" ? 0.55 : topic.label === "developing" ? 0.8 : 1 }}
        />
      </div>
    </button>
  );
}
