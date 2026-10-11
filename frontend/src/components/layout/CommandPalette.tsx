import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { ArrowRight, FileText, Folder, Hash, MessageSquare, Search, Sparkles } from "lucide-react";
import { listChats, listProjects, listSources } from "../../lib/api";
import type { ChatOut, ProjectListItem, SourceOut } from "../../lib/types";
import { useDebounced } from "../../lib/utils";

interface Props {
  open: boolean;
  onClose: () => void;
  onAction: (kind: "new-project" | "new-chat" | "upload" | "toggle-theme" | "settings", projectId?: string) => void;
  projectId?: string;
  onOpenCreateProject: () => void;
}

interface Item {
  kind: "project" | "source" | "chat" | "artifact" | "command";
  id: string;
  title: string;
  sub: string;
  projectId?: string;
}

const COMMANDS: { id: string; title: string; sub: string }[] = [
  { id: "new-project", title: "New project", sub: "Create a project" },
  { id: "new-chat", title: "New chat", sub: "Start a conversation here" },
  { id: "upload", title: "Upload source", sub: "Add files to this project" },
  { id: "toggle-theme", title: "Toggle theme", sub: "Dark / light" },
  { id: "settings", title: "Settings", sub: "Appearance, chat, profile" },
];

export function CommandPalette({ open, onClose, onAction, projectId, onOpenCreateProject }: Props) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [sources, setSources] = useState<SourceOut[]>([]);
  const [chats, setChats] = useState<ChatOut[]>([]);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounced = useDebounced(query, 150);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setActive(0);
      return;
    }
    inputRef.current?.focus();
    listProjects().then(setProjects).catch(() => undefined);
    if (projectId) {
      listSources(projectId).then(setSources).catch(() => undefined);
      listChats(projectId).then(setChats).catch(() => undefined);
    }
  }, [open, projectId]);

  const items: Item[] = useMemo(() => {
    const q = debounced.trim().toLowerCase();
    const match = (s: string) => !q || s.toLowerCase().includes(q);
    const cmds: Item[] = COMMANDS.filter((c) => match(c.title)).map((c) => ({ kind: "command", id: c.id, title: c.title, sub: c.sub }));
    const projs: Item[] = projects.filter((p) => match(p.title)).slice(0, 5).map((p) => ({ kind: "project", id: p.id, title: p.title, sub: `${p.stats.sources} sources`, projectId: p.id }));
    const srcs: Item[] = sources.filter((s) => match(s.filename)).slice(0, 5).map((s) => ({ kind: "source", id: s.id, title: s.filename, sub: s.source_type, projectId }));
    const chs: Item[] = chats.filter((c) => match(c.title)).slice(0, 5).map((c) => ({ kind: "chat", id: c.id, title: c.title, sub: "chat", projectId }));
    let arts: Item[] = [];
    try {
      const raw = localStorage.getItem(`noteslyt:artifacts:${projectId ?? ""}`);
      if (raw && q) {
        arts = (JSON.parse(raw) as { id: string; title: string }[])
          .filter((a) => a.title.toLowerCase().includes(q))
          .slice(0, 4)
          .map((a) => ({ kind: "artifact", id: a.id, title: a.title, sub: "artifact", projectId }));
      }
    } catch {
      /* ignore */
    }
    return [...cmds, ...projs, ...srcs, ...chs, ...arts].slice(0, 24);
  }, [debounced, projects, sources, chats, projectId]);

  useEffect(() => setActive(0), [items.length]);

  const run = (it: Item) => {
    onClose();
    if (it.kind === "command") {
      if (it.id === "new-project") onOpenCreateProject();
      else onAction(it.id as "new-chat" | "upload" | "toggle-theme" | "settings", projectId);
      return;
    }
    if (it.kind === "project") navigate(`/project/${it.id}`);
    else if (it.kind === "chat" && it.projectId) navigate(`/project/${it.projectId}/chat/${it.id}`);
    else if (it.kind === "source" && it.projectId) navigate(`/project/${it.projectId}/source/${it.id}`);
  };

  if (!open) return null;

  const iconFor = (kind: Item["kind"]) =>
    kind === "project" ? <Folder size={14} /> : kind === "source" ? <FileText size={14} /> : kind === "chat" ? <MessageSquare size={14} /> : kind === "artifact" ? <Sparkles size={14} /> : <Hash size={14} />;

  return createPortal(
    <div className="anim-fade fixed inset-0 z-[75] flex items-start justify-center bg-black/55 p-4 pt-[12vh]" style={{ zIndex: 75 }} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="anim-modal w-full max-w-[560px] overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface-raised)]" style={{ boxShadow: "var(--shadow-modal)" }} role="dialog" aria-modal="true" aria-label="Search and commands">
        <div className="flex items-center gap-2 border-b border-[var(--border)] px-3.5">
          <Search size={15} className="shrink-0 text-[var(--text-muted)]" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => (a + 1) % Math.max(1, items.length));
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => (a - 1 + Math.max(1, items.length)) % Math.max(1, items.length));
              }
              if (e.key === "Enter" && items[active]) run(items[active]);
            }}
            placeholder="Search projects, sources, chats — or type a command…"
            aria-label="Search anything"
            className="h-12 w-full bg-transparent text-[15px] outline-none placeholder:text-[var(--text-muted)]"
          />
          <kbd className="rounded border border-[var(--border)] px-1.5 py-px font-mono text-[11px] text-[var(--text-muted)]">esc</kbd>
        </div>
        <div className="max-h-[340px] overflow-y-auto p-1.5" role="listbox" aria-label="Results">
          {items.length === 0 && <p className="px-3 py-6 text-center text-[13px] text-[var(--text-muted)]">No matches. Try another term.</p>}
          {items.map((it, idx) => (
            <button
              key={`${it.kind}-${it.id}`}
              role="option"
              aria-selected={idx === active}
              onMouseEnter={() => setActive(idx)}
              onClick={() => run(it)}
              className={`flex w-full cursor-pointer items-center gap-2.5 rounded-[7px] px-2.5 py-2 text-left ${idx === active ? "bg-[var(--surface-secondary)]" : ""}`}
            >
              <span className="shrink-0 text-[var(--text-muted)]">{iconFor(it.kind)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{it.title}</span>
                <span className="block truncate text-xs text-[var(--text-muted)]">{it.kind} · {it.sub}</span>
              </span>
              <ArrowRight size={13} className="shrink-0 text-[var(--text-muted)]" />
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 border-t border-[var(--border)] px-3.5 py-2 text-[11px] text-[var(--text-muted)]">
          <span>↑↓ navigate</span><span>↵ open</span><span>esc close</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
