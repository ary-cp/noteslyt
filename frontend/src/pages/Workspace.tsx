import type * as React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Search,
  Settings as SettingsIcon,
} from "lucide-react";
import {
  createChat,
  deleteChat,
  deleteSource,
  getProject,
  listChats,
  listProjects,
  listSources,
  renameChat,
  retrySource,
  uploadSources,
} from "../lib/api";
import type { ChatOut, CitationLocator, ProjectListItem, ProjectOut, SourceOut, ViewerTarget } from "../lib/types";
import { useToast } from "../lib/toast";
import { useTheme } from "../lib/theme";
import { useDebounced, useMediaQuery } from "../lib/utils";
import { IconButton } from "../components/ui/Button";
import { BRAND_LOCKUP_DARK_SRC, BRAND_LOCKUP_LIGHT_SRC } from "../components/Brand";
import { ContextMenu, Popover, type CtxItem } from "../components/ui/Menu";
import { Modal } from "../components/ui/Modal";
import { SourcesPanel } from "../features/sources/SourcesPanel";
import { ChatPanel } from "../features/chat/ChatPanel";
import { StudioPanel } from "../features/studio/StudioPanel";
import { SourceReader } from "../features/viewer/SourceReader";
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

/**
 * Handoff for the sidebar reader across the Workspace remount.
 *
 * Opening a source navigates to /project/:id/source/:sid, and App remounts
 * Workspace on that URL change (route key) — discarding the in-flight
 * setReader state, including the citation locator. openSource therefore
 * stashes the full target here; the fresh instance picks it up in its
 * useState initializer. Guarded by project+source so a stale entry can
 * never open the wrong document; cleared on closeReader.
 */
