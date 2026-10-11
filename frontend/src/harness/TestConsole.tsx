/**
 * Backend test bench.
 *
 * Manual harness for everything the API can do today (Phases A–E):
 * health, auth (argon2 + JWT), tier-1 global memory, project CRUD,
 * multi-format upload → ingestion → project-level topics, the tenancy
 * guarantee (cross-user ids return 404, never 403), server-side chats
 * grounded in retrieved context, and tier-2 project memory.
 *
 * This is NOT the product UI — plan.md D6 keeps the real frontend out of
 * scope until the backend is finished. Its job is to make every finished
 * endpoint clickable, including the failure modes, so the backend can be
 * confirmed end-to-end by hand.
 *
 * Panels:
 *   0. What works     — live checklist of the finished surface
 *   1. Health         — GET /health (no token)
 *   2. Identity       — signup / login / whoami / logout
 *   3. Global memory  — GET|PATCH /profile, POST /auth/onboarding
 *   4. Projects       — CRUD + stats (shared selection for panels below)
 *   5. Sources        — upload → ingest-status polling → topics → per-kind
 *                      viewer, warnings, citation-label preview, raw row
 *   6. Tenancy        — second-learner probe, expects 404
 *   7. Isolation      — two projects share no sources/topics/chunks
 *   8. Chats          — server-side conversations, grounded cited answers
 *   9. Memory         — tier-2 BKT mastery, weakest topics, streak
 *   10. Quiz          — verified novel quizzes, typed grading, reports
 *   11. HTTP log      — every request this page made, newest first
 *
 * Nothing beyond the quiz exists yet — Phase F is the last backend phase.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  API_BASE,
  addRequestListener,
  createChat,
  createProject,
  createQuiz,
  deleteChat,
  deleteProject,
  deleteSource,
  fetchLearner,
  fetchSourceBlob,
  getChat,
  getHealth,
  getMemory,
  getProfile,
  getProfileLevels,
  getProject,
  getReport,
  getSource,
  getToken,
  ingestStatus,
  listChats,
  listProjects,
  listSources,
  listTopics,
  login,
  onboarding,
  renameChat,
  sendMessage,
  setToken,
  signup,
  submitAnswer,
  waitForStudioJob,
  updateProfile,
  updateProject,
  uploadSources,
  whoami,
  type RequestLogEntry,
} from "../lib/api";
import {
  type AnswerOut,
  type AssessmentReportOut,
  type ChatDetailOut,
  type ChatOut,
  type HealthResponse,
  type IngestStatus,
  type LearnerOut,
  type ProfileLevels,
  type ProfileOut,
  type ProjectListItem,
  type ProjectMemory,
  type QuizOut,
  type SourceOut,
  type TopicSummary,
  type UserOut,
} from "../lib/types";
import { citationLabel } from "../lib/citations";

// ─────────────────────────────────────────────────────────────────── helpers

function message(error: unknown): string {
  if (error instanceof ApiError) return `${error.status} — ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

/** One-shot result line, so a check either succeeds or states how it failed. */
interface Probe {
  ok: boolean;
  text: string;
}

const idle: Probe | null = null;

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-line bg-surface">
      <header className="border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

