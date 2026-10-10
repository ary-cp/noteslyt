import type * as React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BookMarked,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Moon,
  PanelLeft,
  PanelRight,
  Search,
  Settings as SettingsIcon,
  Sun,
} from "lucide-react";
import {
  createChat,
  deleteChat,
  deleteSource,
  getProject,
  ingestStatus,
  listChats,
  listProjects,
  listSources,
  renameChat,
  uploadSources,
} from "../lib/api";
import type { ChatOut, ProjectListItem, ProjectOut, SourceOut } from "../lib/types";
import { useToast } from "../lib/toast";
import { useTheme } from "../lib/theme";
import { useDebounced, useMediaQuery } from "../lib/utils";
import { IconButton } from "../components/ui/Button";
import { ContextMenu, Popover, type CtxItem } from "../components/ui/Menu";
import { Modal } from "../components/ui/Modal";
import { SourcesPanel } from "../features/sources/SourcesPanel";
import { ChatPanel } from "../features/chat/ChatPanel";
import { StudioPanel } from "../features/studio/StudioPanel";
import { SourceViewerDrawer } from "../features/viewer/SourceViewerDrawer";
import { CommandPalette } from "../components/layout/CommandPalette";
import { SettingsModal } from "../components/layout/SettingsModal";

export interface WorkspaceCtx {
  project: ProjectOut | null;
  sources: SourceOut[];
  chats: ChatOut[];
  activeChatId: string | null;
  selectedSourceIds: Set<string>;
  refreshSources: () => Promise<void>;
  refreshChats: () => Promise<void>;
}

