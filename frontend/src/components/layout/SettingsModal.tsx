import type * as React from "react";
import { Children, useEffect, useMemo, useState } from "react";
import {
  Archive,
  BookOpen,
  FileText,
  FileVideo,
  FolderOpen,
  Hash,
  MessageSquare,
  RefreshCw,
  Search,
  Settings as SettingsIcon,
  Sparkles,
  Sun,
  Upload,
  Volume2,
} from "lucide-react";
import { getLlamaStatus, getProfile, getProfileLevels, updateLlamaEngine, updateProfile } from "../../lib/api";
import type { LlamaStatus, ProfileLevels, ProfileOut } from "../../lib/types";
import { useAuth } from "../../lib/auth";
import { useToast } from "../../lib/toast";
import { useTheme, type ThemeMode } from "../../lib/theme";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import { Dropdown } from "../ui/Menu";
import { Field, RadioGroup, TextArea, TextInput, Toggle } from "../ui/Controls";
import { ADMIN_GROUP_TITLES, ADMIN_NAV_TABS, AdminSettings } from "./AdminSettings";
import type { AdminTab } from "./AdminSettings";

const SHORTCUTS: [string, string][] = [
  ["Ctrl/⌘ + K", "Search & commands"],
  ["Ctrl/⌘ + N", "New chat"],
  ["Ctrl/⌘ + U", "Upload source"],
  ["Esc", "Close dialog"],
  ["Enter", "Send message"],
  ["Shift + Enter", "New line in chat"],
];

const GROUP_ICONS: Record<string, typeof Sun> = {
  chat: MessageSquare,
  embeddings: Sparkles,
  transcription: Volume2,
  media: FileVideo,
  parsing: FileText,
  retrieval: Search,
  memory: Archive,
  learner: BookOpen,
  uploads: Upload,
};

interface StaticNav {
  id: string;
  label: string;
  icon: typeof Sun;
  section: string;
}

const STATIC_NAV: { header: string; items: StaticNav[] }[] = [
  {
    header: "Preferences",
    items: [
      { id: "set-appearance", label: "Appearance", icon: Sun, section: "appearance" },
      { id: "set-chat", label: "Chat", icon: MessageSquare, section: "chat" },
      { id: "set-sources", label: "Sources", icon: FolderOpen, section: "sources" },
      { id: "set-shortcuts", label: "Shortcuts", icon: Hash, section: "shortcuts" },
    ],
  },
  {
    header: "Profile",
    items: [{ id: "set-profile", label: "Learning profile", icon: Sparkles, section: "profile" }],
  },
  {
    header: "System",
    items: [{ id: "set-engine", label: "Embedding engine", icon: RefreshCw, section: "engine" }],
  },
];

