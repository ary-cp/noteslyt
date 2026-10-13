import { Suspense, lazy, memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { AlertTriangle, Check, Copy, Maximize2, Pencil, RefreshCw, Send, Square } from "lucide-react";
import { ChatStreamError, createChat, getChat, renameChat, streamMessage } from "../../lib/api";
import type { ChatCitation, ChatMessageOut, CitationLocator, SourceOut } from "../../lib/types";
import { useToast } from "../../lib/toast";
import { ErrorState } from "../../components/ui/Feedback";
import { Dropdown } from "../../components/ui/Menu";
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
  onOpenCitation?: (sourceId: string | null, locator?: CitationLocator) => void;
}) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeRaw, rehypeKatex]}
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
                  onClick={() => onOpenCitation(citation.source_id, { kind: citation.locator_kind, value: citation.locator_value, end: citation.end_ts, snippet: citation.snippet })}
                >
                  {children}
                </button>
              );
            }
            return <a href={href}>{children}</a>;
          },
        }}
      >
        {text.split(/(```[\s\S]*?```|`[^`\n]*`)/g).map((part, i) => i % 2 ? part : part
          .replace(/\\\[([\s\S]*?)\\\]/g, (_, math: string) => `\n$$\n${math}\n$$\n`)
          .replace(/\\\(([\s\S]*?)\\\)/g, (_, math: string) => `$${math}$`)).join("")}
      </ReactMarkdown>
    </div>
  );
});

/* ---------------- message ---------------- */

/** Expand grouped references ("[1, 2, 4]", "[1-3]") into one link per number. */
function linkifyMultiCitations(text: string): string {
  return text.replace(/\[(\d[\d\s,;–\-]*?)\]/g, (m, group: string) => {
    if (/^\s*\d{1,3}\s*$/.test(group)) {
      const n = group.trim();
      return `[${n}](#cite-ref=${n})`;
    }
    // Timestamps and locator labels are not reference lists — leave alone.
    if (/[A-Za-z:]/.test(group)) return m;
    const nums: string[] = [];
    for (const part of group.split(/[,;]/)) {
      const trimmed = part.trim();
      if (!trimmed) continue;
      const range = /^(\d{1,3})\s*[–\-]\s*(\d{1,3})$/.exec(trimmed);
      if (range) {
        const lo = Math.min(Number(range[1]), Number(range[2]));
        const hi = Math.max(Number(range[1]), Number(range[2]));
        if (hi - lo > 20) return m;
        for (let n = lo; n <= hi; n++) nums.push(String(n));
      } else if (/^\d{1,3}$/.test(trimmed)) {
        nums.push(trimmed);
      } else {
        return m;
      }
    }
    if (!nums.length) return m;
    return nums.map((n) => `[${n}](#cite-ref=${n})`).join(", ");
  });
}

const ChatMessage = memo(function ChatMessage({ msg, sources, onOpenSource, onEdit }: { msg: ChatMessageOut; sources: SourceOut[]; onOpenSource: (id: string | null, locator?: CitationLocator) => void; onEdit?: (content: string) => void }) {
  if (msg.role === "user") {
    const editable = onEdit && !msg.id.startsWith("local-") && !msg.id.startsWith("stream-");
    return (
      <div className="group/q flex flex-col items-end gap-1">
        <div className="max-w-[85%] rounded-[10px] rounded-br-[4px] border border-[var(--border)] bg-[var(--surface-secondary)] px-3.5 py-2.5 text-[15px] whitespace-pre-wrap">
          {msg.content}
        </div>
        {editable && (
          <button
            type="button"
            onClick={() => onEdit(msg.content)}
            aria-label="Edit question"
            title="Edit and re-ask in this chat"
            className="inline-flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-1 text-xs text-[var(--text-muted)] opacity-0 transition-opacity group-hover/q:opacity-100 hover:text-[var(--text-primary)] focus:opacity-100 focus-visible:opacity-100"
          >
            <Pencil size={13} /> Edit
          </button>
        )}
      </div>
    );
  }
  // Models sometimes group references ("[1, 2, 4]", "[1-3]"); split them so
  // every number becomes its own clickable citation. Stored answers written
  // before the server-side audit learned this still carry the grouped form.
  const linkedAnswer = linkifyMultiCitations(msg.content);

  return (
    <div className="group/msg min-w-0">
      {msg.grounded === false && /\*\*Outside your sources:\*\*/i.test(msg.content) && (
        <p className="mb-2 inline-flex items-center gap-1.5 rounded-[7px] border border-[var(--warning)]/30 bg-[var(--warning-soft)] px-2.5 py-1 text-[13px] text-[var(--warning)]">
          <AlertTriangle size={13} /> Includes information outside your sources.
        </p>
      )}
      <MarkdownBody text={linkedAnswer} citations={msg.citations} sources={sources} onOpenCitation={onOpenSource} />
      <MessageActions content={msg.content} />
    </div>
  );
});

