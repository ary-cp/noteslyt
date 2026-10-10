import type * as React from "react";
import { useMemo, useRef, useState } from "react";
import { Check, MessageSquarePlus, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import type { ChatOut, SourceOut } from "../../lib/types";
import { SourceTypeIcon, mediaKind, statusLabel, statusTone } from "../../lib/sourceKind";
import { timeAgo } from "../../lib/utils";
import { Badge, EmptyState, ProgressBar } from "../../components/ui/Feedback";
import { IconButton } from "../../components/ui/Button";

type Filter = "all" | "docs" | "video" | "audio" | "image";

function matchesFilter(s: SourceOut, f: Filter): boolean {
  const k = mediaKind(s.source_type);
  if (f === "all") return true;
  if (f === "docs") return k === "pdf" || k === "text" || k === "office";
  if (f === "video") return k === "video";
  if (f === "audio") return k === "audio";
  if (f === "image") return k === "image";
  return true;
}

export function SourcesPanel({
  sources,
  chats,
  activeChatId,
  selectedIds,
  query,
  debouncedQuery,
  onQuery,
  onToggleSelect,
  onSelectAll,
  onClear,
  onOpenSource,
  onRemoveSource,
  onFiles,
  onNewChat,
  onOpenChat,
  onChatMenu,
  onDeleteChat,
  onRefresh,
}: {
  sources: SourceOut[];
  chats: ChatOut[];
  activeChatId: string | null;
  selectedIds: Set<string>;
  query: string;
  debouncedQuery: string;
  onQuery: (q: string) => void;
  onToggleSelect: (id: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
  onOpenSource: (id: string) => void;
  onRemoveSource: (id: string) => void;
  onFiles: (files: File[]) => void;
  onNewChat: () => void;
  onOpenChat: (id: string) => void;
  onChatMenu: (e: React.MouseEvent, chat: ChatOut) => void;
  onDeleteChat: (chat: ChatOut) => void;
  onRefresh: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = debouncedQuery.trim().toLowerCase();
    return sources.filter((s) => matchesFilter(s, filter) && (!q || s.filename.toLowerCase().includes(q)));
  }, [sources, filter, debouncedQuery]);

  const processing = filtered.filter((s) => ["queued", "parsing", "embedding"].includes(s.status));
  const ready = filtered.filter((s) => s.status === "ready");
  const failed = filtered.filter((s) => s.status === "failed");

  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const files = Array.from(e.dataTransfer.files ?? []);
        if (files.length) onFiles(files);
      }}
    >
      {/* sources header */}
      <div className="shrink-0 border-b border-[var(--border)] px-3 pt-3 pb-2.5">
        <div className="flex items-center justify-between">
          <h2 className="text-[13px] font-semibold tracking-wide text-[var(--text-primary)]">Sources</h2>
          <div className="flex items-center gap-0.5">
            <IconButton label="Refresh sources" onClick={onRefresh}>
              <RefreshCw size={14} />
            </IconButton>
            <IconButton label="Add sources (Ctrl+U)" onClick={() => fileRef.current?.click()}>
              <Plus size={16} />
            </IconButton>
          </div>
        </div>
        <div className="relative mt-2">
          <Search size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-[var(--text-muted)]" />
          <input
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="Search sources…"
            aria-label="Search sources"
            className="h-8 w-full rounded-[7px] border border-[var(--border)] bg-[var(--input)] pr-2 pl-8 text-[13px] outline-none placeholder:text-[var(--text-muted)] focus:border-[var(--accent)]"
          />
        </div>
        <div className="mt-2 flex gap-1" role="tablist" aria-label="Filter sources">
          {(["all", "docs", "video", "audio", "image"] as Filter[]).map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={filter === f}
              onClick={() => setFilter(f)}
              className={`h-6 cursor-pointer rounded-full px-2 text-xs font-medium capitalize transition-colors ${
                filter === f ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              {f === "docs" ? "Docs" : f}
            </button>
          ))}
        </div>
        <div className="mt-2 flex items-center justify-between text-xs text-[var(--text-muted)]">
          <span>
            {sources.length} sources · {selectedIds.size} selected
          </span>
          <span className="flex gap-2">
            <button type="button" onClick={onSelectAll} className="cursor-pointer hover:text-[var(--text-primary)]">Select all</button>
            <button type="button" onClick={onClear} className="cursor-pointer hover:text-[var(--text-primary)]">Clear</button>
          </span>
        </div>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          accept=".pdf,.docx,.pptx,.xlsx,.csv,.tsv,.md,.txt,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.webm,.mov,.png,.jpg,.jpeg,.webp,.gif"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) onFiles(files);
            e.target.value = "";
          }}
        />
      </div>

      {/* source list */}
      <div className={`min-h-0 flex-1 overflow-y-auto px-2 py-2 ${dragOver ? "bg-[var(--accent-soft)]" : ""}`} aria-label="Source list">
        {dragOver && (
          <div className="mb-2 rounded-[8px] border border-dashed border-[var(--accent)] p-3 text-center text-[13px] text-[var(--accent)]">
            Drop files to upload
          </div>
        )}
        {filtered.length === 0 ? (
          <EmptyState
            title={sources.length === 0 ? "No sources yet" : "No matches"}
            body={sources.length === 0 ? "Upload PDFs, docs, video or audio. The AI only answers from what you add." : "Try a different search or filter."}
            action={
              sources.length === 0 ? (
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-[7px] bg-[var(--accent)] px-3 text-[13px] font-medium text-white hover:bg-[var(--accent-hover)]"
                >
                  <Plus size={14} /> Add sources
                </button>
              ) : undefined
            }
          />
        ) : (
          <>
            {processing.length > 0 && (
              <SourceSection title="Processing" count={processing.length} hint="Chat remains available with ready sources.">
                {processing.map((s) => <SourceRow key={s.id} source={s} selected={false} onToggle={() => undefined} onOpen={() => onOpenSource(s.id)} onRemove={() => onRemoveSource(s.id)} />)}
              </SourceSection>
            )}
            {ready.length > 0 && (
              <SourceSection title="Ready to chat" count={ready.length}>
                {ready.map((s) => <SourceRow key={s.id} source={s} selected={selectedIds.has(s.id)} onToggle={() => onToggleSelect(s.id)} onOpen={() => onOpenSource(s.id)} onRemove={() => onRemoveSource(s.id)} />)}
              </SourceSection>
            )}
            {failed.length > 0 && (
              <SourceSection title="Needs attention" count={failed.length}>
                {failed.map((s) => <SourceRow key={s.id} source={s} selected={false} onToggle={() => undefined} onOpen={() => onOpenSource(s.id)} onRemove={() => onRemoveSource(s.id)} />)}
              </SourceSection>
            )}
          </>
        )}
      </div>

      {/* chats */}
      <div className="shrink-0 border-t border-[var(--border)] px-3 py-2.5">
        <div className="mb-1.5 flex items-center justify-between">
          <h2 className="text-[13px] font-semibold tracking-wide">Chats</h2>
          <button
            type="button"
            onClick={onNewChat}
            className="inline-flex h-7 cursor-pointer items-center gap-1 rounded-[6px] px-2 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]"
          >
            <MessageSquarePlus size={13} /> New chat
          </button>
        </div>
        <div className="max-h-[180px] overflow-y-auto" role="list" aria-label="Chats">
          {chats.length === 0 && <p className="px-1 py-1 text-xs text-[var(--text-muted)]">No chats yet — start one above.</p>}
          {chats.map((c) => (
            <div
              key={c.id}
              role="listitem"
              onContextMenu={(e) => onChatMenu(e, c)}
              className={`group flex w-full items-center gap-1 rounded-[7px] pr-1 transition-colors ${
                c.id === activeChatId ? "bg-[var(--surface-secondary)] text-[var(--text-primary)]" : "text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]"
              }`}
            >
              <button type="button" onClick={() => onOpenChat(c.id)} className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 px-2 py-[7px] text-left text-[13px]">
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${c.id === activeChatId ? "bg-[var(--accent)]" : "bg-[var(--border-strong)]"}`} />
              <span className="min-w-0 flex-1 truncate">{c.title}</span>
              <span className="shrink-0 text-[11px] text-[var(--text-muted)]">{timeAgo(c.last_message_at)}</span>
              </button>
              <button type="button" aria-label={`Delete chat ${c.title}`} title="Delete chat" onClick={() => onDeleteChat(c)} className="cursor-pointer rounded p-1 text-[var(--text-muted)] opacity-0 hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] group-hover:opacity-100 focus:opacity-100">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function SourceSection({ title, count, hint, children }: { title: string; count: number; hint?: string; children: React.ReactNode }) {
  return (
    <section className="mb-3" aria-label={title}>
      <div className="flex items-center justify-between px-2 py-1.5">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">{title}</h3>
        <span className="text-[11px] text-[var(--text-muted)]">{count}</span>
      </div>
      {hint && <p className="px-2 pb-1 text-[11px] text-[var(--text-muted)]">{hint}</p>}
      {children}
    </section>
  );
}

function SourceRow({
  source,
  selected,
  onToggle,
  onOpen,
  onRemove,
}: {
  source: SourceOut;
  selected: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onRemove: () => void;
}) {
  const busy = ["queued", "parsing", "embedding"].includes(source.status);
  return (
    <div
      className="group mb-1 flex items-start gap-2.5 rounded-[8px] border border-transparent px-2.5 py-2 transition-colors hover:bg-[var(--surface-secondary)]"
    >
      <button
        type="button"
        role="checkbox"
        aria-checked={selected}
        aria-label={`Use ${source.filename} for retrieval`}
        onClick={onToggle}
        disabled={source.status !== "ready"}
        className={`mt-0.5 flex h-[18px] w-[18px] shrink-0 cursor-pointer items-center justify-center rounded-[5px] border text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-30 ${
          selected ? "border-[var(--accent)] bg-[var(--accent)] text-white" : "border-[var(--border-strong)]"
        }`}
      >
        {selected && <Check size={12} />}
      </button>
      <button type="button" onClick={onOpen} className="flex min-w-0 flex-1 cursor-pointer items-start gap-2 text-left">
        <span className="mt-0.5"><SourceTypeIcon type={source.source_type} /></span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-[var(--text-primary)]">{source.filename}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-[var(--text-muted)]">
            <Badge tone={statusTone(source.status)}>{statusLabel(source.status)}</Badge>
            {source.status === "ready" ? (
              <span>{source.chunk_count} {source.chunk_count === 1 ? "passage" : "passages"}{source.duration_s ? ` · ${Math.round(source.duration_s / 60)} min` : ""}</span>
            ) : busy ? (
              <span className="text-[var(--warning)]">Indexing…</span>
            ) : (
              <span className="truncate text-[var(--danger)]">{source.status_detail || "Failed"}</span>
            )}
          </span>
          {busy && <ProgressBar value={source.status === "queued" ? 15 : source.status === "parsing" ? 45 : 75} className="mt-1.5" />}
        </span>
      </button>
      <button
        type="button"
        aria-label={`Remove ${source.filename}`}
        data-tooltip="Remove from project"
        onClick={onRemove}
        className="tooltip-host shrink-0 cursor-pointer rounded-md p-1.5 text-[var(--text-muted)] opacity-0 group-hover:opacity-100 hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] focus:opacity-100"
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}
