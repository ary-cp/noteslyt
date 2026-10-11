import type * as React from "react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Eye, EyeOff } from "lucide-react";
import { login, signup } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import "./Auth.css";

export default function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const navigate = useNavigate();
  const { signIn } = useAuth();
  const { push } = useToast();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <div className="auth-ambient" aria-hidden="true">
        <div className="auth-blob auth-blob-1" />
        <div className="auth-blob auth-blob-2" />
        <div className="auth-blob auth-blob-3" />
      </div>
      <div className="auth-grid" aria-hidden="true" />

      <div className="auth-card">
        <div className="auth-brand">
          <img
            src="/05-app-icon-neon.svg"
            alt="NotesLyt"
            className="auth-logo-img"
            width={52}
            height={52}
            draggable={false}
          />
          <span className="auth-name">
            Notes<span className="auth-name-accent">lyt</span>
          </span>
        </div>

        <div className="auth-head">
          <h1 className="auth-title">{isLogin ? "Sign in" : "Create account"}</h1>
          <p className="auth-subtitle">
            {isLogin ? "Your projects and sources are private to you." : "One account, unlimited study projects."}
          </p>
        </div>

        <form onSubmit={(e) => void submit(e)}>
          {!isLogin && (
            <div className="auth-group">
              <div className="auth-label-row">
                <label className="auth-label" htmlFor="auth-name">Display name</label>
              </div>
              <div className="auth-input-wrap">
                <input
                  id="auth-name"
                  type="text"
                  className="auth-input"
                  placeholder="Priya"
                  autoComplete="name"
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
            </div>
          )}

          <div className="auth-group">
            <div className="auth-label-row">
              <label className="auth-label" htmlFor="auth-email">Email</label>
            </div>
            <div className="auth-input-wrap">
              <input
                id="auth-email"
                type="email"
                className="auth-input"
                placeholder="you@example.com"
                autoComplete="email"
                autoFocus={isLogin}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
          </div>

          <div className="auth-group">
            <div className="auth-label-row">
              <label className="auth-label" htmlFor="auth-password">Password</label>
              {isLogin && (
                <button
                  type="button"
                  className="auth-forgot"
                  onClick={() => push("Password reset is not enabled", { detail: "Contact your workspace admin.", tone: "info" })}
                >
                  Forgot?
                </button>
              )}
            </div>
            <div className="auth-input-wrap">
              <input
                id="auth-password"
                type={showPassword ? "text" : "password"}
                className="auth-input auth-input-with-icon"
                placeholder={isLogin ? "Your password" : "Min 8 characters"}
                autoComplete={isLogin ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button
                type="button"
                className="auth-eye"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {error && (
            <p role="alert" className="auth-error" style={{ marginBottom: 18 }}>
              {error}
            </p>
          )}

          <button type="submit" className="auth-submit" disabled={busy}>
            {busy ? (isLogin ? "Signing in…" : "Creating account…") : isLogin ? "Sign in" : "Create account"}
          </button>
        </form>

        <div className="auth-footer">
          {isLogin ? (
            <>No account?<Link to="/signup" className="auth-footer-link">Create one</Link></>
          ) : (
            <>Have an account?<Link to="/login" className="auth-footer-link">Sign in</Link></>
          )}
        </div>
      </div>
    </div>
  );
}
