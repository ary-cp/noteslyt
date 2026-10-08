import type * as React from "react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { BookMarked } from "lucide-react";
import { login, signup } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { Button } from "../components/ui/Button";
import { Field, TextInput } from "../components/ui/Controls";

export default function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const navigate = useNavigate();
  const { signIn } = useAuth();
  const { push } = useToast();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isLogin = mode === "login";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    if (!isLogin && !name.trim()) {
      setError("Enter a display name.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = isLogin ? await login(email.trim(), password) : await signup(email.trim(), password, name.trim());
      signIn(res.access_token, res.user);
      push(isLogin ? "Welcome back" : "Account created", { detail: res.user.display_name, tone: "success" });
      navigate("/projects");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-10">
      <div className="w-full max-w-[380px]">
        <div className="mb-6 flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-[8px] bg-[var(--accent)] text-white">
            <BookMarked size={16} />
          </span>
          <span className="text-[16px] font-semibold">StudySpace</span>
        </div>
        <h1 className="text-[24px] font-semibold tracking-tight">{isLogin ? "Sign in" : "Create account"}</h1>
        <p className="mt-1 text-sm text-[var(--text-secondary)]">
          {isLogin ? "Your projects and sources are private to you." : "One account, unlimited study projects."}
        </p>
        <form onSubmit={(e) => void submit(e)} className="mt-5 grid gap-3">
          {!isLogin && (
            <Field label="Display name">
              <TextInput autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Priya" autoComplete="name" />
            </Field>
          )}
          <Field label="Email">
            <TextInput autoFocus={isLogin} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" />
          </Field>
          <Field label="Password">
            <TextInput
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isLogin ? "Your password" : "Min 8 characters"}
              autoComplete={isLogin ? "current-password" : "new-password"}
            />
          </Field>
          {error && (
            <p role="alert" className="rounded-[7px] border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-2.5 py-2 text-[13px] text-[var(--danger)]">
              {error}
            </p>
          )}
          <Button variant="primary" size="md" loading={busy} type="submit">
            {isLogin ? "Sign in" : "Create account"}
          </Button>
        </form>
        <p className="mt-4 text-center text-[13px] text-[var(--text-secondary)]">
          {isLogin ? (
            <>No account? <Link to="/signup" className="text-[var(--accent)] underline underline-offset-2">Create one</Link></>
          ) : (
            <>Have an account? <Link to="/login" className="text-[var(--accent)] underline underline-offset-2">Sign in</Link></>
          )}
        </p>
      </div>
    </div>
  );
}
