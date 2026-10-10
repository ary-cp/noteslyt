import type * as React from "react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Archive, FolderOpen, MoreHorizontal, Plus } from "lucide-react";
import { createProject, deleteProject, listProjects, updateProject } from "../lib/api";
import type { ProjectListItem } from "../lib/types";
import { useToast } from "../lib/toast";
import { timeAgo } from "../lib/utils";
import { Button } from "../components/ui/Button";
import { EmptyState, Skeleton } from "../components/ui/Feedback";
import { ConfirmModal, Modal } from "../components/ui/Modal";
import { Field, TextArea, TextInput } from "../components/ui/Controls";
import { ContextMenu, type CtxItem } from "../components/ui/Menu";

/* ---------------- create / rename modal ---------------- */

function ProjectFormModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (p: { id: string; title: string }) => void;
}) {
  const { push } = useToast();
  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setTitle("");
      setSubject("");
      setDescription("");
      setError("");
    }
  }, [open ]);

  const save = async () => {
    if (!title.trim()) {
      setError("Give your project a name to continue.");
      return;
    }
    setBusy(true);
    try {
      const created = await createProject({ title: title.trim(), subject: subject.trim(), description: description.trim() });
      push("Project created", { detail: created.title, tone: "success" });
      onSaved({ id: created.id, title: created.title });
      onClose();
    } catch (e) {
      push("Could not save project", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New project"
      subtitle="A project holds its own private knowledge base."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={busy}
            onClick={() => void save()}
          >
            Create project
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Field label="Project name" error={error}>
          <TextInput
            autoFocus
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              if (e.target.value.trim()) setError("");
            }}
            placeholder="e.g. Machine Learning"
            onKeyDown={(e) => e.key === "Enter" && void save()}
          />
        </Field>
        <Field label="Subject (optional)">
          <TextInput value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. CS 229" />
        </Field>
        <Field label="Description (optional)">
          <TextArea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="PCA, clustering and ML notes" />
        </Field>
      </div>
    </Modal>
  );
}

/* ---------------- card ---------------- */

function ProjectCard({
  project,
  onOpen,
  onChanged,
  onMenu,
}: {
  project: ProjectListItem;
  onOpen: () => void;
  onChanged: () => void;
  onMenu: (x: number, y: number, items: CtxItem[]) => void;
}) {
  const { push } = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [rename, setRename] = useState(project.title);

  const doDelete = async () => {
    setDeleting(true);
    try {
      await deleteProject(project.id);
      push("Project deleted", { tone: "success" });
      setConfirmDelete(false);
      onChanged();
    } catch (e) {
      push("Delete failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setDeleting(false);
    }
  };

  const menu = (e: React.MouseEvent) => {
    e.stopPropagation();
    const items: CtxItem[] = [
      { label: "Open project", onSelect: onOpen },
      {
        label: "Rename",
        onSelect: () => {
          setRename(project.title);
          setRenameOpen(true);
        },
      },
      {
        label: "Duplicate",
        onSelect: async () => {
          try {
            await createProject({ title: `${project.title} (copy)`, subject: project.subject, description: project.description });
            push("Project duplicated", { tone: "success" });
            onChanged();
          } catch (err) {
            push("Duplicate failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" });
          }
        },
      },
      {
        label: project.archived ? "Unarchive" : "Archive",
        onSelect: async () => {
          try {
            await updateProject(project.id, { archived: !project.archived });
            push(project.archived ? "Project unarchived" : "Project archived", { tone: "success" });
            onChanged();
          } catch (err) {
            push("Update failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" });
          }
        },
      },
      { label: "Delete", danger: true, onSelect: () => setConfirmDelete(true) },
    ];
    onMenu(e.clientX, e.clientY, items);
  };

  const initials = project.title.slice(0, 2).toUpperCase() || "ST";

  return (
    <>
      <article
        className="group flex cursor-pointer flex-col overflow-hidden rounded-[9px] border border-[var(--border)] bg-[var(--surface)] transition-[border-color,transform] duration-150 hover:-translate-y-[1px] hover:border-[var(--border-strong)]"
        onClick={onOpen}
        onContextMenu={(e) => {
          e.preventDefault();
          menu(e);
        }}
        tabIndex={0}
        role="link"
        aria-label={`Open ${project.title}`}
        onKeyDown={(e) => {
          if (e.key === "Enter") onOpen();
        }}
      >
        <div className="flex h-[104px] items-center justify-center border-b border-[var(--border)] bg-[linear-gradient(120deg,rgba(20,184,166,0.18),rgba(59,130,246,0.14),rgba(168,85,247,0.16))]">
          <span className="flex h-11 w-11 items-center justify-center rounded-[8px] border border-[var(--border)] bg-[var(--surface)] text-[15px] font-semibold text-[var(--text-secondary)]">
            {initials}
          </span>
        </div>
        <div className="flex flex-1 flex-col gap-1 p-3.5">
          <div className="flex items-start justify-between gap-2">
            <h3 className="truncate text-[14px] font-semibold">{project.title}</h3>
            <button
              type="button"
              aria-label={`Options for ${project.title}`}
              onClick={menu}
              className="cursor-pointer rounded-md p-1 text-[var(--text-muted)] opacity-0 group-hover:opacity-100 hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)] focus:opacity-100"
            >
              <MoreHorizontal size={15} />
            </button>
          </div>
          <p className="line-clamp-2 min-h-[20px] text-[13px] text-[var(--text-secondary)]">
            {project.description || project.subject || "No description yet."}
          </p>
          <p className="mt-auto pt-2 text-xs text-[var(--text-muted)]">
            {project.stats.sources} {project.stats.sources === 1 ? "source" : "sources"} · {project.stats.chats} {project.stats.chats === 1 ? "chat" : "chats"}
          </p>
          <p className="text-xs text-[var(--text-muted)]">Updated {timeAgo(project.updated_at)}</p>
        </div>
      </article>

      <Modal open={renameOpen} onClose={() => setRenameOpen(false)} title="Rename project" width={420} footer={
        <>
          <Button variant="ghost" onClick={() => setRenameOpen(false)}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!rename.trim()}
            onClick={() => {
              updateProject(project.id, { title: rename.trim() })
                .then(() => {
                  push("Project renamed", { tone: "success" });
                  setRenameOpen(false);
                  onChanged();
                })
                .catch((err: unknown) => push("Rename failed", { detail: err instanceof Error ? err.message : String(err), tone: "error" }));
            }}
          >
            Save
          </Button>
        </>
      }>
        <Field label="Project name">
          <TextInput autoFocus value={rename} onChange={(e) => setRename(e.target.value)} />
        </Field>
      </Modal>

      <ConfirmModal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => void doDelete()}
        busy={deleting}
        title={`Delete “${project.title}”?`}
        body="This removes the project, its sources, chats and generated materials. This cannot be undone."
      />
    </>
  );
}

