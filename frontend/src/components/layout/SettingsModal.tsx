import { useEffect, useState } from "react";
import { getLlamaStatus, getProfile, getProfileLevels, updateLlamaEngine, updateProfile } from "../../lib/api";
import type { LlamaStatus, ProfileLevels, ProfileOut } from "../../lib/types";
import { useToast } from "../../lib/toast";
import { useTheme, type ThemeMode } from "../../lib/theme";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import { Dropdown } from "../ui/Menu";
import { Field, RadioGroup, TextArea, Toggle } from "../ui/Controls";

const SHORTCUTS: [string, string][] = [
  ["Ctrl/⌘ + K", "Search & commands"],
  ["Ctrl/⌘ + N", "New chat"],
  ["Ctrl/⌘ + U", "Upload source"],
  ["Esc", "Close dialog"],
  ["Enter", "Send message"],
  ["Shift + Enter", "New line in chat"],
];

export function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { push } = useToast();
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

  const refreshEngine = () => {
    getLlamaStatus().then(setEngine).catch(() => setEngine(null));
  };

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true); setLoadError("");
    getProfile().then((p) => {
      if (!active) return;
      setProfile(p);
      setCapability(p.capability_level);
      setThinking(p.thinking_level);
      setGoals(p.goals);
    }).catch((e) => { if (active) setLoadError(e instanceof Error ? e.message : "Could not load profile"); }).finally(() => { if (active) setLoading(false); });
    getProfileLevels().then(setLevels).catch(() => undefined);
    refreshEngine();
    try {
      setAutoIndex(localStorage.getItem("studyspace:autoindex") !== "off");
      setDefaultModel(localStorage.getItem("studyspace:model") ?? "default");
    } catch {
      /* ignore */
    }
    return () => { active = false; };
  }, [open ]);

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

  return (
    <Modal open={open} onClose={onClose} title="Settings" width={560}>
      <div className="grid gap-6">
        <section>
          <h3 className="mb-2 text-[13px] font-semibold">Appearance</h3>
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
        </section>
        <section>
          <h3 className="mb-2 text-[13px] font-semibold">Chat</h3>
          <Field label="Default model">
            <Dropdown
              value={defaultModel}
              ariaLabel="Default model"
              onChange={(v) => {
                setDefaultModel(v);
                try {
                  localStorage.setItem("studyspace:model", v);
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
        </section>
        <section>
          <h3 className="mb-2 text-[13px] font-semibold">Sources</h3>
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[13px] font-medium">Automatic indexing</p>
              <p className="text-xs text-[var(--text-muted)]">Parse and embed uploads as soon as they arrive.</p>
            </div>
            <Toggle checked={autoIndex} onChange={(v) => {
              setAutoIndex(v);
              try {
                localStorage.setItem("studyspace:autoindex", v ? "on" : "off");
              } catch {
                /* ignore */
              }
            }} label="Automatic indexing" />
          </div>
        </section>
        <section>
          <h3 className="mb-2 text-[13px] font-semibold">Learning profile</h3>
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
        </section>
        <section>
          <h3 className="mb-2 text-[13px] font-semibold">Embedding engine</h3>
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
        </section>
        <section>
          <h3 className="mb-2 text-[13px] font-semibold">Keyboard shortcuts</h3>
          <div className="grid gap-1">
            {SHORTCUTS.map(([k, desc]) => (
              <div key={k} className="flex items-center justify-between py-0.5 text-[13px]">
                <span className="text-[var(--text-secondary)]">{desc}</span>
                <kbd className="rounded border border-[var(--border)] bg-[var(--surface-secondary)] px-1.5 py-px font-mono text-[11px]">{k}</kbd>
              </div>
            ))}
          </div>
        </section>
      </div>
    </Modal>
  );
}