function Button({
  onClick,
  children,
  tone = "default",
  disabled,
}: {
  onClick: () => void;
  children: React.ReactNode;
  tone?: "default" | "accent" | "danger";
  disabled?: boolean;
}) {
  const tones = {
    default: "border-line bg-surface hover:bg-surface-muted text-ink",
    accent: "border-accent bg-accent text-white hover:opacity-90",
    danger: "border-line bg-surface text-ink hover:bg-surface-muted",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${tones[tone]}`}
    >
      {children}
    </button>
  );
}

const field = "w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm outline-none focus:border-accent";

function Label({ children }: { children: React.ReactNode }) {
  return <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-ink-muted">{children}</label>;
}

function Row({ k, v, warn }: { k: string; v: React.ReactNode; warn?: boolean }) {
  return (
    <div className="flex items-baseline gap-3 border-b border-line/60 py-1 last:border-0">
      <span className="w-32 shrink-0 font-mono text-[11px] text-ink-muted">{k}</span>
      <span className={`min-w-0 flex-1 break-all font-mono text-[11px] ${warn ? "text-accent" : ""}`}>{v}</span>
    </div>
  );
}

function Result({ probe }: { probe: Probe | null }) {
  if (!probe) return null;
  return (
    <p
      className={`mt-2 rounded-md px-2.5 py-1.5 font-mono text-[11px] ${
        probe.ok ? "bg-surface-muted text-ink-muted" : "bg-accent-soft text-accent"
      }`}
    >
      {probe.text}
    </p>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "ready"
      ? "border-line bg-surface-muted text-ink"
      : status === "failed"
        ? "border-accent/40 bg-accent-soft text-accent"
        : "border-line bg-surface text-ink-muted";
  return (
    <span className={`inline-block rounded-md border px-1.5 py-px font-mono text-[10px] ${tone}`}>{status}</span>
  );
}

// ─────────────────────────────────────────────────────── 0 · what works

function OverviewStrip({
  user,
  projectCount,
  sourceCount,
}: {
  user: UserOut | null;
  projectCount: number;
  sourceCount: number;
}) {
  const items: [string, boolean, string][] = [
    ["health", true, "GET /health"],
    ["auth", !!user, user ? `signed in as ${user.email}` : "POST /auth/signup → login"],
    ["profile", !!user, "GET|PATCH /profile"],
    ["projects", projectCount > 0, `${projectCount} project(s)`],
    ["ingestion", sourceCount > 0, sourceCount > 0 ? `${sourceCount} source(s)` : "upload a PDF below"],
    ["chat + memory", !!user, user ? "panels 8–9" : "sign in first"],
    ["quiz", !!user, user ? "panel 10" : "sign in first"],
  ];
  return (
    <div className="flex flex-wrap gap-2">
      {items.map(([label, on, hint]) => (
        <span
          key={label}
          title={hint}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] ${
            on ? "border-line bg-surface text-ink" : "border-line bg-surface-muted text-ink-muted"
          }`}
        >
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${on ? "bg-accent" : "bg-ink-muted opacity-40"}`}
          />
          {label} · {hint}
        </span>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────── 1 · health

function HealthPanel({ onHealth }: { onHealth: (h: HealthResponse | null) => void }) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    getHealth()
      .then((h) => {
        setHealth(h);
        onHealth(h);
      })
      .catch((e: unknown) => {
        setError(message(e));
        onHealth(null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(load, [load]);

  return (
    <Panel title="1 · Health" subtitle="GET /health — no token required. If this fails, uvicorn or Postgres is down.">
      <Button onClick={load}>Refresh</Button>
      {error && <Result probe={{ ok: false, text: error }} />}
      {health && (
        <div className="mt-3">
          <Row
            k="status"
            v={
              <span className={health.status === "ok" ? "" : "text-accent"}>
                {health.status}
                {health.status !== "ok" && " — unapplied migrations: run uv run python scripts/migrate.py"}
              </span>
            }
            warn={health.status !== "ok"}
          />
          <Row k="database" v={`${health.database.target} · ${health.database.server}`} />
          <Row
            k="migrations"
            v={
              health.database.migrations_pending.length === 0
                ? "all applied"
                : health.database.migrations_pending.join(", ")
            }
            warn={health.database.migrations_pending.length > 0}
          />
          <Row k="storage" v={`${health.storage.backend} · ${health.storage.path}`} />
          <Row k="chat" v={`${health.llm_model} @ ${health.llm_base_url}`} />
          <Row k="embeddings" v={`${health.embedding_endpoint.model} @ ${health.embedding_endpoint.base_url}`} />
          <Row k="transcription" v={`${health.transcription.model} @ ${health.transcription.base_url}`} />
          <Row k="parser" v={health.parser} />
          <Row k="projects" v={String(health.projects)} />
        </div>
      )}
    </Panel>
  );
}

// ─────────────────────────────────────────────────────────── 2 · identity

function IdentityPanel({
  user,
  onAuthenticated,
}: {
  user: UserOut | null;
  onAuthenticated: (u: UserOut | null) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [probe, setProbe] = useState<Probe | null>(idle);
  const [busy, setBusy] = useState(false);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
      setProbe({ ok: true, text: `${label} — ok` });
    } catch (e) {
      setProbe({ ok: false, text: `${label} — ${message(e)}` });
    } finally {
      setBusy(false);
    }
  };

  const doSignup = () =>
    run("signup", async () => {
      const res = await signup(email, password, displayName);
      setToken(res.access_token);
      onAuthenticated(res.user);
    });

  const doLogin = () =>
    run("login", async () => {
      const res = await login(email, password);
      setToken(res.access_token);
      onAuthenticated(res.user);
    });

  const doWhoami = () =>
    run("whoami", async () => {
      onAuthenticated(await whoami());
    });

  const doLogout = () =>
    run("logout", async () => {
      setToken(null);
      onAuthenticated(null);
    });

  const quickFill = () => {
    const stamp = Date.now().toString(36);
    setEmail(`tester-${stamp}@test.co`);
    setPassword("testing-password");
    setDisplayName(`Tester ${stamp}`);
  };

  const copyToken = async () => {
    const t = getToken();
    if (!t) return;
    try {
      await navigator.clipboard.writeText(t);
      setProbe({ ok: true, text: "token copied to clipboard" });
    } catch {
      setProbe({ ok: false, text: "clipboard blocked — select the token manually" });
    }
  };

  return (
    <Panel title="2 · Identity" subtitle="POST /auth/signup · /auth/login · GET /auth/me — argon2 + HS256 JWT">
      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <Label>email</Label>
          <input className={field} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@test.co" />
        </div>
        <div>
          <Label>password</Label>
          <input
            className={field}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="min 8 characters"
          />
        </div>
        <div>
          <Label>display name</Label>
          <input
            className={field}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Priya"
          />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={doSignup} tone="accent" disabled={busy || !email || !password || !displayName}>
          Sign up
        </Button>
        <Button onClick={doLogin} disabled={busy || !email || !password}>
          Log in
        </Button>
        <Button onClick={doWhoami} disabled={busy || !getToken()}>
          Who am I
        </Button>
        <Button onClick={doLogout} tone="danger" disabled={busy || !user}>
          Log out
        </Button>
        <Button onClick={quickFill} disabled={busy}>
          Fill demo user
        </Button>
        <Button onClick={copyToken} disabled={!getToken()}>
          Copy token
        </Button>
      </div>

      <div className="mt-3">
        <Row k="signed in" v={user ? `${user.display_name} · ${user.email}` : "no — anonymous"} warn={!user} />
        <Row k="user id" v={user?.id ?? "—"} />
        <Row k="token" v={getToken() ? `${getToken()!.slice(0, 24)}… (${getToken()!.length} chars)` : "—"} />
        <Row k="send as" v="Authorization: Bearer <token> — attached automatically" />
      </div>
      <Result probe={probe} />
    </Panel>
  );
}

// ─────────────────────────────────────────────────────────── 3 · profile

function ProfilePanel({ user, levels }: { user: UserOut | null; levels: ProfileLevels | null }) {
  const [profile, setProfile] = useState<ProfileOut | null>(null);
  const [capability, setCapability] = useState("intermediate");
  const [thinking, setThinking] = useState("analytical");
  const [goals, setGoals] = useState("");
  const [probe, setProbe] = useState<Probe | null>(idle);

  const load = useCallback(async () => {
    setProfile(await getProfile());
  }, []);

  useEffect(() => {
    if (user) load().catch((e: unknown) => setProbe({ ok: false, text: message(e) }));
    else setProfile(null);
  }, [user, load]);

  const save = async () => {
    try {
      setProfile(await updateProfile({ capability_level: capability, thinking_level: thinking, goals }));
      setProbe({ ok: true, text: "profile saved (PATCH /profile)" });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    }
  };

  /** Proves omitted fields are left alone rather than reset to "unknown". */
  const goalsOnly = async () => {
    try {
      const next = await updateProfile({ goals });
      setProfile(next);
      setProbe({
        ok: true,
        text: `goals-only patch → capability_level=${next.capability_level}, thinking_level=${next.thinking_level} (unchanged)`,
      });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    }
  };

  return (
    <Panel title="3 · Global memory" subtitle="GET|PATCH /profile + POST /auth/onboarding — tier 1: who the learner is">
      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <Label>capability</Label>
          <select className={field} value={capability} onChange={(e) => setCapability(e.target.value)}>
            {(levels?.capability_levels ?? ["unknown", "beginner", "intermediate", "advanced"]).map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label>thinking level</Label>
          <select className={field} value={thinking} onChange={(e) => setThinking(e.target.value)}>
            {(levels?.thinking_levels ?? ["unknown", "concrete", "analytical", "abstract"]).map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label>goals</Label>
          <input className={field} value={goals} onChange={(e) => setGoals(e.target.value)} placeholder="pass thermodynamics" />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={save} disabled={!user}>
          Save profile
        </Button>
        <Button onClick={goalsOnly} disabled={!user}>
          Patch goals only
        </Button>
        <Button
          onClick={async () => {
            try {
              await onboarding(capability, thinking, goals);
              await load();
              setProbe({ ok: true, text: "onboarding accepted (204) — re-read via GET /profile" });
            } catch (e) {
              setProbe({ ok: false, text: message(e) });
            }
          }}
          disabled={!user}
        >
          POST /auth/onboarding
        </Button>
        <Button onClick={() => load()} disabled={!user}>
          Re-read
        </Button>
      </div>

      {profile && (
        <div className="mt-3">
          <Row k="capability" v={profile.capability_level} />
          <Row k="thinking" v={profile.thinking_level} />
          <Row k="goals" v={profile.goals || "—"} />
          <Row k="study pattern" v={JSON.stringify(profile.study_pattern)} />
          <Row k="strengths" v={JSON.stringify(profile.strengths)} />
        </div>
      )}
      <Result probe={probe} />
    </Panel>
  );
}

// ─────────────────────────────────────────────────────────── 4 · projects

function ProjectsPanel({
  user,
  projects,
  selectedId,
  onSelected,
  onChanged,
}: {
  user: UserOut | null;
  projects: ProjectListItem[];
  selectedId: string;
  onSelected: (id: string) => void;
  onChanged: () => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [probe, setProbe] = useState<Probe | null>(idle);

  const create = async () => {
    try {
      const created = await createProject({ title, subject, description });
      setTitle("");
      setSubject("");
      setDescription("");
      await onChanged();
      onSelected(created.id);
      setProbe({ ok: true, text: `project created — ${created.id.slice(0, 8)}… "${created.title}"` });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    }
  };

  const selected = projects.find((p) => p.id === selectedId) ?? null;

  return (
    <Panel title="4 · Projects" subtitle="POST/GET/PATCH/DELETE /projects — one subject, its own corpus. Click a row to select it for panels 5–6.">
      <div className="grid gap-2 sm:grid-cols-[1fr_1fr]">
        <div>
          <Label>title</Label>
          <input
            className={field}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Thermodynamics"
            onKeyDown={(e) => e.key === "Enter" && title.trim() && create()}
          />
        </div>
        <div>
          <Label>subject</Label>
          <input
            className={field}
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Physics"
            onKeyDown={(e) => e.key === "Enter" && title.trim() && create()}
          />
        </div>
      </div>
      <div className="mt-2">
        <Label>description (optional)</Label>
        <input
          className={field}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="what this project covers"
          onKeyDown={(e) => e.key === "Enter" && title.trim() && create()}
        />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={create} tone="accent" disabled={!user || !title.trim()}>
          Create
        </Button>
        <Button onClick={() => onChanged()} disabled={!user}>
          Refresh list
        </Button>
      </div>

      <div className="mt-3">
        {projects.length === 0 && <p className="py-2 text-xs text-ink-muted">No projects yet — create one above.</p>}
        {projects.map((p) => {
          const active = p.id === selectedId;
          return (
            <div
              key={p.id}
              onClick={() => onSelected(p.id)}
              className={`flex cursor-pointer items-start gap-3 border-b border-line/60 py-2 last:border-0 ${
                active ? "rounded bg-accent-soft/60 px-2" : "px-2 hover:bg-surface-muted"
              }`}
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">
                  {active ? "● " : "○ "}
                  {p.title} {p.archived ? <span className="text-[11px] text-ink-muted">(archived)</span> : null}
                </p>
                <p className="truncate text-[11px] text-ink-muted">
                  {p.subject || "no subject"} · {p.stats.sources} src · {p.stats.topics} topics · {p.stats.chunks}{" "}
                  chunks · {p.stats.chats} chats
                </p>
                <p className="truncate font-mono text-[10px] text-ink-muted">{p.id}</p>
              </div>
              <div className="flex shrink-0 gap-1" onClick={(e) => e.stopPropagation()}>
                <Button
                  onClick={async () => {
                    try {
                      const one = await getProject(p.id);
                      setProbe({ ok: true, text: `GET /projects/${one.id.slice(0, 8)}… → "${one.title}"` });
                    } catch (e) {
                      setProbe({ ok: false, text: message(e) });
                    }
                  }}
                >
                  Open
                </Button>
                <Button
                  onClick={async () => {
                    try {
                      await updateProject(p.id, { subject: `${p.subject || "untitled"} (edited)` });
                      await onChanged();
                      setProbe({ ok: true, text: "PATCH applied (subject edited)" });
                    } catch (e) {
                      setProbe({ ok: false, text: message(e) });
                    }
                  }}
                >
                  Edit
                </Button>
                <Button
                  onClick={async () => {
                    try {
                      await updateProject(p.id, { archived: !p.archived });
                      await onChanged();
                      setProbe({ ok: true, text: p.archived ? "unarchived" : "archived" });
                    } catch (e) {
                      setProbe({ ok: false, text: message(e) });
                    }
                  }}
                >
                  {p.archived ? "Unarchive" : "Archive"}
                </Button>
                <Button
                  onClick={async () => {
                    try {
                      await deleteProject(p.id);
                      await onChanged();
                      setProbe({ ok: true, text: "deleted (cascaded sources, chunks, topics, files)" });
                    } catch (e) {
                      setProbe({ ok: false, text: message(e) });
                    }
                  }}
                  tone="danger"
                >
                  Delete
                </Button>
              </div>
            </div>
          );
        })}
      </div>
      {selected && (
        <div className="mt-2">
          <Row k="selected" v={`${selected.title} · ${selected.id}`} />
        </div>
      )}
      <Result probe={probe} />
    </Panel>
  );
}

// ───────────────────────────────────────────── 5 · sources + ingestion

/** Viewer class for a stored format id — mirrors `media_kind()` in app/storage.py. */
function mediaKind(sourceType: string): "pdf" | "video" | "audio" | "image" | "text" | "office" {
  if (sourceType === "video") return "video";
  if (sourceType === "audio") return "audio";
  if (sourceType === "image") return "image";
  if (sourceType === "pdf") return "pdf";
  if (sourceType === "md" || sourceType === "txt" || sourceType === "csv" || sourceType === "tsv") return "text";
  return "office";
}

/** "12:34" or "1:02:03" → seconds, for segment seek buttons. */
function parseTimestamp(value: string): number | null {
  const parts = value.split(":").map((p) => Number(p));
  if (parts.some((n) => !Number.isFinite(n) || n < 0) || parts.length < 2 || parts.length > 3) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

/** 83.4 → "1:23", for the duration line on audio/video sources. */
function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  return `${m}:${String(Math.round(sec % 60)).padStart(2, "0")}`;
}

/**
 * In-harness viewer (Phase D.21): one branch per media kind. The bytes are
 * fetched with the bearer token into a blob URL — a plain `<a href>` cannot
 * send `Authorization` and would 401. Segment locators from the topic join
 * become seek buttons on audio/video.
 */
function SourceViewer({
  source,
  segments,
}: {
  source: SourceOut;
  segments: { kind: string; value: string }[];
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const kind = mediaKind(source.source_type);

  useEffect(() => {
    let live = true;
    let objectUrl: string | null = null;
    setUrl(null);
    setText(null);
    setError(null);
    fetchSourceBlob(source.id)
      .then(async (blob) => {
        if (!live) return;
        if (kind === "text") {
          setText(await blob.text());
        } else {
          objectUrl = URL.createObjectURL(blob);
          setUrl(objectUrl);
        }
      })
      .catch((e: unknown) => live && setError(message(e)));
    return () => {
      live = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id]);

  const seek = (value: string) => {
    const at = parseTimestamp(value);
    const el = mediaRef.current;
    if (at !== null && el) {
      el.currentTime = at;
      el.play().catch(() => undefined);
    }
  };

  const seekButtons = segments.filter((l) => l.kind === "segment" && parseTimestamp(l.value) !== null);

  if (error) return <p className="py-1 font-mono text-[11px] text-accent">{error}</p>;
  if (kind === "text") {
    if (text === null) return <p className="py-1 text-xs text-ink-muted">loading…</p>;
    return <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface-muted p-2.5 font-mono text-[11px]">{text.slice(0, 8000)}</pre>;
  }
  if (url === null) return <p className="py-1 text-xs text-ink-muted">loading…</p>;
  return (
    <div className="mt-1.5 rounded-md border border-line bg-surface-muted p-2">
      {kind === "pdf" && <iframe src={url} title={source.filename} className="h-72 w-full rounded bg-white" />}
      {kind === "video" && (
        <video ref={mediaRef as React.Ref<HTMLVideoElement>} src={url} controls preload="metadata" className="max-h-72 w-full rounded" />
      )}
      {kind === "audio" && (
        <audio ref={mediaRef as React.Ref<HTMLAudioElement>} src={url} controls preload="metadata" className="w-full" />
      )}
      {kind === "image" && <img src={url} alt={source.filename} className="max-h-72 rounded" />}
      {kind === "office" && (
        <p className="text-[11px] text-ink-muted">
          No inline preview for {source.source_type} —{" "}
          <a href={url} download={source.filename} className="text-accent underline underline-offset-2">
            download {source.filename}
          </a>
        </p>
      )}
      {seekButtons.length > 0 && (kind === "video" || kind === "audio") && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {seekButtons.slice(0, 24).map((l) => (
            <button
              key={`${l.kind}:${l.value}`}
              type="button"
              onClick={() => seek(l.value)}
              className="rounded-md border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-accent hover:bg-accent-soft"
              title={`seek to ${l.value}`}
            >
              {l.value}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * D19 preview: the exact citation labels this source's locators produce.
 * Rendered from the topic join (the same locators the audit checks against),
 * so what you see is what a future chat answer may cite.
 */
function CitationPreview({ locators }: { locators: { kind: string; value: string }[] }) {
  const labels: string[] = [];
  for (const l of locators) {
    const label = citationLabel(l.kind, l.value);
    if (!labels.includes(label)) labels.push(label);
  }
  if (labels.length === 0) return null;
  return (
    <div className="mt-1.5">
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-muted">
        citation labels this source can produce ({labels.length})
      </p>
      <div className="flex flex-wrap gap-1">
        {labels.slice(0, 40).map((label) => (
          <span
            key={label}
            className="rounded-md border border-accent/20 bg-accent-soft px-1.5 py-px font-mono text-[10px] text-accent"
          >
            {label}
          </span>
        ))}
      </div>
      {labels.length > 40 && (
        <p className="mt-1 font-mono text-[10px] text-ink-muted">… +{labels.length - 40} more</p>
      )}
    </div>
  );
}

function SourcesPanel({
  projects,
  selectedId,
  onSelected,
  onChanged,
  onSourceCount,
}: {
  projects: ProjectListItem[];
  selectedId: string;
  onSelected: (id: string) => void;
  onChanged: () => Promise<void>;
  onSourceCount: (n: number) => void;
}) {
  const [sources, setSources] = useState<SourceOut[]>([]);
  const [topics, setTopics] = useState<TopicSummary[]>([]);
  const [status, setStatus] = useState<IngestStatus | null>(null);
  const [probe, setProbe] = useState<Probe | null>(idle);
  const [busy, setBusy] = useState(false);
  const [autoPoll, setAutoPoll] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const pollTimer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    if (!selectedId) {
      setSources([]);
      setTopics([]);
      setStatus(null);
      onSourceCount(0);
      return;
    }
    const [s, t, st] = await Promise.all([
      listSources(selectedId),
      listTopics(selectedId),
      ingestStatus(selectedId),
    ]);
    setSources(s);
    setTopics(t);
    setStatus(st);
    onSourceCount(s.length);
  }, [selectedId, onSourceCount]);

  useEffect(() => {
    refresh().catch((e: unknown) => setProbe({ ok: false, text: message(e) }));
  }, [refresh]);

  // Auto-refresh while ingestion is unsettled, so progress is visible.
  useEffect(() => {
    if (!autoPoll || !selectedId) return;
    if (status?.settled) return;
    if (!sources.some((s) => ["queued", "parsing", "embedding"].includes(s.status))) return;
    pollTimer.current = window.setTimeout(() => {
      refresh().catch(() => undefined);
    }, 1500);
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, [autoPoll, selectedId, status, sources, refresh]);

  /** Poll until nothing is queued/parsing/embedding. */
  const waitForIngest = async (pid: string, tries = 90): Promise<boolean> => {
    for (let i = 0; i < tries; i++) {
      const st = await ingestStatus(pid);
      setStatus(st);
      if (st.settled) return true;
      await new Promise((r) => setTimeout(r, 1000));
    }
    return false;
  };

  const onFiles = async (files: FileList | null) => {
    if (!files?.length || !selectedId) return;
    setBusy(true);
    setProbe(null);
    try {
      const res = await uploadSources(selectedId, Array.from(files));
      if (res.rejected.length) {
        setProbe({
          ok: false,
          text: `accepted ${res.accepted.length}, rejected: ${res.rejected.map((r) => `${r.filename} — ${r.reason}`).join("; ")}`,
        });
      } else {
        setProbe({ ok: true, text: `${res.accepted.length} file(s) queued (202) — ingesting…` });
      }
      await refresh();
      await onChanged();
      const settled = await waitForIngest(selectedId);
      await refresh();
      await onChanged();
      const after = await listSources(selectedId);
      const failed = after.filter((s) => s.status === "failed");
      if (!settled) {
        setProbe({ ok: false, text: "timed out waiting for ingestion — hit Refresh." });
      } else if (failed.length) {
        setProbe({
          ok: false,
          text: `${failed.length} source(s) failed: ${failed.map((s) => `${s.filename} — ${s.status_detail}`).join("; ")}`,
        });
      } else {
        const ready = after.filter((s) => s.status === "ready");
        const chunks = ready.reduce((n, s) => n + s.chunk_count, 0);
        setProbe({ ok: true, text: `ingestion settled — ${ready.length} ready, ${chunks} chunks.` });
      }
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const pollOne = async (id: string) => {
    try {
      const s = await getSource(id);
      setSources((prev) => prev.map((p) => (p.id === id ? s : p)));
      setProbe({ ok: true, text: `${s.filename}: ${s.status}${s.status_detail ? ` — ${s.status_detail}` : ""} · ${s.chunk_count} chunks` });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteSource(id);
      await refresh();
      await onChanged();
      setProbe({ ok: true, text: "source deleted (chunks + file cascaded)" });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    }
  };

  const settled = status?.settled ?? true;
  const counts = status?.counts ?? {};

  /** All topic locators covering one source — seek buttons + citation preview. */
  const locatorsFor = (sourceId: string): { kind: string; value: string }[] =>
    topics.flatMap((t) =>
      t.sources.filter((s) => s.source_id === sourceId).flatMap((s) => s.locators),
    );

  return (
    <Panel
      title="5 · Sources & ingestion"
      subtitle="POST /projects/{id}/sources → 202 queued, then poll until ready. pdf · docx · pptx · xlsx · csv/tsv · md · txt · audio · video · image (images need IMAGE_CAPTIONS=true on the server)."
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-52 flex-1">
          <Label>project (shared with panel 4)</Label>
          <select className={field} value={selectedId} onChange={(e) => onSelected(e.target.value)}>
            {projects.length === 0 && <option value="">— create a project first —</option>}
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label>upload</Label>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.pptx,.xlsx,.csv,.tsv,.md,.txt,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.webm,.mov,.png,.jpg,.jpeg,.webp,.gif"
            disabled={busy || !selectedId}
            onChange={(e) => onFiles(e.target.files)}
            className="block text-xs file:mr-2 file:rounded-md file:border file:border-line file:bg-surface-muted file:px-2 file:py-1 file:text-xs"
          />
        </div>
        <Button onClick={() => refresh()} disabled={!selectedId}>
          Refresh
        </Button>
        <label className="flex items-center gap-1.5 text-[11px] text-ink-muted">
          <input type="checkbox" checked={autoPoll} onChange={(e) => setAutoPoll(e.target.checked)} />
          auto-poll
        </label>
      </div>

      <div className="mt-3 rounded-md border border-line bg-surface-muted px-2.5 py-2">
        <p className="font-mono text-[11px] text-ink-muted">
          ingest-status: {settled ? "settled" : "working…"} ·{" "}
          {Object.entries(counts)
            .map(([k, v]) => `${k}=${v}`)
            .join(" · ") || "no sources"}
        </p>
        {!settled && (
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
            <div className="h-full w-1/3 animate-pulse rounded-full bg-accent" />
          </div>
        )}
      </div>

      <div className="mt-3">
        {sources.length === 0 && <p className="py-1 text-xs text-ink-muted">No sources in this project.</p>}
        {sources.map((s) => (
          <div key={s.id} className="border-b border-line/60 py-2 last:border-0">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">
                  {s.filename} <span className="font-mono text-[10px] text-ink-muted">({s.source_type})</span>{" "}
                  <StatusBadge status={s.status} />
                </p>
                <p className="truncate font-mono text-[11px] text-ink-muted">
                  {s.units} units · {s.chunk_count} chunks · parser={s.parser || "—"}
                  {s.duration_s != null ? ` · ${formatDuration(s.duration_s)} of media` : ""}
                  {s.status_detail ? ` · ${s.status_detail}` : ""}
                </p>
                {s.warnings.length > 0 && (
                  <p className="break-words font-mono text-[11px] text-accent">
                    ⚠ {s.warnings.map(String).join(" · ")}
                  </p>
                )}
                <p className="truncate font-mono text-[10px] text-ink-muted">{s.id}</p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button onClick={() => pollOne(s.id)}>Poll</Button>
                <Button onClick={() => setExpandedId((prev) => (prev === s.id ? null : s.id))}>
                  {expandedId === s.id ? "Hide" : "Preview"}
                </Button>
                <Button onClick={() => remove(s.id)} tone="danger">
                  Delete
                </Button>
              </div>
            </div>
            {expandedId === s.id && (
              <div>
                <SourceViewer source={s} segments={locatorsFor(s.id)} />
                <CitationPreview locators={locatorsFor(s.id)} />
                <details className="mt-1.5">
                  <summary className="cursor-pointer font-mono text-[11px] text-accent">
                    raw source row
                  </summary>
                  <pre className="mt-1 max-h-48 overflow-auto rounded-md border border-line bg-surface-muted p-2 font-mono text-[10px]">
                    {JSON.stringify(s, null, 2)}
                  </pre>
                </details>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="mt-4">
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-muted">
          project-level topics ({topics.length}) — GET /projects/{"{id}"}/topics
        </p>
        {topics.length === 0 && <p className="text-xs text-ink-muted">No topics yet — upload a source.</p>}
        {topics.map((t) => (
          <div key={t.id} className="border-b border-line/60 py-1.5 last:border-0">
            <p className="text-sm">
              {t.name} <span className="font-mono text-[10px] text-ink-muted">/{t.slug}</span>
            </p>
            {t.description && <p className="text-[11px] text-ink-muted">{t.description}</p>}
            <p className="text-[11px] text-ink-muted">
              {t.sources.length === 0
                ? "no source linked"
                : t.sources
                    .map(
                      (s) =>
                        `${s.filename} [${s.locators.map((l) => `${l.kind}:${l.value}`).join(", ") || "—"}]`,
                    )
                    .join("  ·  ")}
            </p>
          </div>
        ))}
      </div>
      <Result probe={probe} />
    </Panel>
  );
}

// ─────────────────────────────────────────────────────────── 6 · tenancy

function TenancyPanel({ projects }: { projects: ProjectListItem[] }) {
  const [probe, setProbe] = useState<Probe | null>(idle);
  const [busy, setBusy] = useState(false);
  const [manualId, setManualId] = useState("");

  /**
   * Registers a throwaway second learner, then tries to reach the first
   * learner's project with that token. Must be 404 — never 403, never 200.
   */
  const run = async () => {
    const victim = projects[0];
    if (!victim) {
      setProbe({ ok: false, text: "create a project first" });
      return;
    }
    setBusy(true);
    const savedToken = getToken();
    const stamp = Date.now();
    try {
      const intruder = await signup(`intruder-${stamp}@test.co`, "intruder-password", "Intruder");
      setToken(intruder.access_token);

      const intruderList = await listProjects();
      let readStatus = 0;
      let readDetail = "";
      try {
        await getProject(victim.id);
        readStatus = 200;
      } catch (e) {
        readStatus = e instanceof ApiError ? e.status : 0;
        readDetail = e instanceof ApiError ? e.message : String(e);
      }

      let srcStatus = 0;
      try {
        await listSources(victim.id);
        srcStatus = 200;
      } catch (e) {
        srcStatus = e instanceof ApiError ? e.status : 0;
      }

      const leaked = intruderList.some((p) => p.id === victim.id);
      const ok = readStatus === 404 && srcStatus === 404 && !leaked;
      setProbe({
        ok,
        text: ok
          ? `PASS — intruder got ${readStatus} "${readDetail}" on the victim's project, ${srcStatus} on its sources; ` +
            `their list holds ${intruderList.length} project(s), none of them the victim's`
          : `FAIL — project=${readStatus} sources=${srcStatus} (200 would mean a breach); leaked=${leaked}`,
      });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    } finally {
      // Always put the original session back, or the console is left signed out.
      setToken(savedToken);
      setBusy(false);
    }
  };

  const probeManual = async () => {
    if (!manualId.trim()) return;
    setBusy(true);
    const savedToken = getToken();
    try {
      const intruder = await signup(`intruder-${Date.now()}@test.co`, "intruder-password", "Intruder");
      setToken(intruder.access_token);
      let st = 0;
      try {
        await getProject(manualId.trim());
        st = 200;
      } catch (e) {
        st = e instanceof ApiError ? e.status : 0;
      }
      setProbe({ ok: st === 404, text: st === 404 ? `PASS — ${st} on ${manualId.trim().slice(0, 8)}…` : `FAIL — got ${st}, expected 404` });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    } finally {
      setToken(savedToken);
      setBusy(false);
    }
  };

  return (
    <Panel
      title="6 · Tenancy check"
      subtitle="Registers a second learner and tries to reach your project. Expects 404, never 403 (ids must not be enumerable)."
    >
      <div className="flex flex-wrap gap-2">
        <Button onClick={run} tone="accent" disabled={busy || projects.length === 0}>
          {busy ? "running…" : "Attempt cross-user access"}
        </Button>
      </div>
      <div className="mt-3 flex items-end gap-2">
        <div className="flex-1">
          <Label>probe an arbitrary project id as an intruder</Label>
          <input
            className={`${field} font-mono`}
            value={manualId}
            onChange={(e) => setManualId(e.target.value)}
            placeholder="paste any project uuid"
          />
        </div>
        <Button onClick={probeManual} disabled={busy || !manualId.trim()}>
          Probe
        </Button>
      </div>
      {projects.length === 0 && <p className="mt-2 text-xs text-ink-muted">Create a project above to run this.</p>}
      <Result probe={probe} />
    </Panel>
  );
}

