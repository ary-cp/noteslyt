/**
 * Left pane, chat mode: scrolling transcript + composer.
 * Empty state offers three starter prompts derived from the document's topics
 * so a cold demo has something to click within two seconds.
 */

import { useEffect, useMemo, useRef } from "react";
import type { DocumentInfo, Message } from "../lib/types";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";

interface Props {
  doc: DocumentInfo;
  messages: Message[];
  busy: boolean;
  onSend: (text: string) => void;
  onCite: (page: number) => void;
}

export function ChatPane({ doc, messages, busy, onSend, onCite }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Keep the newest message in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const starters = useMemo(() => {
    const topics = doc.topics.slice(0, 2).map((t) => `Explain ${t.name.toLowerCase()}`);
    return ["Summarise the key ideas of this material", ...topics, "What is covered on page 1?"].slice(0, 4);
  }, [doc.topics]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
        {messages.length === 0 ? (
          <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center text-center">
            <h2 className="text-lg font-semibold tracking-tight">Ask anything about these slides</h2>
            <p className="mt-1.5 text-sm text-ink-muted">
              Every answer is grounded in the PDF on the right and cites the exact page.
            </p>
            <div className="mt-6 grid w-full gap-2">
              {starters.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => onSend(s)}
                  className="rounded-xl border border-line bg-surface px-4 py-2.5 text-left text-sm text-ink transition-colors hover:bg-surface-muted"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto flex max-w-2xl flex-col gap-6">
            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} onCite={onCite} />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>
      <Composer disabled={busy} onSend={onSend} />
    </div>
  );
}