export default function Workspace({
  initialChatId,
  initialSourceId,
}: {
  initialChatId?: string;
  initialSourceId?: string;
}) {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const { push } = useToast();
  const { resolved, toggle } = useTheme();
  const isTablet = useMediaQuery("(max-width: 1100px)");
  const isMobile = useMediaQuery("(max-width: 760px)");

  const [project, setProject] = useState<ProjectOut | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [sources, setSources] = useState<SourceOut[]>([]);
  const selectedProjectRef = useRef("");
  const selectionTouchedRef = useRef(false);
  const currentProjectRef = useRef(projectId);
  currentProjectRef.current = projectId;
  const [chats, setChats] = useState<ChatOut[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(initialChatId ?? null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [switcherAnchor, setSwitcherAnchor] = useState({ x: 0, y: 0 });
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [mobileTab, setMobileTab] = useState<"sources" | "chat" | "studio">("chat");
  const [viewerId, setViewerId] = useState<string | null>(initialSourceId ?? null);
  const [ctx, setCtx] = useState<{ x: number; y: number; items: CtxItem[] } | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [palette, setPalette] = useState(false);
  const [settings, setSettings] = useState(false);
  const [renameTarget, setRenameTarget] = useState<ChatOut | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const switcherBtn = useRef<HTMLButtonElement>(null);
  const onSearch = () => setPalette(true);
  const onSettings = () => setSettings(true);

  const refreshSources = useCallback(async () => {
    if (!projectId) return;
    try {
      const list = await listSources(projectId);
      if (currentProjectRef.current !== projectId) return;
      setSources(list);
      if (selectedProjectRef.current !== projectId) {
        selectedProjectRef.current = projectId;
        selectionTouchedRef.current = false;
      }
      setSelectedIds((prev) => {
        const ready = list.filter((s) => s.status === "ready").map((s) => s.id);
        if (!selectionTouchedRef.current) return new Set(ready);
        const readyIds = new Set(ready);
        return new Set([...prev].filter((id) => readyIds.has(id)));
      });
    } catch {
      /* panel surfaces its own errors */
    }
  }, [projectId]);

  const refreshChats = useCallback(async () => {
    if (!projectId) return;
    try {
      const list = await listChats(projectId);
      setChats(list);
      setActiveChatId((prev) => {
        if (prev && list.some((c) => c.id === prev)) return prev;
        return list[0]?.id ?? null;
      });
    } catch {
      /* keep old list */
    }
  }, [projectId]);

  useEffect(() => {
    let live = true;
    setNotFound(false);
    getProject(projectId)
      .then((p) => live && setProject(p))
      .catch(() => live && setNotFound(true));
    listProjects().then((l) => live && setProjects(l)).catch(() => undefined);
    void refreshSources();
    void refreshChats();
    return () => {
      live = false;
    };
  }, [projectId, refreshSources, refreshChats]);

  // Poll ingestion while unsettled
  useEffect(() => {
    if (!projectId) return;
    if (!sources.some((s) => ["queued", "parsing", "embedding"].includes(s.status))) return;
    const t = window.setTimeout(() => {
      ingestStatus(projectId)
        .then((st) => {
          if (!st.settled) void refreshSources();
          else void refreshSources();
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearTimeout(t);
  }, [projectId, sources, refreshSources]);

  useEffect(() => {
    if (isMobile) {
      setLeftOpen(false);
      setRightOpen(false);
    } else if (isTablet) {
      setRightOpen(false);
    }
  }, [isMobile, isTablet]);

  const toggleSelect = useCallback((id: string) => {
    selectionTouchedRef.current = true;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const newChat = useCallback(async () => {
    try {
      const c = await createChat(projectId);
      await refreshChats();
      setActiveChatId(c.id);
      navigate(`/project/${projectId}/chat/${c.id}`, { replace: true });
    } catch (e) {
      push("Could not create chat", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    }
  }, [projectId, refreshChats, navigate, push]);

  const openChatMenu = useCallback(
    (e: React.MouseEvent, chat: ChatOut) => {
      e.preventDefault();
      e.stopPropagation();
      setCtx({
        x: e.clientX,
        y: e.clientY,
        items: [
          { label: "Open", onSelect: () => {
            setActiveChatId(chat.id);
            navigate(`/project/${projectId}/chat/${chat.id}`);
          } },
          {
            label: "Rename",
            onSelect: () => {
              setRenameTarget(chat);
              setRenameValue(chat.title);
            },
          },
          {
            label: "Delete",
            danger: true,
            onSelect: () => {
              deleteChat(chat.id)
                .then(() => {
                  push("Chat deleted", { tone: "success" });
                  return refreshChats();
                })
                .catch((err: unknown) => push("Delete failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" }));
            },
          },
        ],
      });
    },
    [navigate, projectId, refreshChats, push],
  );

  const removeChat = useCallback(async (chat: ChatOut) => {
    try {
      await deleteChat(chat.id);
      const next = chats.find((item) => item.id !== chat.id);
      if (activeChatId === chat.id) {
        setActiveChatId(next?.id ?? null);
        navigate(next ? `/project/${projectId}/chat/${next.id}` : `/project/${projectId}`, { replace: true });
      }
      await refreshChats();
      push("Chat deleted", { tone: "success" });
    } catch (err) {
      push("Delete failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" });
    }
  }, [activeChatId, chats, navigate, projectId, push, refreshChats]);

  const onFiles = useCallback(
    async (files: File[]) => {
      if (!files.length) return;
      try {
        const res = await uploadSources(projectId, files);
        if (res.rejected.length) {
          push("Some files were rejected", {
            detail: res.rejected.map((r) => `${r.filename}: ${r.reason}`).join("; "),
            tone: "warning",
          });
        } else {
          push(`${res.accepted.length} file(s) queued`, { detail: "Parsing and indexing in the background.", tone: "success" });
        }
        await refreshSources();
      } catch (e) {
        push("Upload failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
      }
    },
    [projectId, refreshSources, push],
  );

  const removeSource = useCallback(
    async (id: string) => {
      try {
        await deleteSource(id);
        push("Source removed", { tone: "success" });
        await refreshSources();
      } catch (e) {
        push("Remove failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
      }
    },
    [refreshSources, push],
  );

  const selectedSources = useMemo(() => sources.filter((s) => selectedIds.has(s.id)), [sources, selectedIds]);
  const [sourceQuery, setSourceQuery] = useState("");
  const debouncedQuery = useDebounced(sourceQuery, 200);

  if (notFound) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-[40px] font-semibold">404</p>
        <p className="text-sm text-[var(--text-secondary)]">This project does not exist or belongs to someone else.</p>
        <Link to="/" className="inline-flex h-9 items-center rounded-[7px] bg-[var(--accent)] px-4 text-sm font-medium text-white">
          Back to projects
        </Link>
      </div>
    );
  }

  const leftVisible = isMobile ? mobileTab === "sources" : leftOpen;
  const rightVisible = isMobile ? mobileTab === "studio" : rightOpen;
  const chatVisible = !isMobile || mobileTab === "chat";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* workspace top bar */}
      <div className="flex h-[54px] shrink-0 items-center gap-2 border-b border-[var(--border)] bg-[var(--sidebar-bg)] px-3">
        <Link to="/" aria-label="Back to projects" className="inline-flex h-8 w-8 items-center justify-center rounded-[7px] text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]">
          <ArrowLeft size={16} />
        </Link>
        <button
          ref={switcherBtn}
          type="button"
          onClick={(e) => {
            setSwitcherAnchor({ x: e.clientX - 120, y: e.currentTarget.getBoundingClientRect().bottom + 6 });
            setSwitcherOpen(true);
          }}
          className="flex min-w-0 cursor-pointer items-center gap-2 rounded-[7px] px-2 py-1.5 hover:bg-[var(--surface-secondary)]"
          aria-haspopup="dialog"
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] bg-[var(--accent)] text-[11px] font-bold text-white">
            {(project?.title ?? "…").slice(0, 1).toUpperCase()}
          </span>
          <span className="max-w-[180px] truncate text-sm font-semibold">{project?.title ?? "Loading…"}</span>
          <ChevronDown size={14} className="shrink-0 text-[var(--text-muted)]" />
        </button>
        <span className="hidden truncate text-[13px] text-[var(--text-muted)] md:inline">
          {chats.find((c) => c.id === activeChatId)?.title ?? "New chat"}
        </span>
        <span className="ml-2 hidden items-center gap-1.5 rounded-full border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--text-secondary)] lg:inline-flex">
          <BookMarked size={12} />
          {selectedIds.size} of {sources.length} sources
        </span>
        <div className="ml-auto flex items-center gap-1">
          {!isMobile && (
            <>
              <IconButton label={leftOpen ? "Hide sources" : "Show sources"} onClick={() => setLeftOpen((v) => !v)}>
                <PanelLeft size={16} />
              </IconButton>
              <IconButton label={rightOpen ? "Hide studio" : "Show studio"} onClick={() => setRightOpen((v) => !v)}>
                <PanelRight size={16} />
              </IconButton>
            </>
          )}
          <IconButton label="Search (Ctrl+K)" onClick={onSearch}>
            <Search size={16} />
          </IconButton>
          <IconButton label={resolved === "dark" ? "Light mode" : "Dark mode"} onClick={toggle}>
            {resolved === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </IconButton>
          <IconButton label="Settings" onClick={onSettings}>
            <SettingsIcon size={16} />
          </IconButton>
        </div>
      </div>

      {/* mobile tab switch */}
      {isMobile && (
        <div className="flex shrink-0 gap-1 border-b border-[var(--border)] bg-[var(--sidebar-bg)] p-2">
          {(["sources", "chat", "studio"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setMobileTab(t)}
              className={`h-8 flex-1 cursor-pointer rounded-[7px] text-[13px] font-medium capitalize ${
                mobileTab === t ? "bg-[var(--surface-secondary)] text-[var(--text-primary)]" : "text-[var(--text-secondary)]"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      {/* three regions, one workspace */}
      <div className="flex min-h-0 flex-1">
        {leftVisible && (
          <aside
            className={`${isMobile ? "flex-1" : "w-[300px] shrink-0"} flex min-h-0 flex-col border-r border-[var(--border)] bg-[var(--sidebar-bg)]`}
            aria-label="Sources and chats"
          >
            <SourcesPanel
              sources={sources}
              chats={chats}
              activeChatId={activeChatId}
              selectedIds={selectedIds}
              query={sourceQuery}
              debouncedQuery={debouncedQuery}
              onQuery={setSourceQuery}
              onToggleSelect={toggleSelect}
              onSelectAll={() => {
                selectionTouchedRef.current = true;
                setSelectedIds(new Set(sources.filter((s) => s.status === "ready").map((s) => s.id)));
              }}
              onClear={() => {
                selectionTouchedRef.current = true;
                setSelectedIds(new Set());
              }}
              onOpenSource={(id) => {
                setViewerId(id);
                navigate(`/project/${projectId}/source/${id}`);
              }}
              onRemoveSource={(id) => void removeSource(id)}
              onFiles={(f) => void onFiles(f)}
              onNewChat={() => void newChat()}
              onOpenChat={(id) => {
                setActiveChatId(id);
                navigate(`/project/${projectId}/chat/${id}`);
                if (isMobile) setMobileTab("chat");
              }}
              onChatMenu={openChatMenu}
              onDeleteChat={(chat) => void removeChat(chat)}
              onRefresh={() => {
                void refreshSources();
                void refreshChats();
              }}
            />
          </aside>
        )}

        {chatVisible && (
          <main className="flex min-w-0 flex-1 flex-col bg-[var(--main-bg)]" aria-label="Chat">
            {!leftOpen && !isMobile && (
              <button type="button" onClick={() => setLeftOpen(true)} aria-label="Show sources" className="absolute mt-2 ml-2 cursor-pointer rounded-md border border-[var(--border)] bg-[var(--surface)] p-1 text-[var(--text-secondary)]">
                <ChevronRight size={14} />
              </button>
            )}
            <ChatPanel
              projectId={projectId}
              chatId={activeChatId}
              sources={sources}
              selectedSources={selectedSources}
              selectedSourceIds={[...selectedIds]}
              selectedCount={selectedIds.size}
              totalCount={sources.length}
              onChatCreated={(id) => {
                setActiveChatId(id);
                void refreshChats();
                navigate(`/project/${projectId}/chat/${id}`, { replace: true });
              }}
              onOpenSource={(id) => {
                setViewerId(id);
                navigate(`/project/${projectId}/source/${id}`);
              }}
              onNeedSources={() => (isMobile ? setMobileTab("sources") : setLeftOpen(true))}
            />
          </main>
        )}

        {rightVisible && (
          <aside
            className={`${isMobile ? "flex-1" : "w-[330px] shrink-0"} flex min-h-0 flex-col border-l border-[var(--border)] bg-[var(--sidebar-bg)]`}
            aria-label="Studio"
          >
            {!rightOpen && !isMobile ? null : null}
            <StudioPanel
              projectId={projectId}
              sources={sources}
              selectedSources={selectedSources}
            />
          </aside>
        )}
      </div>

      {!isMobile && !rightOpen && (
        <button type="button" onClick={() => setRightOpen(true)} aria-label="Show studio" className="absolute right-2 bottom-16 cursor-pointer rounded-md border border-[var(--border)] bg-[var(--surface)] p-1 text-[var(--text-secondary)]">
          <ChevronLeft size={14} />
        </button>
      )}

      <Popover open={switcherOpen} onClose={() => setSwitcherOpen(false)} anchor={switcherAnchor} width={300}>
        <p className="px-2 pt-1 pb-2 text-xs font-medium text-[var(--text-muted)]">Switch project</p>
        <div className="max-h-[300px] overflow-y-auto">
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => {
                setSwitcherOpen(false);
                navigate(`/project/${p.id}`);
              }}
              className={`flex w-full cursor-pointer items-center gap-2 rounded-[6px] px-2 py-2 text-left text-[13px] hover:bg-[var(--surface-secondary)] ${p.id === projectId ? "text-[var(--text-primary)]" : "text-[var(--text-secondary)]"}`}
            >
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] bg-[var(--surface-secondary)] text-[11px] font-bold">
                {p.title.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 truncate">{p.title}</span>
              {p.id === projectId && <ChevronDown size={13} className="text-[var(--accent)]" />}
            </button>
          ))}
        </div>
        <Link to="/" className="mt-1 flex items-center justify-center rounded-[6px] border border-[var(--border)] px-2 py-2 text-[13px] font-medium hover:bg-[var(--surface-secondary)]">
          All projects
        </Link>
      </Popover>

      {ctx && <ContextMenu x={ctx.x} y={ctx.y} items={ctx.items} onClose={() => setCtx(null)} />}

      <SourceViewerDrawer
        sourceId={viewerId}
        sources={sources}
        onClose={() => {
          setViewerId(null);
          navigate(`/project/${projectId}${activeChatId ? `/chat/${activeChatId}` : ""}`, { replace: true });
        }}
        onRemove={(id) => void removeSource(id)}
      />

      <CommandPalette
        open={palette}
        onClose={() => setPalette(false)}
        projectId={projectId}
        onOpenCreateProject={() => navigate("/")}
        onAction={(kind) => {
          if (kind === "new-chat") void newChat();
          else if (kind === "toggle-theme") toggle();
          else if (kind === "settings") setSettings(true);
          else if (kind === "upload") push("Use + Add in Sources", { tone: "info" });
          else if (kind === "new-project") navigate("/");
        }}
      />
      <SettingsModal open={settings} onClose={() => setSettings(false)} />

      <Modal
        open={renameTarget !== null}
        onClose={() => setRenameTarget(null)}
        title="Rename chat"
        width={420}
        footer={
          <>
            <button type="button" onClick={() => setRenameTarget(null)} className="h-8 cursor-pointer rounded-[7px] border border-[var(--border)] px-3 text-[13px] font-medium hover:bg-[var(--surface-secondary)]">
              Cancel
            </button>
            <button
              type="button"
              disabled={!renameValue.trim()}
              onClick={() => {
                if (!renameTarget) return;
                renameChat(renameTarget.id, renameValue.trim())
                  .then(() => {
                    push("Chat renamed", { tone: "success" });
                    setRenameTarget(null);
                    return refreshChats();
                  })
                  .catch((err: unknown) => push("Rename failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" }));
              }}
              className="h-8 cursor-pointer rounded-[7px] bg-[var(--accent)] px-3 text-[13px] font-medium text-white hover:bg-[var(--accent-hover)] disabled:opacity-50"
            >
              Save
            </button>
          </>
        }
      >
        <label className="mb-1.5 block text-[13px] font-medium" htmlFor="ws-chat-rename">Chat name</label>
        <input
          id="ws-chat-rename"
          autoFocus
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && renameValue.trim() && renameTarget) {
              renameChat(renameTarget.id, renameValue.trim())
                .then(() => {
                  push("Chat renamed", { tone: "success" });
                  setRenameTarget(null);
                  return refreshChats();
                })
                .catch((err: unknown) => push("Rename failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" }));
            }
          }}
          className="h-9 w-full rounded-[7px] border border-[var(--border)] bg-[var(--input)] px-2.5 text-sm outline-none focus:border-[var(--accent)]"
        />
      </Modal>
    </div>
  );
}