// ─────────────────────────────────────────────────── 7 · project isolation

/**
 * Phase C structural check: two projects' corpora must be disjoint — no
 * shared source ids, no shared topic ids, per-project chunk counts from the
 * stats block. (Vector-level isolation — "A's query retrieves nothing from
 * B" — has no HTTP endpoint yet and is covered by `scripts/smoke_test.py`.)
 */
function IsolationPanel({ projects }: { projects: ProjectListItem[] }) {
  const [a, setA] = useState<string>("");
  const [b, setB] = useState<string>("");
  const [probe, setProbe] = useState<Probe | null>(idle);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (projects.length >= 1 && !a) setA(projects[0].id);
    if (projects.length >= 2 && !b) setB(projects[1].id);
  }, [projects, a, b]);

  const compare = async () => {
    if (!a || !b || a === b) {
      setProbe({ ok: false, text: "pick two different projects" });
      return;
    }
    setBusy(true);
    try {
      const [srcA, srcB, topA, topB] = await Promise.all([
        listSources(a),
        listSources(b),
        listTopics(a),
        listTopics(b),
      ]);
      const idsA = new Set(srcA.map((s) => s.id));
      const sharedSources = srcB.filter((s) => idsA.has(s.id));
      const tidsA = new Set(topA.map((t) => t.id));
      const sharedTopics = topB.filter((t) => tidsA.has(t.id));
      const statA = projects.find((p) => p.id === a)?.stats;
      const statB = projects.find((p) => p.id === b)?.stats;
      const ok = sharedSources.length === 0 && sharedTopics.length === 0;
      setProbe({
        ok,
        text: ok
          ? `PASS — disjoint: A has ${srcA.length} src / ${topA.length} topics / ${statA?.chunks ?? "?"} chunks, ` +
            `B has ${srcB.length} src / ${topB.length} topics / ${statB?.chunks ?? "?"} chunks, 0 shared ids`
          : `FAIL — shared sources=${sharedSources.length} shared topics=${sharedTopics.length}`,
      });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="7 · Project isolation"
      subtitle="Phase C guarantee, structural half: two projects share no sources, topics or chunks."
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-40 flex-1">
          <Label>project A</Label>
          <select className={field} value={a} onChange={(e) => setA(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-40 flex-1">
          <Label>project B</Label>
          <select className={field} value={b} onChange={(e) => setB(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        </div>
        <Button onClick={compare} tone="accent" disabled={busy || projects.length < 2}>
          {busy ? "comparing…" : "Compare corpora"}
        </Button>
      </div>
      {projects.length < 2 && (
        <p className="mt-2 text-xs text-ink-muted">Create two projects (panel 4) to run this.</p>
      )}
      <Result probe={probe} />
    </Panel>
  );
}

// ─────────────────────────────────────────────────────────────── 8 · chats

function ChatsPanel({ selectedId }: { selectedId: string }) {
  const [chats, setChats] = useState<ChatOut[]>([]);
  const [chatId, setChatId] = useState<string>("");
  const [detail, setDetail] = useState<ChatDetailOut | null>(null);
  const [draft, setDraft] = useState("");
  const [probe, setProbe] = useState<Probe | null>(idle);
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const reloadList = useCallback(async () => {
    if (!selectedId) {
      setChats([]);
      setChatId("");
      setDetail(null);
      return;
    }
    const list = await listChats(selectedId);
    setChats(list);
    setChatId((prev) => (list.some((c) => c.id === prev) ? prev : (list[0]?.id ?? "")));
  }, [selectedId]);

  const reloadDetail = useCallback(async (id: string) => {
    if (!id) {
      setDetail(null);
      return;
    }
    setDetail(await getChat(id));
  }, []);

  useEffect(() => {
    reloadList().catch((e: unknown) => setProbe({ ok: false, text: message(e) }));
  }, [reloadList]);

  useEffect(() => {
    reloadDetail(chatId).catch((e: unknown) => setProbe({ ok: false, text: message(e) }));
  }, [chatId, reloadDetail]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [detail?.messages.length]);

  const create = async () => {
    try {
      const chat = await createChat(selectedId);
      await reloadList();
      setChatId(chat.id);
      setProbe({ ok: true, text: `chat opened — ${chat.id.slice(0, 8)}…` });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || !chatId || busy) return;
    setDraft("");
    setBusy(true);
    try {
      const res = await sendMessage(chatId, text);
      await Promise.all([reloadDetail(chatId), reloadList()]);
      setProbe({
        ok: res.grounded,
        text: res.grounded
          ? `grounded — ${res.citations.length} citation(s), best distance ${res.best_distance?.toFixed(3) ?? "?"}`
          : `refused — ${res.answer}`,
      });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
      setDraft(text);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="8 · Chats"
      subtitle="POST /projects/{id}/chats → POST /chats/{id}/messages. History is server-side; answers are grounded + cited or refused."
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-52 flex-1">
          <Label>chat ({chats.length} in this project)</Label>
          <select className={field} value={chatId} onChange={(e) => setChatId(e.target.value)}>
            {chats.length === 0 && <option value="">— no chats yet —</option>}
            {chats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title} ({c.message_count})
              </option>
            ))}
          </select>
        </div>
        <Button onClick={create} tone="accent" disabled={!selectedId || busy}>
          New chat
        </Button>
        <Button
          onClick={async () => {
            if (!chatId) return;
            try {
              await renameChat(chatId, `${detail?.chat.title ?? "chat"} (edited)`);
              await Promise.all([reloadDetail(chatId), reloadList()]);
              setProbe({ ok: true, text: "renamed" });
            } catch (e) {
              setProbe({ ok: false, text: message(e) });
            }
          }}
          disabled={!chatId || busy}
        >
          Rename
        </Button>
        <Button
          onClick={async () => {
            if (!chatId) return;
            try {
              await deleteChat(chatId);
              await reloadList();
              setProbe({ ok: true, text: "chat deleted (messages cascaded)" });
            } catch (e) {
              setProbe({ ok: false, text: message(e) });
            }
          }}
          tone="danger"
          disabled={!chatId || busy}
        >
          Delete
        </Button>
        <Button onClick={() => reloadDetail(chatId)} disabled={!chatId}>
          Refresh
        </Button>
      </div>

      {detail?.chat.summary && (
        <p className="mt-2 rounded-md border border-line bg-surface-muted px-2.5 py-1.5 font-mono text-[11px] text-ink-muted">
          summary: {detail.chat.summary}
        </p>
      )}

      <div className="mt-3 max-h-96 space-y-2 overflow-y-auto">
        {(!detail || detail.messages.length === 0) && (
          <p className="py-1 text-xs text-ink-muted">No messages yet — ask something grounded in the uploads.</p>
        )}
        {detail?.messages.map((m) => (
          <div key={m.id} className={`rounded-md border border-line px-2.5 py-1.5 ${m.role === "user" ? "bg-surface" : "bg-surface-muted"}`}>
            <p className="mb-0.5 font-mono text-[10px] uppercase tracking-wide text-ink-muted">
              {m.role}
              {m.role === "assistant" && (m.grounded === false ? " · refused" : m.grounded ? " · grounded" : "")}
            </p>
            <p className="whitespace-pre-wrap text-sm">{m.content}</p>
            {m.citations.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {m.citations.map((c, i) => (
                  <span
                    key={`${c.locator_kind}:${c.locator_value}:${i}`}
                    title={c.snippet || c.heading}
                    className="rounded-md border border-accent/20 bg-accent-soft px-1.5 py-px font-mono text-[10px] text-accent"
                  >
                    {citationLabel(c.locator_kind, c.locator_value)}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="mt-2 flex gap-2">
        <input
          className={field}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="What is an eigenvector?"
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={!chatId || busy}
        />
        <Button onClick={send} tone="accent" disabled={!chatId || busy || !draft.trim()}>
          {busy ? "…" : "Send"}
        </Button>
      </div>
      <Result probe={probe} />
    </Panel>
  );
}

// ────────────────────────────────────────────────────────────── 9 · memory

function MemoryPanel({ selectedId }: { selectedId: string }) {
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [probe, setProbe] = useState<Probe | null>(idle);

  const load = useCallback(async () => {
    if (!selectedId) {
      setMemory(null);
      return;
    }
    setMemory(await getMemory(selectedId));
  }, [selectedId]);

  useEffect(() => {
    load().catch((e: unknown) => setProbe({ ok: false, text: message(e) }));
  }, [load]);

  return (
    <Panel
      title="9 · Project memory"
      subtitle="GET /projects/{id}/memory — tier-2 BKT mastery, weakest topics, coverage, study streak."
    >
      <Button onClick={() => load()} disabled={!selectedId}>
        Refresh
      </Button>
      {memory && (
        <div className="mt-3">
          <Row
            k="coverage"
            v={`not started ${memory.coverage.not_started ?? 0} · in progress ${memory.coverage.in_progress ?? 0} · mastered ${memory.coverage.mastered ?? 0}`}
          />
          <Row
            k="streak"
            v={`${memory.streak.current_days ?? 0} day(s) current · ${memory.streak.active_days_14 ?? 0} active in 14d`}
          />
          <Row k="weakest" v={memory.weakest.map((t) => t.slug).join(", ") || "—"} />
          <div className="mt-2">
            {memory.topics.length === 0 && <p className="text-xs text-ink-muted">No topics yet.</p>}
            {memory.topics.map((t) => (
              <div key={t.topic_id} className="border-b border-line/60 py-1.5 last:border-0">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="truncate text-sm">
                    {t.name} <span className="font-mono text-[10px] text-ink-muted">/{t.slug}</span>
                  </p>
                  <p className="shrink-0 font-mono text-[11px] text-ink-muted">
                    {t.p_mastery.toFixed(2)} · {t.label} · {t.attempts} att · {t.coverage_status}
                  </p>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${Math.round(t.p_mastery * 100)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      <Result probe={probe} />
    </Panel>
  );
}

// ─────────────────────────────────────────────────────────────── 10 · quiz

function QuizPanel({ selectedId }: { selectedId: string }) {
  const [topics, setTopics] = useState<TopicSummary[]>([]);
  const [topicId, setTopicId] = useState("");
  const [n, setN] = useState(3);
  const [quiz, setQuiz] = useState<QuizOut | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, AnswerOut>>({});
  const [report, setReport] = useState<AssessmentReportOut | null>(null);
  const [learner, setLearner] = useState<LearnerOut | null>(null);
  const [probe, setProbe] = useState<Probe | null>(idle);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!selectedId) {
      setTopics([]);
      return;
    }
    listTopics(selectedId).then(setTopics).catch(() => setTopics([]));
  }, [selectedId]);

  const create = async () => {
    if (!selectedId || busy) return;
    setBusy(true);
    try {
      const job = await createQuiz(selectedId, {
        topic_id: topicId || undefined,
        n_questions: n,
      });
      const completed = await waitForStudioJob(selectedId, job.id);
      const q = completed.payload as unknown as QuizOut;
      setQuiz(q);
      setDrafts({});
      setResults({});
      setReport(null);
      setProbe({
        ok: true,
        text: `quiz ${q.quiz_id.slice(0, 8)}… — verified ${q.verification.verified}/${q.verification.requested} ` +
          `(regenerated ${q.verification.regenerated}, dropped ${q.verification.dropped})`,
      });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    } finally {
      setBusy(false);
    }
  };

  const submit = async (qid: string, qtype: string) => {
    if (!quiz || busy) return;
    const raw = (drafts[qid] ?? "").trim();
    if (!raw) {
      setProbe({ ok: false, text: qtype === "mcq" ? "pick an option first" : "type an answer first" });
      return;
    }
    setBusy(true);
    try {
      const answer: number | string = qtype === "short_answer" ? raw : Number(raw);
      const res = await submitAnswer(quiz.quiz_id, qid, answer);
      setResults((prev) => ({ ...prev, [qid]: res }));
      if (res.assessment_id) {
        setReport(await getReport(res.assessment_id));
        setLearner(await fetchLearner(selectedId));
      }
      setProbe({
        ok: res.correct,
        text: `${res.correct ? "correct" : "wrong"} — mastery ${res.p_before.toFixed(2)} → ${res.p_after.toFixed(2)}`,
      });
    } catch (e) {
      setProbe({ ok: false, text: message(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="10 · Quiz"
      subtitle="POST /projects/{id}/quiz → answer each question → cited feedback + BKT → report on completion."
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-40 flex-1">
          <Label>topic (blank = weakest / diagnostic spread)</Label>
          <select className={field} value={topicId} onChange={(e) => setTopicId(e.target.value)}>
            <option value="">auto</option>
            {topics.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
        <div className="w-24">
          <Label>questions</Label>
          <input
            className={field}
            type="number"
            min={1}
            max={10}
            value={n}
            onChange={(e) => setN(Math.max(1, Math.min(10, Number(e.target.value) || 3)))}
          />
        </div>
        <Button onClick={create} tone="accent" disabled={!selectedId || busy}>
          {busy ? "…" : "Generate quiz"}
        </Button>
        <Button
          onClick={async () => {
            try {
              setLearner(await fetchLearner(selectedId));
              setProbe({ ok: true, text: "learner reloaded" });
            } catch (e) {
              setProbe({ ok: false, text: message(e) });
            }
          }}
          disabled={!selectedId}
        >
          Learner
        </Button>
      </div>

      {quiz && (
        <div className="mt-3 space-y-2">
          {quiz.questions.map((q, i) => {
            const res = results[q.id];
            return (
              <div key={q.id} className="rounded-md border border-line px-2.5 py-2">
                <p className="text-sm">
                  <span className="font-mono text-[10px] text-ink-muted">Q{i + 1} · {q.qtype} · /{q.topic_slug} · </span>
                  {q.stem}
                </p>
                <p className="font-mono text-[10px] text-ink-muted">{q.locator}</p>
                {q.qtype === "mcq" && (
                  <select
                    className={`${field} mt-1.5`}
                    value={drafts[q.id] ?? ""}
                    onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
                    disabled={!!res}
                  >
                    <option value="">— pick an option —</option>
                    {q.options.map((o, oi) => (
                      <option key={oi} value={oi}>
                        {o}
                      </option>
                    ))}
                  </select>
                )}
                {q.qtype !== "mcq" && (
                  <input
                    className={`${field} mt-1.5`}
                    value={drafts[q.id] ?? ""}
                    onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
                    placeholder={q.qtype === "numerical" ? "a number" : "answer in your own words"}
                    onKeyDown={(e) => e.key === "Enter" && submit(q.id, q.qtype)}
                    disabled={!!res}
                  />
                )}
                {!res ? (
                  <div className="mt-1.5">
                    <Button onClick={() => submit(q.id, q.qtype)} disabled={busy}>
                      Submit
                    </Button>
                  </div>
                ) : (
                  <div className="mt-1.5 rounded-md bg-surface-muted px-2 py-1.5">
                    <p className={`text-xs font-medium ${res.correct ? "" : "text-accent"}`}>
                      {res.correct ? "✓ correct" : "✗ wrong"} · {res.p_before.toFixed(2)} →{" "}
                      {res.p_after.toFixed(2)}
                    </p>
                    <p className="mt-0.5 text-xs">{res.feedback}</p>
                    <p className="mt-0.5 font-mono text-[10px] text-ink-muted">
                      {res.citation.label}
                      {res.citation.source ? ` · ${res.citation.source}` : ""}
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {report?.report.score && (
        <div className="mt-3 rounded-md border border-line bg-surface-muted px-2.5 py-2">
          <p className="text-sm font-medium">
            {report.label} — {report.report.score.correct}/{report.report.score.total}
          </p>
          {report.report.next_up && (
            <p className="font-mono text-[11px] text-ink-muted">next up: {report.report.next_up}</p>
          )}
        </div>
      )}

      {learner && learner.recent_quizzes.length > 0 && (
        <div className="mt-3">
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-muted">
            recent quizzes
          </p>
          {learner.recent_quizzes.slice(0, 5).map((r) => (
            <p key={r.quiz_id} className="font-mono text-[11px] text-ink-muted">
              {r.quiz_id.slice(0, 8)}… · {r.topic_slug ?? "mixed"} · {r.correct}/{r.answered} of{" "}
              {r.n_questions} · {r.created_at.slice(0, 16).replace("T", " ")}
            </p>
          ))}
        </div>
      )}
      <Result probe={probe} />
    </Panel>
  );
}

// ────────────────────────────────────────────────────────── 11 · http log

function HttpLogPanel({ entries, onClear }: { entries: RequestLogEntry[]; onClear: () => void }) {
  if (entries.length === 0) return null;
  return (
    <Panel title="11 · HTTP log" subtitle="Every request this page made — newest first. Failures show the backend status.">
      <div className="mb-2 flex justify-end">
        <Button onClick={onClear}>Clear</Button>
      </div>
      <div className="max-h-64 overflow-y-auto">
        {entries.map((e, i) => (
          <div key={`${e.at}-${i}`} className="flex items-baseline gap-2 border-b border-line/60 py-1 last:border-0">
            <span
              className={`w-10 shrink-0 font-mono text-[11px] ${e.ok ? "text-ink-muted" : "text-accent"}`}
            >
              {e.status || "ERR"}
            </span>
            <span className="w-14 shrink-0 font-mono text-[11px] text-ink-muted">{e.method}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{e.path}</span>
            <span className="shrink-0 font-mono text-[10px] text-ink-muted">{e.ms}ms</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

// ─────────────────────────────────────────────────────────────── shell

export function TestConsole() {
  const [user, setUser] = useState<UserOut | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [levels, setLevels] = useState<ProfileLevels | null>(null);
  const [sourceCount, setSourceCount] = useState(0);
  const [log, setLog] = useState<RequestLogEntry[]>([]);

  useEffect(() => addRequestListener((e) => setLog((prev) => [e, ...prev].slice(0, 60))), []);

  const reloadProjects = useCallback(async () => {
    const list = await listProjects();
    setProjects(list);
    setSelectedId((prev) => {
      if (list.some((p) => p.id === prev)) return prev;
      return list[0]?.id ?? "";
    });
  }, []);

  // Re-adopt a session left over from a previous visit, so a refresh keeps
  // you signed in instead of silently reverting to the login form.
  useEffect(() => {
    if (!getToken()) return;
    whoami().then(setUser).catch(() => setToken(null));
    getProfileLevels().then(setLevels).catch(() => undefined);
  }, []);

  const onAuthenticated = (u: UserOut | null) => {
    setUser(u);
    if (u) getProfileLevels().then(setLevels).catch(() => undefined);
  };

  // Keep the project list in step with sign-in state.
  useEffect(() => {
    if (!user) {
      setProjects([]);
      setSelectedId("");
      setSourceCount(0);
      return;
    }
    reloadProjects().catch(() => setProjects([]));
  }, [user, reloadProjects]);

  return (
    <div className="min-h-screen bg-surface-muted">
      <header className="border-b border-line bg-surface px-5 py-3">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="text-sm font-semibold">Noteslyt — backend test bench</h1>
            <p className="text-[11px] text-ink-muted">
              manual harness for the finished API (auth · projects · ingestion · tenancy) · not the product UI ·{" "}
              <span className="font-mono">{API_BASE}</span>
            </p>
          </div>
          <a
            href={`${API_BASE}/docs`}
            className="text-[11px] text-accent underline underline-offset-2"
            target="_blank"
            rel="noreferrer"
          >
            OpenAPI docs
          </a>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-4 px-5 py-5">
        <OverviewStrip user={user} projectCount={projects.length} sourceCount={sourceCount} />
        <HealthPanel onHealth={() => undefined} />
        <IdentityPanel user={user} onAuthenticated={onAuthenticated} />
        {user && (
          <>
            <ProfilePanel user={user} levels={levels} />
            <ProjectsPanel
              user={user}
              projects={projects}
              selectedId={selectedId}
              onSelected={setSelectedId}
              onChanged={reloadProjects}
            />
            <SourcesPanel
              projects={projects}
              selectedId={selectedId}
              onSelected={setSelectedId}
              onChanged={reloadProjects}
              onSourceCount={setSourceCount}
            />
            <TenancyPanel projects={projects} />
            <IsolationPanel projects={projects} />
            <ChatsPanel selectedId={selectedId} />
            <MemoryPanel selectedId={selectedId} />
            <QuizPanel selectedId={selectedId} />
          </>
        )}
        <HttpLogPanel entries={log} onClear={() => setLog([])} />
        <p className="pb-4 text-center font-mono text-[10px] text-ink-muted">
          tip: the same flow runs headless via `uv run python scripts/verify_console_flow.py`
        </p>
      </main>
    </div>
  );
}