let pendingReader: { projectId: string; target: ViewerTarget } | null = null;

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
  const newlyUploaded = useRef(new Set<string>());
  const currentProjectRef = useRef(projectId);
  currentProjectRef.current = projectId;
  const [chats, setChats] = useState<ChatOut[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(initialChatId ?? null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [switcherAnchor, setSwitcherAnchor] = useState({ x: 0, y: 0 });
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [sourceSidebarWidth, setSourceSidebarWidth] = useState(300);
  const [studioSidebarWidth, setStudioSidebarWidth] = useState(330);
  const [mobileTab, setMobileTab] = useState<"sources" | "chat" | "studio">("chat");
  // Sidebar reader: opening a source (or clicking a citation) shows its
  // parsed text in the left sidebar — no right-side popup.
  const [reader, setReader] = useState<ViewerTarget | null>(() => {
    if (
      pendingReader &&
      pendingReader.projectId === projectId &&
      pendingReader.target.sourceId === initialSourceId
    ) {
      return pendingReader.target;
    }
    return initialSourceId ? { sourceId: initialSourceId, nonce: 0 } : null;
  });
  useEffect(() => {
    // The initializer above consumed the handoff (if any); drop it so later
    // mounts start clean instead of reopening a stale citation.
    pendingReader = null;
  }, []);
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
        const added = ready.filter((id) => newlyUploaded.current.has(id));
        added.forEach((id) => newlyUploaded.current.delete(id));
        return new Set([...prev].filter((id) => readyIds.has(id)).concat(added));
      });
    } catch {
      /* panel surfaces its own errors */
    }
  }, [projectId]);

  const refreshChats = useCallback(async () => {
    if (!projectId) return;
    try {
      const list = await listChats(projectId);
      if (currentProjectRef.current !== projectId) return;
      setChats(list);
      // A list response may precede a newly created chat; never replace an active conversation.
      setActiveChatId((prev) => prev ?? list[0]?.id ?? null);
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

  useEffect(() => {
    if (initialChatId) setActiveChatId(initialChatId);
  }, [initialChatId]);

  useEffect(() => {
    const refresh = () => { void refreshChats(); };
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refreshChats]);

  // Retry transient failures until every upload settles; a rejected poll must
  // never leave an image stuck as "processing" until the browser is reloaded.
  const hasPendingSources = sources.some((s) => ["queued", "parsing", "embedding"].includes(s.status));
  useEffect(() => {
    if (!projectId || !hasPendingSources) return;
    let live = true;
    let timer = 0;
    const poll = async () => {
      await refreshSources();
      if (live) timer = window.setTimeout(poll, 2000);
    };
    timer = window.setTimeout(poll, 1000);
    return () => { live = false; window.clearTimeout(timer); };
  }, [projectId, hasPendingSources, refreshSources]);

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
      setMobileTab("chat");
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

  const openSource = useCallback(
    (id: string | null, locator?: CitationLocator) => {
      if (!id) return;
      const next: ViewerTarget = { sourceId: id, kind: locator?.kind, value: locator?.value, end: locator?.end, snippet: locator?.snippet, nonce: Date.now() };
      pendingReader = { projectId, target: next };
      setReader(next);
      navigate(`/project/${projectId}/source/${id}`);
      if (isMobile) setMobileTab("sources");
    },
    [navigate, projectId, isMobile],
  );

  const closeReader = useCallback(() => {
    pendingReader = null;
    setReader(null);
    navigate(`/project/${projectId}${activeChatId ? `/chat/${activeChatId}` : ""}`, { replace: true });
  }, [navigate, projectId, activeChatId]);

  const resizeSidebar = (side: "sources" | "studio", clientX: number) => {
    if (side === "sources") setSourceSidebarWidth(Math.min(520, Math.max(220, clientX)));
    else setStudioSidebarWidth(Math.min(560, Math.max(240, window.innerWidth - clientX)));
  };

  const resizeHandle = (side: "sources" | "studio", value: number) => (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${side === "sources" ? "sources" : "studio"} sidebar`}
      aria-valuenow={value}
      tabIndex={0}
      title="Drag to resize"
      className="group relative z-10 hidden w-1.5 shrink-0 cursor-col-resize touch-none items-center justify-center hover:bg-[var(--accent)]/20 focus-visible:bg-[var(--accent)]/20 md:flex"
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        resizeSidebar(side, event.clientX);
      }}
      onPointerMove={(event) => {
        if (event.buttons === 1) resizeSidebar(side, event.clientX);
      }}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const delta = event.key === "ArrowRight" ? 16 : -16;
        if (side === "sources") setSourceSidebarWidth((width) => Math.min(520, Math.max(220, width + delta)));
        else setStudioSidebarWidth((width) => Math.min(560, Math.max(240, width - delta)));
      }}
    >
      <span className="h-10 w-0.5 rounded-full bg-[var(--border)] group-hover:bg-[var(--accent)]" />
    </div>
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
        res.accepted.forEach((source) => newlyUploaded.current.add(source.id));
        setSources((prev) => [...prev.filter((source) => !res.accepted.some((added) => added.id === source.id)), ...res.accepted]);
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

  const retryFailedSource = useCallback(
    async (id: string) => {
      try {
        await retrySource(id);
        push("Retry queued", { detail: "Resuming after the failure — transcription is not redone.", tone: "success" });
        await refreshSources();
      } catch (e) {
        push("Retry failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
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
      {/* workspace top bar — full brand lockup doubles as the dashboard home link */}
      <div className="flex h-[58px] shrink-0 items-center gap-2 border-b border-[var(--border)] bg-[var(--sidebar-bg)] px-3">
        <Link
          to="/"
          aria-label="NotesLyt home — back to projects"
          title="Back to projects"
          className="shrink-0 rounded-[7px] hover:opacity-90"
        >
          <img
            src={resolved === "dark" ? BRAND_LOCKUP_DARK_SRC : BRAND_LOCKUP_LIGHT_SRC}
            alt="NotesLyt"
            draggable={false}
            style={{ height: 50, width: "auto", display: "block", borderRadius: 6 }}
          />
        </Link>
        <div className="ml-auto flex min-w-0 items-center gap-1">
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
          <span className="hidden max-w-[200px] truncate text-[13px] text-[var(--text-muted)] md:inline">
            {chats.find((c) => c.id === activeChatId)?.title ?? "New chat"}
          </span>
          <IconButton label="Search (Ctrl+K)" onClick={onSearch}>
            <Search size={16} />
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
        {(
          <aside
            className={`${isMobile ? "flex-1" : "shrink-0"} flex min-h-0 flex-col border-r border-[var(--border)] bg-[var(--sidebar-bg)]`}
            style={!isMobile ? { width: sourceSidebarWidth, flexBasis: sourceSidebarWidth } : undefined}
            aria-label={reader ? "Source reader" : "Sources and chats"}
          >
            {(() => {
              const open = reader ? sources.find((s) => s.id === reader.sourceId) ?? null : null;
              if (reader && open) {
                return <SourceReader source={open} target={reader} onBack={closeReader} />;
              }
              if (reader) {
                return (
                  <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
                    <p className="text-sm font-medium">Source not available</p>
                    <p className="text-[13px] text-[var(--text-secondary)]">It may have been removed from this project.</p>
                    <button
                      type="button"
                      onClick={closeReader}
                      className="mt-1 h-8 cursor-pointer rounded-[7px] border border-[var(--border)] px-3 text-[13px] font-medium hover:bg-[var(--surface-secondary)]"
                    >
                      Back to sources
                    </button>
                  </div>
                );
              }
              return (
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
                openSource(id);
              }}
              onRemoveSource={(id) => void removeSource(id)}
              onRetrySource={(id) => void retryFailedSource(id)}
              onFiles={(f) => void onFiles(f)}
              onNewChat={() => void newChat()}
              onOpenChat={(id) => {
                setActiveChatId(id);
                navigate(`/project/${projectId}/chat/${id}`);
                if (isMobile) setMobileTab("chat");
              }}
              onChatMenu={openChatMenu}
              onDeleteChat={(chat) => void removeChat(chat)}
              onHide={() => {
                if (isMobile) setMobileTab("chat");
                else setLeftOpen(false);
              }}
              onRefresh={() => {
                void refreshSources();
                void refreshChats();
              }}
            />
              );
            })()}
          </aside>
        )}

        {leftVisible && !isMobile && resizeHandle("sources", sourceSidebarWidth)}

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
              onChatUpdated={refreshChats}
              onOpenSource={openSource}
              onNeedSources={() => (isMobile ? setMobileTab("sources") : setLeftOpen(true))}
            />
          </main>
        )}

        {rightVisible && (
          <>
          {!isMobile && resizeHandle("studio", studioSidebarWidth)}
          <aside
            className={`${isMobile ? "flex-1" : "shrink-0"} flex min-h-0 flex-col border-l border-[var(--border)] bg-[var(--sidebar-bg)]`}
            style={!isMobile ? { width: studioSidebarWidth, flexBasis: studioSidebarWidth } : undefined}
            aria-label="Studio"
          >
            {!rightOpen && !isMobile ? null : null}
            <StudioPanel
              projectId={projectId}
              sources={sources}
              selectedSources={selectedSources}
              onHide={() => {
                if (isMobile) setMobileTab("chat");
                else setRightOpen(false);
              }}
            />
          </aside>
          </>
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