/* ---------------- dashboard page ---------------- */

export function useProjectsList() {
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      setProjects(await listProjects());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  return { projects, loading, error, refresh };
}

export default function Dashboard({ onOpenPalette }: { onOpenPalette: () => void }) {
  const navigate = useNavigate();
  const { projects, loading, error, refresh } = useProjectsList();
  const [createOpen, setCreateOpen] = useState(false);
  const [ctx, setCtx] = useState<{ x: number; y: number; items: CtxItem[] } | null>(null);
  const active = useMemo(() => projects.filter((p) => !p.archived), [projects]);
  const archived = useMemo(() => projects.filter((p) => p.archived), [projects]);

  return (
    <div className="mx-auto w-full max-w-[1080px] px-5 py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight">Your Projects</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">Continue learning or start something new.</p>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[228px]" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-[9px] border border-[var(--border)] bg-[var(--surface)] p-6 text-center">
          <p className="text-sm font-medium">Could not load projects</p>
          <p className="mt-1 text-[13px] text-[var(--text-secondary)]">{error}</p>
          <div className="mt-3">
            <Button onClick={() => void refresh()}>Retry</Button>
          </div>
        </div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon={<FolderOpen size={18} />}
          title="No projects yet"
          body="Create a project to collect PDFs, lectures and notes — then chat and study from them."
          action={<Button variant="primary" onClick={() => setCreateOpen(true)}><Plus size={15} /> Create project</Button>}
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {active.map((p) => (
              <ProjectCard key={p.id} project={p} onOpen={() => navigate(`/project/${p.id}`)} onChanged={() => void refresh()} onMenu={(x, y, items) => setCtx({ x, y, items })} />
            ))}
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="flex min-h-[228px] cursor-pointer flex-col items-center justify-center gap-2 rounded-[9px] border border-dashed border-[var(--border-strong)] text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface)]">
                <Plus size={16} />
              </span>
              <span className="text-sm font-medium">Create new project</span>
              <span className="text-xs text-[var(--text-muted)]">Collect sources, then study</span>
            </button>
          </div>
          {archived.length > 0 && (
            <div className="mt-8">
              <h2 className="mb-3 flex items-center gap-1.5 text-[13px] font-medium text-[var(--text-secondary)]">
                <Archive size={14} /> Archived ({archived.length})
              </h2>
              <div className="grid grid-cols-1 gap-3 opacity-80 sm:grid-cols-2 lg:grid-cols-3">
                {archived.map((p) => (
                  <ProjectCard key={p.id} project={p} onOpen={() => navigate(`/project/${p.id}`)} onChanged={() => void refresh()} onMenu={(x, y, items) => setCtx({ x, y, items })} />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <p className="mt-8 text-center text-xs text-[var(--text-muted)]">
        Press <Kbd>Ctrl K</Kbd> for search & commands · <button type="button" className="cursor-pointer underline" onClick={onOpenPalette}>open palette</button>
      </p>

      <ProjectFormModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSaved={(p) => {
          void refresh();
          navigate(`/project/${p.id}`);
        }}
      />
      {ctx && <ContextMenu x={ctx.x} y={ctx.y} items={ctx.items} onClose={() => setCtx(null)} />}
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-[var(--border)] bg-[var(--surface-secondary)] px-1.5 py-px font-mono text-[11px]">
      {children}
    </kbd>
  );
}