function MessageActions({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-1.5 flex items-center gap-1">
      <button
        type="button"
        onClick={() => {
          navigator.clipboard.writeText(content).catch(() => undefined);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }}
        aria-label="Copy response"
        title="Copy response"
        className="inline-flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-1 text-xs text-[var(--text-muted)] opacity-0 transition-opacity group-hover/msg:opacity-100 hover:text-[var(--text-primary)] focus:opacity-100 focus-visible:opacity-100"
      >
        {copied ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy response</>}
      </button>
    </div>
  );
}

/* ---------------- chat panel ---------------- */

const SUGGESTIONS = [
  { label: "Summarize", prompt: "Summarize the main ideas from my sources" },
  { label: "Explain a concept", prompt: "Explain the main concepts step by step" },
  { label: "Quiz me", prompt: "Quiz me on the key concepts from my sources" },
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
  onChatUpdated,
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
  onChatUpdated: () => void;
  onOpenSource: (id: string | null, locator?: CitationLocator) => void;
  onNeedSources: () => void;
}) {
  const { push } = useToast();
  const [messages, setMessages] = useState<ChatMessageOut[]>([]);
  const [title, setTitle] = useState("New chat");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [sendError, setSendError] = useState("");
  const [failedQuestion, setFailedQuestion] = useState("");
  const [draft, setDraft] = useState("");
  const [model, setModel] = useState<"default" | "precise" | "explanatory">("default");
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const loadedChatRef = useRef<string | null>(null);
  const createdChatRef = useRef<string | null>(null);
  const nearBottom = useRef(true);
  const [refreshTick, setRefreshTick] = useState(0);
  useEffect(() => () => controllerRef.current?.abort(), []);

  const hasReady = sources.some((s) => s.status === "ready");

  useEffect(() => {
    if (createdChatRef.current === chatId && chatId) { createdChatRef.current = null; loadedChatRef.current = chatId; return; }
    controllerRef.current?.abort();
    controllerRef.current = null;
    setSending(false);
    let live = true;
    if (loadedChatRef.current !== chatId) {
      setMessages([]); nearBottom.current = true; loadedChatRef.current = chatId;
    }
    if (!chatId) {
      setMessages([]); setTitle("New chat"); setError(""); setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    setSendError("");
    getChat(chatId)
      .then((d) => d.total > d.messages.length ? getChat(chatId, 200, Math.max(0, d.total - 200)) : d)
      .then((d) => { if (live) { setMessages(d.messages); setTitle(d.chat.title); } })
      .catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [chatId, refreshTick]);

  useEffect(() => {
    if (nearBottom.current) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending, loading]);

  useEffect(() => {
    const refresh = () => {
      if (!controllerRef.current && !loading && chatId) setRefreshTick((n) => n + 1);
    };
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [chatId, loading]);

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || controllerRef.current || loading) return;
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
    setSendError("");
    setFailedQuestion("");
    const controller = new AbortController();
    controllerRef.current = controller;
    setSending(true);
    nearBottom.current = true;
    let id = chatId;
    if (!id) {
      try {
        const c = await createChat(projectId);
        if (controller.signal.aborted) return;
        id = c.id;
        createdChatRef.current = c.id;
        onChatCreated(c.id);
      } catch (e) {
        if (controller.signal.aborted) return;
        controllerRef.current = null;
        setSending(false);
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

    let buffered = "";
    let frame: number | null = null;
    const flush = () => {
      frame = null;
      const text = buffered; buffered = "";
      if (text && !controller.signal.aborted) setMessages((prev) => prev.map((m) => m.id === streamingId ? { ...m, content: m.content + text } : m));
    };
    try {
      const res = await streamMessage(id, content, (token) => {
        buffered += token;
        if (frame === null) frame = window.requestAnimationFrame(flush);
      }, model, selectedSourceIds, controller.signal);
      if (frame !== null) window.cancelAnimationFrame(frame);
      flush();
      if (controller.signal.aborted) return;
      setMessages((prev) => prev.map((m) => m.id === streamingId
        ? { ...m, content: res.answer, citations: res.citations, grounded: res.grounded }
        : m));
      if (res.chat_title) setTitle(res.chat_title);
      onChatUpdated();
    } catch (e) {
      if (controller.signal.aborted) return;
      // A transport error can follow a successfully persisted answer. Reconcile
      // by the new user turn, never remove already-visible streamed text.
      for (let attempt = 0; attempt < (e instanceof ChatStreamError ? 1 : 10) && !controller.signal.aborted; attempt++) {
        try {
          const first = await getChat(id);
          const saved = first.total > first.messages.length ? await getChat(id, 200, Math.max(0, first.total - 200)) : first;
          if (controller.signal.aborted) return;
          const match = [...saved.messages].reverse().find((m) => m.role === "user" && m.content === content && new Date(m.created_at).getTime() >= new Date(optimistic.created_at).getTime() - 2000);
          const userIndex = match ? saved.messages.indexOf(match) : -1;
          if (userIndex >= 0 && saved.messages.slice(userIndex + 1).some((m) => m.role === "assistant")) {
            setMessages(saved.messages); setTitle(saved.chat.title); onChatUpdated(); return;
          }
        } catch { /* The next attempt can recover a transient network failure. */ }
        if (!(e instanceof ChatStreamError) && attempt < 9 && !controller.signal.aborted) await new Promise<void>((resolve) => {
          const finish = () => { clearTimeout(timer); controller.signal.removeEventListener("abort", finish); resolve(); };
          const timer = window.setTimeout(finish, 2000);
          controller.signal.addEventListener("abort", finish, { once: true });
        });
      }
      if (controller.signal.aborted) return;
      setSendError(e instanceof Error ? e.message : "The response could not be completed.");
      setFailedQuestion(content);
      setMessages((prev) => prev.filter((m) => m.id !== streamingId || m.content.trim()));
    } finally {
      if (frame !== null) window.cancelAnimationFrame(frame);
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setSending(false);
      }
    }
  };

  const rename = async () => {
    if (!chatId) return;
    setRenameOpen(true);
    setRenameValue(title);
  };

  // Load a sent question back into the composer so it can be edited and
  // re-asked in the same chat as a new turn (history is append-only).
  const editQuestion = (content: string) => {
    setDraft(content);
    setSendError("");
    nearBottom.current = true;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    window.setTimeout(() => composerRef.current?.focus(), 0);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* chat header */}
      <div className="flex h-[46px] shrink-0 items-center gap-2 border-b border-[var(--border)] px-4">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{title}</h2>
        <span className="hidden text-xs text-[var(--text-muted)] sm:inline">
          Using {selectedCount} of {totalCount} sources
        </span>
        <button type="button" aria-label="Refresh conversation" title="Fetch latest messages and chats"
          disabled={sending || loading} onClick={() => { setRefreshTick((n) => n + 1); onChatUpdated(); }}
          className="rounded-md p-2 text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] disabled:opacity-40">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
        <button type="button" onClick={() => void rename()} className="cursor-pointer rounded-md px-2 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]">
          Rename
        </button>
      </div>

      {/* messages */}
      <div ref={scrollRef} onScroll={() => {
        const el = scrollRef.current;
        if (el) nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
      }} className="min-h-0 flex-1 overflow-y-auto px-4 py-5" aria-live="polite">
        <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6">
          {loading && messages.length === 0 ? (
            <div className="grid gap-4">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-20 animate-pulse rounded-[8px] bg-[var(--surface-secondary)]" />
              ))}
            </div>
          ) : error ? (
            <ErrorState body={error} onRetry={() => setRefreshTick((n) => n + 1)} />
          ) : messages.length === 0 && !sending ? (
            <div className="mx-auto flex w-full max-w-[440px] flex-col items-center px-4 py-12 text-center">
              <h3 className="text-xl font-medium tracking-tight">{hasReady ? "Explore your sources" : "Start with a source"}</h3>
              <p className="mt-2 max-w-[340px] text-sm leading-relaxed text-[var(--text-muted)]">
                {hasReady ? "Ask a question. Get answers linked to your sources." : "Add a document, recording, or image to start learning."}
              </p>
              {hasReady ? <div className="mt-6 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => <button key={s.label} type="button" onClick={() => void send(s.prompt)}
                  className="cursor-pointer rounded-lg border border-[var(--border)] px-3 py-2 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]">
                  {s.label}
                </button>)}
              </div> : <button type="button" onClick={onNeedSources}
                className="mt-6 cursor-pointer rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90">
                Add a source
              </button>}
            </div>
          ) : (
            messages.map((m) => <ChatMessage key={m.id} msg={m} sources={sources} onOpenSource={onOpenSource} onEdit={editQuestion} />)
          )}
          {!sending && (sendError || (!loading && messages.at(-1)?.role === "user")) && (
            <div role="alert" className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-3 text-sm">
              <p className="font-medium">Reply could not be completed</p>
              <p className="mt-1 whitespace-pre-wrap break-words text-[var(--text-secondary)]">{sendError || "No saved reply was found for the last question. Refresh to check again, or put the question back in the composer to resend."}</p>
              <button type="button" className="mt-2 rounded border border-[var(--border-strong)] px-2 py-1" onClick={() => setDraft(failedQuestion || messages.at(-1)?.content || "")}>Put question back in composer</button>
            </div>
          )}
          {sending && (
            <div className="flex items-center gap-2 text-[var(--text-muted)]" aria-label="Generating">
              <span className="dot" /><span className="dot" /><span className="dot" />
              <span className="ml-1 text-[13px]">
                {selectedSources.length > 0 ? `Reading ${selectedSources.length} source${selectedSources.length > 1 ? "s" : ""}…` : "Thinking…"}
              </span>

            </div>
          )}
        </div>
      </div>

      {/* input */}
      <div className="shrink-0 px-4 pt-2 pb-4">
        <div className="chat-composer mx-auto max-w-[760px] rounded-[12px] border border-[var(--border)] bg-[var(--input)] transition-colors">
          <textarea
            ref={composerRef}
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
            className="max-h-[140px] w-full resize-none bg-transparent px-3.5 pt-3 text-[15px] outline-none focus-visible:outline-none placeholder:text-[var(--text-muted)]"
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
            <span className="ml-auto hidden text-xs text-[var(--text-muted)] sm:block">Shift+Enter for a new line</span>
            <button
              type="button"
              onClick={() => {
                if (sending) {
                  controllerRef.current?.abort(); controllerRef.current = null; setSending(false);
                  push("Response stopped", { detail: "You can refresh to check saved messages.", tone: "info" });
                } else void send(draft);
              }}
              disabled={!sending && (!draft.trim() || loading)}
              aria-label={sending ? "Stop response" : "Send message"}
              title={sending ? "Stop response" : "Send message"}
              className="ml-auto inline-flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-[7px] bg-[var(--accent)] text-white transition-opacity hover:bg-[var(--accent-hover)] disabled:cursor-not-allowed disabled:opacity-40 sm:ml-0"
            >
              {sending ? <Square size={15} fill="currentColor" /> : <Send size={15} />}
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
                      onChatUpdated();
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
                      onChatUpdated();
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