export function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { push } = useToast();
  const { user } = useAuth();
  const { mode, setMode } = useTheme();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [profile, setProfile] = useState<ProfileOut | null>(null);
  const [levels, setLevels] = useState<ProfileLevels | null>(null);
  const [capability, setCapability] = useState("unknown");
  const [thinking, setThinking] = useState("unknown");
  const [goals, setGoals] = useState("");
  const [autoIndex, setAutoIndex] = useState(true);
  const [defaultModel, setDefaultModel] = useState("default");
  const [engine, setEngine] = useState<LlamaStatus | null>(null);
  const [engineBusy, setEngineBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState("set-appearance");
  const [matchCounts, setMatchCounts] = useState<Record<string, number>>({});

  const isAdmin = user?.is_admin === true;

  const refreshEngine = () => {
    getLlamaStatus().then(setEngine).catch(() => setEngine(null));
  };

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setTab("set-appearance");
    let activeFlag = true;
    setLoading(true); setLoadError("");
    getProfile().then((p) => {
      if (!activeFlag) return;
      setProfile(p);
      setCapability(p.capability_level);
      setThinking(p.thinking_level);
      setGoals(p.goals);
    }).catch((e) => { if (activeFlag) setLoadError(e instanceof Error ? e.message : "Could not load profile"); }).finally(() => { if (activeFlag) setLoading(false); });
    getProfileLevels().then(setLevels).catch(() => undefined);
    refreshEngine();
    try {
      setAutoIndex(localStorage.getItem("noteslyt:autoindex") !== "off");
      setDefaultModel(localStorage.getItem("noteslyt:model") ?? "default");
    } catch {
      /* ignore */
    }
    return () => { activeFlag = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const saveProfile = async () => {
    setSaving(true);
    try {
      const next = await updateProfile({ capability_level: capability, thinking_level: thinking, goals });
      setProfile(next);
      push("Profile saved", { tone: "success" });
    } catch (e) {
      push("Save failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally { setSaving(false); }
  };

  const q = query.trim().toLowerCase();
  const staticMatches = (label: string) => !q || label.toLowerCase().includes(q);
  // Admins manage the engine inside the Embeddings tab — no separate entry.
  const staticSections = isAdmin
    ? STATIC_NAV.map((s) => ({ ...s, items: s.items.filter((i) => i.id !== "set-engine") })).filter((s) => s.items.length > 0)
    : STATIC_NAV;
  const adminGroups = useMemo(
    () => ADMIN_NAV_TABS.filter((g) => !q || (matchCounts[g] ?? 0) > 0 || ADMIN_GROUP_TITLES[g].toLowerCase().includes(q)),
    [q, matchCounts],
  );
  const adminTab = tab.startsWith("admin-") ? tab.slice("admin-".length) : null;

  // Searching can hide the open tab — fall back to the first visible one.
  useEffect(() => {
    if (!q) return;
    const visibleStatic = staticSections.flatMap((s) => s.items)
      .filter((item) => staticMatches(item.label))
      .map((item) => item.id);
    const visibleAdmin: string[] = [];
    for (const g of adminGroups) visibleAdmin.push(`admin-${g}`);
    const visible = new Set([...visibleStatic, ...visibleAdmin]);
    if (!visible.has(tab) && visibleAdmin.length + visibleStatic.length > 0) {
      const first = [...visibleStatic, ...visibleAdmin][0];
      if (first) setTab(first);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const personalSections = (
    <>
      <div id="set-appearance">
        <h3 className="text-[15px] font-semibold">Appearance</h3>
        <div className="mt-2">
          <Field label="Theme">
            <RadioGroup
              value={mode}
              onChange={(v) => setMode(v as ThemeMode)}
              ariaLabel="Theme"
              options={[
                { value: "dark", label: "Dark" },
                { value: "light", label: "Light" },
                { value: "system", label: "System" },
              ]}
            />
          </Field>
        </div>
      </div>
      <div id="set-chat">
        <h3 className="text-[15px] font-semibold">Chat</h3>
        <ChatDefaultBlock defaultModel={defaultModel} onChange={setDefaultModel} />
      </div>
      <div id="set-sources">
        <h3 className="text-[15px] font-semibold">Sources</h3>
        <div className="mt-2 flex items-center justify-between gap-3">
          <div>
            <p className="text-[13px] font-medium">Automatic indexing</p>
            <p className="text-[13px] text-[var(--text-muted)]">Parse and embed uploads as soon as they arrive.</p>
          </div>
          <Toggle checked={autoIndex} onChange={(v) => {
            setAutoIndex(v);
            try {
              localStorage.setItem("noteslyt:autoindex", v ? "on" : "off");
            } catch {
              /* ignore */
            }
          }} label="Automatic indexing" />
        </div>
      </div>
      <div id="set-profile">
        <h3 className="text-[15px] font-semibold">Learning profile</h3>
        <div className="mt-2">
          {loading ? <p className="text-sm text-[var(--text-muted)]">Loading your profile…</p> : loadError ? <p role="alert" className="text-sm text-[var(--danger)]">{loadError}</p> : profile ? (
            <div className="grid gap-3">
              {profile.quiz_progress && <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-secondary)] p-3">
                <p className="text-sm font-medium">Your quiz progress</p>
                <p className="mt-1 text-sm">{profile.quiz_progress.answered ? `${Math.round((profile.quiz_progress.correct / profile.quiz_progress.answered) * 100)}% correct overall` : "No quizzes taken yet"}</p>
                <p className="mt-1 text-xs text-[var(--text-muted)]">{profile.quiz_progress.answered ? "Based on saved first attempts. Open a project's Studio to see your overall mastery." : "Take your first quiz to build a learning profile from your answers."}</p>
              </div>}
              <p className="text-xs text-[var(--text-muted)]">Set your learning preferences below. Quiz performance quietly adapts what you practice next.</p>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Capability">
                  <Dropdown
                    value={capability}
                    ariaLabel="Capability"
                    onChange={setCapability}
                    options={(levels?.capability_levels ?? ["unknown", "beginner", "intermediate", "advanced"]).map((v) => ({ value: v, label: v }))}
                  />
                </Field>
                <Field label="Thinking">
                  <Dropdown
                    value={thinking}
                    ariaLabel="Thinking"
                    onChange={setThinking}
                    options={(levels?.thinking_levels ?? ["unknown", "concrete", "analytical", "abstract"]).map((v) => ({ value: v, label: v }))}
                  />
                </Field>
              </div>
              <Field label="Goals">
                <TextArea value={goals} onChange={(e) => setGoals(e.target.value)} placeholder="e.g. pass thermodynamics" />
              </Field>
              <div>
                <Button variant="secondary" loading={saving} disabled={saving} onClick={() => void saveProfile()}>Save profile</Button>
              </div>
            </div>
          ) : (
            <p className="text-[13px] text-[var(--text-muted)]">Sign in to edit your learning profile.</p>
          )}
        </div>
      </div>
      <div id="set-engine">
        <h3 className="text-[15px] font-semibold">Embedding engine</h3>
        <div className="mt-2">
          {engine === null ? (
            <p className="text-[13px] text-[var(--text-muted)]">Engine status unavailable.</p>
          ) : engine.mode === "cloud" ? (
            <p className="text-[13px] text-[var(--text-muted)]">
              Using the cloud endpoint from <span className="font-mono">EMBEDDING_*</span> env vars.
              Set <span className="font-mono">EMBEDDING_MODE=local</span> to use the vendored llama.cpp server.
            </p>
          ) : (
            <div className="grid gap-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[13px] font-medium">
                    Local llama.cpp {engine.build_tag ? `· ${engine.build_tag}` : ""}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {engine.installed ? "Installed" : "Not downloaded yet"}
                    {engine.model_present ? " · model ready" : " · model missing"}
                    {engine.running ? " · server running" : " · server sleeps until needed"}
                  </p>
                  {(engine.update_state !== "idle" || engine.update_message) && (
                    <p className="mt-1 text-xs text-[var(--text-muted)]">
                      Update: {engine.update_state}{engine.update_message ? ` — ${engine.update_message}` : ""}
                    </p>
                  )}
                </div>
                <Button
                  variant="secondary"
                  loading={engineBusy}
                  disabled={engineBusy}
                  onClick={() => {
                    setEngineBusy(true);
                    updateLlamaEngine()
                      .then(() => {
                        push("Engine update started", { detail: "This downloads in the background.", tone: "success" });
                        window.setTimeout(refreshEngine, 5000);
                      })
                      .catch((e: unknown) => push("Update failed to start", { detail: e instanceof Error ? e.message : String(e), tone: "error" }))
                      .finally(() => setEngineBusy(false));
                  }}
                >
                  Update engine
                </Button>
              </div>
              <button
                type="button"
                onClick={refreshEngine}
                className="cursor-pointer self-start text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              >
                Refresh status
              </button>
            </div>
          )}
        </div>
      </div>
      <div id="set-shortcuts">
        <h3 className="text-[15px] font-semibold">Keyboard shortcuts</h3>
        <div className="mt-2 grid gap-1">
          {SHORTCUTS.map(([k, desc]) => (
            <div key={k} className="flex items-center justify-between py-0.5 text-[13px]">
              <span className="text-[var(--text-secondary)]">{desc}</span>
              <kbd className="rounded border border-[var(--border)] bg-[var(--surface-secondary)] px-1.5 py-px font-mono text-[11px]">{k}</kbd>
            </div>
          ))}
        </div>
      </div>
    </>
  );

  if (!isAdmin) {
    return (
      <Modal open={open} onClose={onClose} title="Settings" width={560}>
        <div className="grid gap-6">{personalSections}</div>
      </Modal>
    );
  }

  const navItemCls = (id: string) =>
    `flex w-full cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] ${
      tab === id ? "bg-[var(--surface-secondary)] font-medium text-[var(--text-primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
    }`;

  // One tab visible at a time — sidebar switches, nothing scroll-spies.
  const staticTab = staticSections.flatMap((s) => s.items).find((item) => item.id === tab);

  return (
    <Modal open={open} onClose={onClose} title="Settings" width={960}>
      <div className="flex items-start gap-6">
        <aside className="sticky top-0 max-h-[68vh] w-56 shrink-0 self-start overflow-y-auto pr-1">
          <div className="relative mb-3">
            <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" />
            <TextInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search"
              aria-label="Search settings"
              className="pl-8"
            />
          </div>
          {staticSections.map((section) => {
            const visible = section.items.filter((item) => staticMatches(item.label));
            if (visible.length === 0) return null;
            return (
              <div key={section.header} className="mb-3">
                <p className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">{section.header}</p>
                {visible.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button key={item.id} type="button" onClick={() => setTab(item.id)} className={navItemCls(item.id)}>
                      <Icon size={14} className="shrink-0" /> {item.label}
                    </button>
                  );
                })}
              </div>
            );
          })}
          <div className="mb-3">
            <p className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">Admin</p>
            {adminGroups.map((group) => {
              const Icon = GROUP_ICONS[group] ?? SettingsIcon;
              const count = matchCounts[group];
              return (
                <button key={group} type="button" onClick={() => setTab(`admin-${group}`)} className={navItemCls(`admin-${group}`)}>
                  <Icon size={14} className="shrink-0" /> {ADMIN_GROUP_TITLES[group] ?? group}
                  {q && count !== undefined && (
                    <span className="ml-auto text-[11px] text-[var(--text-muted)]">{count}</span>
                  )}
                </button>
              );
            })}
          </div>
        </aside>
        <div className="min-h-[50vh] min-w-0 flex-1">
          {isAdmin && (adminTab !== null || tab === "set-chat") ? (
            <>
              {tab === "set-chat" && (
                <>
                  <h3 className="text-[15px] font-semibold">Chat</h3>
                  <ChatDefaultBlock defaultModel={defaultModel} onChange={setDefaultModel} />
                  <h3 className="mb-2 mt-6 text-[15px] font-semibold">Server</h3>
                </>
              )}
              <AdminSettings
                query={query}
                tab={(adminTab ?? "chat") as AdminTab}
                onMatchCounts={setMatchCounts}
              />
            </>
          ) : staticTab ? (
            <StaticTabContent id={staticTab.id} personalSections={personalSections} />
          ) : null}
        </div>
      </div>
    </Modal>
  );
}

function ChatDefaultBlock({ defaultModel, onChange }: { defaultModel: string; onChange: (v: string) => void }) {
  return (
    <div className="mt-2">
      <Field label="Default mode">
        <Dropdown
          value={defaultModel}
          ariaLabel="Default response mode"
          onChange={(v) => {
            onChange(v);
            try {
              localStorage.setItem("noteslyt:model", v);
            } catch {
              /* ignore */
            }
          }}
          options={[
            { value: "default", label: "Default" },
            { value: "precise", label: "Precise" },
            { value: "creative", label: "Explanatory" },
          ]}
        />
      </Field>
    </div>
  );
}

function StaticTabContent({ id, personalSections }: { id: string; personalSections: React.ReactNode }) {
  // personalSections holds every personal tab in order; show only the active one.
  const fragment = personalSections as React.ReactElement<{ children?: React.ReactNode }>;
  const children = Children.toArray(fragment.props.children);
  const order = ["set-appearance", "set-chat", "set-sources", "set-profile", "set-engine", "set-shortcuts"];
  const index = order.indexOf(id);
  return <>{children[index] ?? null}</>;
}
