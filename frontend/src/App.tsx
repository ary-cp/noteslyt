import type * as React from "react";
/**
 * NotesLyt — AI learning workspace.
 *
 * Routes:
 *   /            → dashboard
 *   /projects    → legacy client redirect (API on hard navigation)
 *   /login, /signup
 *   /project/:projectId
 *   /project/:projectId/chat/:chatId
 *   /project/:projectId/source/:sourceId
 *   *            → 404
 *
 * The old backend test console moved to /console (kept for manual API checks).
 */

import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import { ThemeProvider, useTheme } from "./lib/theme";
import { ToastProvider, useToast } from "./lib/toast";
import { createProject, getSetupStatus } from "./lib/api";
import { AppHeader } from "./components/layout/AppHeader";
import { DriftBanner } from "./components/layout/DriftBanner";
import { SettingsModal } from "./components/layout/SettingsModal";
import { SetupWizard } from "./components/layout/SetupWizard";
import { CommandPalette } from "./components/layout/CommandPalette";
import { ToastViewport } from "./components/ui/Toast";
import { Modal } from "./components/ui/Modal";
import { Button } from "./components/ui/Button";
import { Field, TextArea, TextInput } from "./components/ui/Controls";
import Dashboard from "./pages/Dashboard";
import Workspace from "./pages/Workspace";
import AuthPage from "./pages/Auth";
import NotFound from "./pages/NotFound";
import { TestConsole } from "./harness/TestConsole";
import { tooltipStyles } from "./components/ui/Tooltip";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const loc = useLocation();
  if (!ready) {
    return (
      <div className="flex min-h-full items-center justify-center">
        <div className="grid w-full max-w-[1080px] grid-cols-1 gap-3 px-5 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-[200px] animate-pulse rounded-[9px] bg-[var(--surface-secondary)]" />
          ))}
        </div>
      </div>
    );
  }
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return <>{children}</>;
}

function ProjectRoute({ view }: { view: "workspace" | "chat" | "source" }) {
  const { projectId, chatId, sourceId } = useParams();
  void view;
  void projectId;
  return (
    <Workspace
      key={projectId}
      initialChatId={chatId}
      initialSourceId={sourceId}
    />
  );
}

function Shell() {
  const navigate = useNavigate();
  const loc = useLocation();
  const { push } = useToast();
  const { toggle } = useTheme();
  const { user, ready } = useAuth();
  const [palette, setPalette] = useState(false);
  const [settings, setSettings] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  const inWorkspace = loc.pathname.startsWith("/project/");
  const projectId = inWorkspace ? loc.pathname.split("/")[2] : undefined;
  const hideHeader = inWorkspace || ["/login", "/signup"].includes(loc.pathname);

  const paletteAction = useCallback(
    (kind: "new-project" | "new-chat" | "upload" | "toggle-theme" | "settings", pid?: string) => {
      if (kind === "new-project") setCreateOpen(true);
      else if (kind === "toggle-theme") toggle();
      else if (kind === "settings") setSettings(true);
      else if (kind === "new-chat" && pid) navigate(`/project/${pid}`);
      else if (kind === "upload" && pid) {
        push("Use + Add in Sources", { detail: "The upload control lives at the top of the left sidebar.", tone: "info" });
        navigate(`/project/${pid}`);
      }
    },
    [navigate, push, toggle],
  );

  // global shortcuts: Ctrl+K palette, Ctrl+N chat, Ctrl+U upload hint
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((v) => !v);
      }
      if (mod && e.key.toLowerCase() === "n" && !e.shiftKey) {
        const inProj = window.location.pathname.startsWith("/project/");
        if (inProj) {
          e.preventDefault();
          paletteAction("new-chat", window.location.pathname.split("/")[2]);
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [paletteAction]);

  // First-run onboarding: the first admin lands in the setup wizard so API
  // keys/endpoints are entered before anything else. Finish or Skip marks it
  // done; closing is disabled so it reappears next sign-in until resolved.
  useEffect(() => {
    if (!ready || !user?.is_admin) return;
    getSetupStatus()
      .then((s) => {
        if (!s.setup_completed) setSetupOpen(true);
      })
      .catch(() => undefined);
  }, [ready, user]);

  const create = async () => {
    if (!title.trim()) {
      setFormError("Give your project a name to continue.");
      return;
    }
    setBusy(true);
    try {
      const p = await createProject({ title: title.trim(), subject: subject.trim(), description: description.trim() });
      push("Project created", { detail: p.title, tone: "success" });
      setCreateOpen(false);
      setTitle("");
      setSubject("");
      setDescription("");
      navigate(`/project/${p.id}`);
    } catch (e) {
      push("Could not create project", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col bg-[var(--background)] text-[var(--text-primary)]">
      <style>{tooltipStyles}</style>
      {!hideHeader && <AppHeader onSearch={() => setPalette(true)} onSettings={() => setSettings(true)} />}
      <DriftBanner />
      <div className="flex min-h-0 flex-1 flex-col">
        <Routes>
          <Route path="/projects" element={<Navigate to="/" replace />} />
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/signup" element={<AuthPage mode="signup" />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <Dashboard onOpenPalette={() => setPalette(true)} />
              </RequireAuth>
            }
          />
          <Route
            path="/project/:projectId"
            element={
              <RequireAuth>
                <ProjectRoute view="workspace" />
              </RequireAuth>
            }
          />
          <Route
            path="/project/:projectId/chat/:chatId"
            element={
              <RequireAuth>
                <ProjectRoute view="chat" />
              </RequireAuth>
            }
          />
          <Route
            path="/project/:projectId/source/:sourceId"
            element={
              <RequireAuth>
                <ProjectRoute view="source" />
              </RequireAuth>
            }
          />
          <Route path="/console" element={<TestConsole />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </div>

      <CommandPalette
        open={palette}
        onClose={() => setPalette(false)}
        onAction={paletteAction}
        projectId={projectId}
        onOpenCreateProject={() => setCreateOpen(true)}
      />
      <SettingsModal open={settings} onClose={() => setSettings(false)} />
      <SetupWizard open={setupOpen} onDone={() => setSetupOpen(false)} />
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="New project"
        subtitle="A project holds its own private knowledge base."
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button variant="primary" loading={busy} onClick={() => void create()}>Create project</Button>
          </>
        }
      >
        <div className="grid gap-3">
          <Field label="Project name" error={formError}>
            <TextInput autoFocus value={title} onChange={(e) => { setTitle(e.target.value); if (e.target.value.trim()) setFormError(""); }} placeholder="e.g. Machine Learning" onKeyDown={(e) => e.key === "Enter" && void create()} />
          </Field>
          <Field label="Subject (optional)">
            <TextInput value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. CS 229" />
          </Field>
          <Field label="Description (optional)">
            <TextArea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this project about?" />
          </Field>
        </div>
      </Modal>
      <ToastViewport />
    </div>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <AuthProvider>
          <Shell />
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
