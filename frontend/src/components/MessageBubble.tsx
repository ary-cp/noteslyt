/**
 * One chat row.
 *   user      → compact grey bubble, right-aligned (ChatGPT style)
 *   assistant → full-width prose, no bubble, with a "Sources" footer of chips
 *   pending   → three blinking dots
 *   refusal   → muted styling + a small "not in your material" note
 */

import { FileText } from "lucide-react";
import type { Message } from "../lib/types";
import { MarkdownMessage } from "./MarkdownMessage";

interface Props {
  message: Message;
  onCite: (page: number) => void;
}

export function MessageBubble({ message, onCite }: Props) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-surface-muted border border-line px-4 py-2.5 text-[15px] leading-7 whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  if (message.pending) {
    return (
      <div className="flex items-center gap-1.5 px-1 py-3" aria-label="Thinking">
        <span className="dot" />
        <span className="dot" />
        <span className="dot" />
      </div>
    );
  }

  if (message.error) {
    return (
      <div className="rounded-xl border border-line bg-surface-muted px-4 py-3 text-sm text-ink-muted">
        {message.content}
      </div>
    );
  }

  const refused = message.grounded === false;

  return (
    <div className="px-1">
      {refused ? (
        <p className="text-[15px] leading-7 text-ink-muted italic">{message.content}</p>
      ) : (
        <MarkdownMessage markdown={message.content} onCite={onCite} />
      )}

      {!refused && message.citations && message.citations.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1 text-[11px] uppercase tracking-wide text-ink-muted">
            <FileText size={12} strokeWidth={1.75} /> Sources
          </span>
          {message.citations.map((c) => (
            <button
              key={c.page}
              type="button"
              className="cite"
              title={c.heading ? `${c.heading} — ${c.snippet}` : c.snippet}
              onClick={() => onCite(c.page)}
            >
              Page {c.page}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
