import { useRef } from "react";
import { MessageSquarePlus, PanelLeft, PanelRight, Plus } from "lucide-react";
import type { SourceOut } from "../../lib/types";
import { SourceTypeIcon } from "../../lib/sourceKind";
import { STUDIO_TOOLS, type StudioTool } from "../../features/studio/StudioPanel";

export const RAIL_ACCEPT =
  ".pdf,.docx,.pptx,.xlsx,.csv,.tsv,.md,.txt,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.webm,.mov,.png,.jpg,.jpeg,.webp,.gif";

const railBtn =
  "flex h-9 w-9 cursor-pointer items-center justify-center rounded-[8px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]";

/**
 * Slim left rail (NotebookLM-style): shown when the Sources sidebar is
 * hidden. Expand control + add-sources + one icon per source + new chat.
 * Clicking a source icon re-opens the sidebar on that source.
 */
export function SourcesRail({
  sources,
  selectedIds,
  onExpand,
  onFiles,
  onOpenSource,
  onNewChat,
}: {
  sources: SourceOut[];
  selectedIds: Set<string>;
  onExpand: () => void;
  onFiles: (files: File[]) => void;
  onOpenSource: (id: string) => void;
  onNewChat: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  return (
    <aside
      aria-label="Sources (collapsed)"
      className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-[var(--border)] bg-[var(--sidebar-bg)] py-2"
    >
      <button type="button" aria-label="Show sources" title="Show sources" onClick={onExpand} className={railBtn}>
        <PanelLeft size={17} />
      </button>
      <button
        type="button"
        aria-label="Add sources"
        title="Add sources"
        onClick={() => fileRef.current?.click()}
        className={railBtn}
      >
        <Plus size={17} />
      </button>
      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        accept={RAIL_ACCEPT}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) onFiles(files);
          e.target.value = "";
        }}
      />
      <div className="my-1 h-px w-8 bg-[var(--border)]" />
      <div className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto" role="list" aria-label="Sources">
        {sources.map((s) => {
          const busy = ["queued", "parsing", "embedding"].includes(s.status);
          const selected = selectedIds.has(s.id);
          return (
            <button
              key={s.id}
              type="button"
              role="listitem"
              title={`${s.filename}${s.status !== "ready" ? ` · ${s.status}` : ""}`}
              aria-label={`Open ${s.filename}`}
              onClick={() => onOpenSource(s.id)}
              className={`relative flex h-9 w-9 cursor-pointer items-center justify-center rounded-[8px] transition-colors hover:bg-[var(--surface-secondary)] ${
                selected ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "text-[var(--text-secondary)]"
              } ${busy ? "opacity-60" : ""}`}
            >
              <SourceTypeIcon type={s.source_type} size={17} />
              {s.status === "failed" && (
                <span className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-[var(--danger)]" />
              )}
              {(s.status === "queued" || s.status === "parsing" || s.status === "embedding") && (
                <span className="absolute top-1 right-1 h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--warning)]" />
              )}
            </button>
          );
        })}
        {sources.length === 0 && (
          <span className="px-1 text-center text-[10px] leading-tight text-[var(--text-muted)]">No sources</span>
        )}
      </div>
      <div className="my-1 h-px w-8 bg-[var(--border)]" />
      <button type="button" aria-label="New chat" title="New chat" onClick={onNewChat} className={railBtn}>
        <MessageSquarePlus size={17} />
      </button>
    </aside>
  );
}

/**
 * Slim right rail (NotebookLM-style): shown when the Studio sidebar is
 * hidden. Expand control + one icon per Studio tool. Clicking a tool
 * re-opens Studio with that tool's dialog.
 */
export function StudioRail({
  onExpand,
  onPickTool,
}: {
  onExpand: () => void;
  onPickTool: (tool: StudioTool) => void;
}) {
  return (
    <aside
      aria-label="Studio (collapsed)"
      className="flex w-14 shrink-0 flex-col items-center gap-1 border-l border-[var(--border)] bg-[var(--sidebar-bg)] py-2"
    >
      <button type="button" aria-label="Show studio" title="Show studio" onClick={onExpand} className={railBtn}>
        <PanelRight size={17} />
      </button>
      <div className="my-1 h-px w-8 bg-[var(--border)]" />
      <div className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto" role="list" aria-label="Studio tools">
        {STUDIO_TOOLS.filter((t) => t.ready).map((t) => (
          <button
            key={t.id}
            type="button"
            role="listitem"
            title={`${t.title} — ${t.desc}`}
            aria-label={`Open ${t.title} in Studio`}
            onClick={() => onPickTool(t.id)}
            className={`${railBtn}`}
          >
            <span className="flex items-center justify-center [&>svg]:h-[17px] [&>svg]:w-[17px]">{t.icon}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
