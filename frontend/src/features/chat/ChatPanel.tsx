import { Suspense, lazy, memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { AlertTriangle, Check, Copy, FileText, Maximize2, Send, Square } from "lucide-react";
import { createChat, getChat, listChats, renameChat, streamMessage } from "../../lib/api";
import type { ChatCitation, ChatMessageOut, SourceOut } from "../../lib/types";
import { useToast } from "../../lib/toast";
import { ErrorState } from "../../components/ui/Feedback";
import { Dropdown } from "../../components/ui/Menu";
import { SourceTypeIcon } from "../../lib/sourceKind";
import { citationLabel } from "../../lib/utils";

const MermaidBlock = lazy(() => import("./MermaidBlock"));

/* ---------------- code block ---------------- */

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="overflow-hidden rounded-[8px] border border-[var(--border)]">
      <div className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--surface-secondary)] px-3 py-1.5">
        <span className="font-mono text-xs text-[var(--text-secondary)]">{language || "code"}</span>
        <button
          type="button"
          onClick={() => {
            navigator.clipboard.writeText(code).catch(() => undefined);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          }}
          className="inline-flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          {copied ? <><Check size={12} /> Copied</> : <><Copy size={12} /> Copy</>}
        </button>
      </div>
      <pre className="!rounded-none !border-0 overflow-x-auto bg-[var(--surface-secondary)] p-3 font-mono text-[13px] leading-6">
        <code>{code}</code>
      </pre>
    </div>
  );
}

/* ---------------- markdown with latex + mermaid + code ---------------- */

