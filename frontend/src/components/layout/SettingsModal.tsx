import type * as React from "react";
import { Children, useEffect, useMemo, useState } from "react";
import {
  Archive,
  BookOpen,
  FileText,
  FileVideo,
  MessageSquare,
  Search,
  Settings as SettingsIcon,
  Sparkles,
  Sun,
  Upload,
  Volume2,
} from "lucide-react";
import { getProfile, getProfileLevels, updateProfile } from "../../lib/api";
import type { ProfileLevels, ProfileOut } from "../../lib/types";
import { useAuth } from "../../lib/auth";
import { useToast } from "../../lib/toast";
import { DEFAULT_ACCENT, useTheme, type ThemeMode } from "../../lib/theme";
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
      { id: "set-personalisation", label: "Personalisation", icon: Sun, section: "personalisation" },
      { id: "set-chat", label: "Chat", icon: MessageSquare, section: "chat" },
    ],
  },
];

export function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { push } = useToast();
  const { user } = useAuth();
  const { mode, setMode, accent, setAccent, resetAccent } = useTheme();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [profile, setProfile] = useState<ProfileOut | null>(null);
  const [, setLevels] = useState<ProfileLevels | null>(null);
  const [goals, setGoals] = useState("");
  const [memo, setMemo] = useState("");
  const [autoIndex, setAutoIndex] = useState(true);
  const [defaultModel, setDefaultModel] = useState("default");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState("set-personalisation");
  const [matchCounts, setMatchCounts] = useState<Record<string, number>>({});

  const isAdmin = user?.is_admin === true;

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setTab("set-personalisation");
    let activeFlag = true;
    setLoading(true); setLoadError("");
    getProfile().then((p) => {
      if (!activeFlag) return;
      setProfile(p);
      setGoals(p.goals);
      setMemo(p.learning_style_notes ?? "");
    }).catch((e) => { if (activeFlag) setLoadError(e instanceof Error ? e.message : "Could not load profile"); }).finally(() => { if (activeFlag) setLoading(false); });
    getProfileLevels().then(setLevels).catch(() => undefined);
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
      const next = await updateProfile({ goals, learning_style_notes: memo });
      setProfile(next);
      setMemo(next.learning_style_notes ?? "");
      push("Profile saved", { tone: "success" });
    } catch (e) {
      push("Save failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally { setSaving(false); }
  };

  const q = query.trim().toLowerCase();
  const staticMatches = (label: string) => !q || label.toLowerCase().includes(q);
  // Engine updates live in the admin Embeddings tab — regular users never see them.
  const staticSections = STATIC_NAV;
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
        <div className="mt-2 grid gap-3">
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
          <Field label="Accent color" hint="Recolors buttons, highlights and progress everywhere.">
            <AccentPicker accent={accent} onChange={setAccent} onReset={resetAccent} />
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
                <p className="mt-1 text-xs text-[var(--text-muted)]">{profile.quiz_progress.answered ? "Based on saved first attempts. Open your Learning Profile page for mastery by topic." : "Take your first quiz to build a learning profile from your answers."}</p>
                <a href="/learning-profile" className="mt-1 inline-block text-xs text-[var(--accent)] hover:underline">Open Learning Profile →</a>
              </div>}
              <p className="text-xs text-[var(--text-muted)]">How you learn — auto-written by the tutor from your questions and updated after chats. Edit it any time; the tutor reads it before every reply.</p>
              <Field label="How you learn (auto-updated memo)" hint="e.g. prefers charts, slow deliberate pace, out-of-the-box questions">
                <TextArea value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="e.g. learns fast from examples, prefers charts over tables, asks out-of-the-box questions" />
              </Field>
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

function AccentPicker({ accent, onChange, onReset }: { accent: string; onChange: (hex: string) => void; onReset: () => void }) {
  const presets = [DEFAULT_ACCENT, "#3b82f6", "#8b5cf6", "#ec4899", "#f59e0b", "#06b6d4", "#ef4444"];
  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1.5">
        {presets.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => onChange(c)}
            aria-label={`Accent ${c}`}
            aria-pressed={accent.toLowerCase() === c}
            title={c}
            className={`h-7 w-7 cursor-pointer rounded-full border-2 transition-transform hover:scale-110 ${accent.toLowerCase() === c ? "border-[var(--text-primary)]" : "border-transparent"}`}
            style={{ backgroundColor: c }}
          />
        ))}
      </div>
      <label
        title="Custom color"
        className="relative h-7 w-7 cursor-pointer overflow-hidden rounded-full border border-dashed border-[var(--border-strong)]"
        style={{ background: "conic-gradient(red, yellow, lime, aqua, blue, magenta, red)" }}
      >
        <span className="sr-only">Custom accent color</span>
        <input
          type="color"
          value={/^#[0-9a-f]{6}$/i.test(accent) ? accent : DEFAULT_ACCENT}
          onChange={(e) => onChange(e.target.value)}
          aria-label="Custom accent color"
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        />
      </label>
      {accent.toLowerCase() !== DEFAULT_ACCENT && (
        <button
          type="button"
          onClick={onReset}
          className="cursor-pointer rounded px-2 py-0.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
        >
          Reset
        </button>
      )}
    </div>
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
  // Personalisation merges appearance + sources + learning profile + shortcuts
  // into one tab; Chat stays separate (personal default + admin server).
  const fragment = personalSections as React.ReactElement<{ children?: React.ReactNode }>;
  const children = Children.toArray(fragment.props.children);
  // personalSections order: [appearance, chat, sources, profile, shortcuts]
  if (id === "set-personalisation") {
    return <div className="grid gap-6">{[children[0], children[2], children[3], children[4]]}</div>;
  }
  const order = ["set-personalisation", "set-chat"];
  if (id === "set-chat") return <>{children[1] ?? null}</>;
  void order;
  return null;
}