export const MarkdownBody = memo(function MarkdownBody({
  text,
  citations = [],
  sources = [],
  onOpenCitation,
}: {
  text: string;
  citations?: ChatCitation[];
  sources?: SourceOut[];
  onOpenCitation?: (sourceId: string | null) => void;
}) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          code(props: any) {
            const { className, children } = props;
            const match = /language-(\w+)/.exec(className || "");
            const lang = match?.[1] ?? "";
            const raw = String(children ?? "").replace(/\n$/, "");
            if (lang === "mermaid") {
              return (
                <Suspense fallback={<div className="rounded border border-[var(--border)] p-3 text-[13px] text-[var(--text-muted)]">Rendering diagram…</div>}>
                  <MermaidBlock code={raw} />
                </Suspense>
              );
            }
            if (className || raw.includes("\n")) {
              return <CodeBlock language={lang} code={raw} />;
            }
            return <code className={className}>{children}</code>;
          },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          pre(props: any) {
            return <>{props.children}</>;
          },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          a(props: any) {
            const { href, children } = props;
            const ref = /^#cite-ref=(\d+)$/.exec(href ?? "");
            const index = ref ? Number(ref[1]) - 1 : -1;
            const citation = citations[index];
            if (citation && onOpenCitation) {
              const sourceName = sources.find((source) => source.id === citation.source_id)?.filename ?? "source";
              return (
                <button
                  type="button"
                  className="cite"
                  title={`${sourceName} · ${citationLabel(citation.locator_kind, citation.locator_value)}`}
                  onClick={() => onOpenCitation(citation.source_id)}
                >
                  {children}
                </button>
              );
            }
            return <a href={href}>{children}</a>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

/* ---------------- message ---------------- */

function CitationChips({ citations, sources, onOpen }: { citations: ChatCitation[]; sources: SourceOut[]; onOpen: (sourceId: string | null) => void }) {
  if (!citations.length) return null;
  return (
    <div className="mt-3 border-t border-[var(--border)] pt-2.5">
      <p className="mb-1.5 text-xs font-medium text-[var(--text-muted)]">Sources</p>
      <div className="grid gap-1">
        {citations.map((c, i) => (
          <button
            key={`${c.source_id}-${i}`}
            type="button"
            onClick={() => onOpen(c.source_id)}
            className="flex cursor-pointer items-center gap-2 rounded-[7px] border border-[var(--border)] bg-[var(--surface-secondary)] px-2.5 py-[7px] text-left text-[13px] transition-colors hover:border-[var(--border-strong)]"
          >
            <span className="shrink-0 font-mono text-[12px] font-semibold text-[var(--accent)]">[^{i + 1}]</span>
            <span className="min-w-0 flex-1 truncate">
              {sources.find((source) => source.id === c.source_id)?.filename ?? c.heading ?? "Source"}
              <span className="text-[var(--text-muted)]"> · {citationLabel(c.locator_kind, c.locator_value)}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function ChatMessage({ msg, sources, onOpenSource }: { msg: ChatMessageOut; sources: SourceOut[]; onOpenSource: (id: string | null) => void }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-[10px] rounded-br-[4px] border border-[var(--border)] bg-[var(--surface-secondary)] px-3.5 py-2.5 text-[15px] whitespace-pre-wrap">
          {msg.content}
        </div>
      </div>
    );
  }
  const linkedAnswer = msg.content.replace(/\[(\d{1,3})\]/g, "[$1](#cite-ref=$1)");

  return (
    <div className="min-w-0">
      {msg.grounded === false && (
        <p className="mb-2 inline-flex items-center gap-1.5 rounded-[7px] border border-[var(--warning)]/30 bg-[var(--warning-soft)] px-2.5 py-1 text-[13px] text-[var(--warning)]">
          <AlertTriangle size={13} /> Not in your sources — answered from general knowledge.
        </p>
      )}
      <MarkdownBody text={linkedAnswer} citations={msg.citations} sources={sources} onOpenCitation={onOpenSource} />
      <CitationChips citations={msg.citations} sources={sources} onOpen={onOpenSource} />
    </div>
  );
}

/* ---------------- chat panel ---------------- */

const SUGGESTIONS = [
  "Explain the main concepts",
  "Summarize these lectures",
  "Create an exam from my sources",
  "Compare the key ideas",
  "Teach me this topic step by step",
];

export function ChatPanel({
  projectId,
  chatId,
  sources,
  selectedSources,
  selectedSourceIds,
  selectedCount,
  totalCount,
  onChatCreated,
  onOpenSource,
  onNeedSources,
}: {
  projectId: string;
  chatId: string | null;
  sources: SourceOut[];
  selectedSources: SourceOut[];
  selectedSourceIds: string[];
  selectedCount: number;
  totalCount: number;
  onChatCreated: (id: string) => void;
  onOpenSource: (id: string | null) => void;
  onNeedSources: () => void;
}) {
  const { push } = useToast();
  const [messages, setMessages] = useState<ChatMessageOut[]>([]);
  const [title, setTitle] = useState("New chat");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [model, setModel] = useState<"default" | "precise" | "explanatory">("default");
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef(false);

  const hasReady = sources.some((s) => s.status === "ready");

  useEffect(() => {
    if (!chatId) {
      setMessages([]);
      setTitle("New chat");
      setError("");
      return;
    }
    setLoading(true);
    setError("");
    getChat(chatId)
      .then((d) => {
        setMessages(d.messages);
        setTitle(d.chat.title);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [chatId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || sending) return;
    if (!hasReady) {
      push("Add a source first", { detail: "The AI answers from your project files.", tone: "warning" });
      onNeedSources();
      return;
    }
    if (!selectedSourceIds.length) {
      push("Select a source", { detail: "Choose at least one ready source for this chat.", tone: "warning" });
      onNeedSources();
      return;
    }
    let id = chatId;
    if (!id) {
      try {
        const c = await createChat(projectId);
        id = c.id;
        onChatCreated(c.id);
      } catch (e) {
        push("Could not start chat", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
        return;
      }
    }
    const optimistic: ChatMessageOut = {
      id: `local-${Date.now()}`,
      role: "user",
      content,
      citations: [],
      grounded: null,
      created_at: new Date().toISOString(),
    };
    const streamingId = `stream-${Date.now()}`;
    const streaming: ChatMessageOut = {
      id: streamingId,
      role: "assistant",
      content: "",
      citations: [],
      grounded: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic, streaming]);
    setDraft("");
    setSending(true);
    abortRef.current = false;
    try {
      const res = await streamMessage(id, content, (token) => {
        if (!abortRef.current) {
          setMessages((prev) => prev.map((m) => m.id === streamingId ? { ...m, content: m.content + token } : m));
        }
      }, model, selectedSourceIds);
      if (abortRef.current) return;
      setMessages((prev) => prev.map((m) => m.id === streamingId
        ? { ...m, content: res.answer, citations: res.citations, grounded: res.grounded }
        : m));
      if (res.chat_title) setTitle(res.chat_title);
      // refresh chat list titles elsewhere via polling parent? best-effort:
      listChats(projectId).catch(() => undefined);
    } catch (e) {
      if (!abortRef.current) push("Generation failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
      setMessages((prev) => prev.filter((m) => m.id !== optimistic.id && m.id !== streamingId));
      setDraft(content);
    } finally {
      setSending(false);
    }
  };

  const rename = async () => {
    if (!chatId) return;
    setRenameOpen(true);
    setRenameValue(title);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* chat header */}
      <div className="flex h-[46px] shrink-0 items-center gap-2 border-b border-[var(--border)] px-4">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{title}</h2>
        <span className="hidden text-xs text-[var(--text-muted)] sm:inline">
          Using {selectedCount} of {totalCount} sources
        </span>
        <button type="button" onClick={() => void rename()} className="cursor-pointer rounded-md px-2 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]">
          Rename
        </button>
      </div>

      {/* messages */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-5" aria-live="polite">
        <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6">
          {loading ? (
            <div className="grid gap-4">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-20 animate-pulse rounded-[8px] bg-[var(--surface-secondary)]" />
              ))}
            </div>
          ) : error ? (
            <ErrorState body={error} onRetry={() => window.location.reload()} />
          ) : messages.length === 0 && !sending ? (
            <div className="flex flex-col items-center pt-10 text-center">
              <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-[10px] border border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)]">
                <FileText size={19} />
              </span>
              <h3 className="text-[19px] font-semibold tracking-tight">What do you want to learn?</h3>
              <p className="mt-1.5 max-w-[420px] text-sm text-[var(--text-secondary)]">
                Ask questions about your sources. Answers cite the exact page or timestamp.
              </p>
              <div className="mt-5 flex max-w-[520px] flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void send(s)}
                    className="cursor-pointer rounded-full border border-[var(--border)] bg-[var(--surface)] px-3.5 py-1.5 text-[13px] text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
                  >
                    {s}
                  </button>
                ))}
              </div>
              {!hasReady && (
                <button type="button" onClick={onNeedSources} className="mt-6 cursor-pointer text-[13px] text-[var(--accent)] underline underline-offset-2">
                  Upload a source to begin
                </button>
              )}
            </div>
          ) : (
            messages.map((m) => <ChatMessage key={m.id} msg={m} sources={sources} onOpenSource={onOpenSource} />)
          )}
          {sending && (
            <div className="flex items-center gap-2 text-[var(--text-muted)]" aria-label="Generating">
              <span className="dot" /><span className="dot" /><span className="dot" />
              <span className="ml-1 text-[13px]">
                {selectedSources.length > 0 ? `Reading ${selectedSources.length} source${selectedSources.length > 1 ? "s" : ""}…` : "Thinking…"}
              </span>
              <button type="button" onClick={() => { abortRef.current = true; setSending(false); }} className="ml-2 inline-flex cursor-pointer items-center gap-1 rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--surface-secondary)]">
                <Square size={11} /> Stop
              </button>
            </div>
          )}
        </div>
      </div>

      {/* selected sources strip */}
      {selectedSources.length > 0 && (
        <div className="shrink-0 border-t border-[var(--border)] px-4 py-1.5">
          <div className="mx-auto flex max-w-[760px] items-center gap-1.5 overflow-x-auto">
            {selectedSources.slice(0, 6).map((s) => (
              <span key={s.id} className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
                <SourceTypeIcon type={s.source_type} size={12} />
                <span className="max-w-[140px] truncate">{s.filename}</span>
              </span>
            ))}
            {selectedSources.length > 6 && <span className="text-xs text-[var(--text-muted)]">+{selectedSources.length - 6}</span>}
          </div>
        </div>
      )}

      {/* input */}
      <div className="shrink-0 px-4 pt-2 pb-4">
        <div className="mx-auto max-w-[760px] rounded-[10px] border border-[var(--border)] bg-[var(--input)] transition-colors focus-within:border-[var(--accent)]">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(draft);
              }
            }}
            placeholder={hasReady ? "Ask anything about your sources…" : "Upload a source to start asking…"}
            aria-label="Ask anything about your sources"
            rows={2}
            className="max-h-[140px] w-full resize-none bg-transparent px-3.5 pt-3 text-[15px] outline-none placeholder:text-[var(--text-muted)]"
          />
          <div className="flex items-center gap-2 px-2.5 pb-2.5">
            <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
              Response style
              <Dropdown
                value={model}
                onChange={(value) => setModel(value as typeof model)}
                ariaLabel="Response style"
                className="chat-style-menu w-[132px]"
                options={[
                  { value: "default", label: "Balanced", hint: "Clear answer with useful context" },
                  { value: "precise", label: "Precise", hint: "Direct answer, minimal detail" },
                  { value: "explanatory", label: "Explanatory", hint: "Step-by-step, learner-friendly" },
                ]}
              />
            </label>
            <span className="ml-auto text-xs text-[var(--text-muted)]">Shift+Enter for a new line</span>
            <button
              type="button"
              onClick={() => void send(draft)}
              disabled={!draft.trim() || sending}
              aria-label="Send message"
              className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-[7px] bg-[var(--accent)] text-white transition-opacity hover:bg-[var(--accent-hover)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={15} />
            </button>
          </div>
        </div>
      </div>

      {renameOpen && (
        <div className="anim-fade fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" style={{ zIndex: 60 }} onMouseDown={(e) => e.target === e.currentTarget && setRenameOpen(false)}>
          <div role="dialog" aria-modal="true" aria-label="Rename chat" className="anim-modal w-full max-w-[420px] rounded-[10px] border border-[var(--border)] bg-[var(--modal)] p-5" style={{ boxShadow: "var(--shadow-modal)" }}>
            <h2 className="text-[15px] font-semibold">Rename chat</h2>
            <input
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setRenameOpen(false);
                if (e.key === "Enter" && renameValue.trim() && chatId) {
                  renameChat(chatId, renameValue.trim())
                    .then(() => {
                      setTitle(renameValue.trim());
                      setRenameOpen(false);
                      push("Chat renamed", { tone: "success" });
                    })
                    .catch((err: unknown) => push("Rename failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" }));
                }
              }}
              className="mt-3 h-9 w-full rounded-[7px] border border-[var(--border)] bg-[var(--input)] px-2.5 text-sm outline-none focus:border-[var(--accent)]"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setRenameOpen(false)} className="h-8 cursor-pointer rounded-[7px] border border-[var(--border)] px-3 text-[13px] font-medium hover:bg-[var(--surface-secondary)]">
                Cancel
              </button>
              <button
                type="button"
                disabled={!renameValue.trim()}
                onClick={() => {
                  if (!chatId) return;
                  renameChat(chatId, renameValue.trim())
                    .then(() => {
                      setTitle(renameValue.trim());
                      setRenameOpen(false);
                      push("Chat renamed", { tone: "success" });
                    })
                    .catch((err: unknown) => push("Rename failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" }));
                }}
                className="h-8 cursor-pointer rounded-[7px] bg-[var(--accent)] px-3 text-[13px] font-medium text-white hover:bg-[var(--accent-hover)] disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function ExpandIcon() {
  return <Maximize2 size={12} />;
}
