import type * as React from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export type ThemeMode = "dark" | "light" | "system";
export type ResolvedTheme = "dark" | "light";

const KEY = "noteslyt:theme";
const ACCENT_KEY = "noteslyt:accent";
export const DEFAULT_ACCENT = "#22c55e";

function resolve(mode: ThemeMode): ResolvedTheme {
  if (mode !== "system") return mode;
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function hexToRgb(hex: string): [number, number, number] | null {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function shade([r, g, b]: [number, number, number], f: number): string {
  const c = (v: number) => Math.min(255, Math.max(0, Math.round(v * f)));
  return `#${((c(r) << 16) | (c(g) << 8) | c(b)).toString(16).padStart(6, "0")}`;
}

function applyAccent(hex: string): void {
  const rgb = hexToRgb(hex);
  const root = document.documentElement;
  if (!rgb) {
    root.style.removeProperty("--accent");
    root.style.removeProperty("--accent-hover");
    root.style.removeProperty("--accent-soft");
    root.style.removeProperty("--accent-border");
    return;
  }
  const [r, g, b] = rgb;
  root.style.setProperty("--accent", hex);
  root.style.setProperty("--accent-hover", shade(rgb, 0.82));
  root.style.setProperty("--accent-soft", `rgba(${r}, ${g}, ${b}, 0.1)`);
  root.style.setProperty("--accent-border", `rgba(${r}, ${g}, ${b}, 0.35)`);
}

interface ThemeCtx {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  setMode: (m: ThemeMode) => void;
  toggle: () => void;
  accent: string;
  setAccent: (hex: string) => void;
  resetAccent: () => void;
}

const Ctx = createContext<ThemeCtx>({ mode: "dark", resolved: "dark", setMode: () => undefined, toggle: () => undefined, accent: DEFAULT_ACCENT, setAccent: () => undefined, resetAccent: () => undefined });

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => {
    try {
      return (localStorage.getItem(KEY) as ThemeMode) || "dark";
    } catch {
      return "dark";
    }
  });
  const [accent, setAccentState] = useState<string>(() => {
    try {
      const saved = localStorage.getItem(ACCENT_KEY) || "";
      return hexToRgb(saved) ? saved : DEFAULT_ACCENT;
    } catch {
      return DEFAULT_ACCENT;
    }
  });
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolve(mode));

  useEffect(() => {
    const apply = () => {
      const r = resolve(mode);
      setResolved(r);
      document.documentElement.dataset.theme = r;
      document.documentElement.style.colorScheme = r;
    };
    apply();
    applyAccent(accent);
    if (mode === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: light)");
      const fn = () => apply();
      mq.addEventListener("change", fn);
      return () => mq.removeEventListener("change", fn);
    }
  }, [mode, accent]);

  const setMode = useCallback((m: ThemeMode) => {
    setModeState(m);
    try {
      localStorage.setItem(KEY, m);
    } catch {
      /* ignore */
    }
  }, []);

  const toggle = useCallback(() => {
    setModeState((prev) => {
      const cur = resolve(prev);
      const next: ThemeMode = cur === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(KEY, next);
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const setAccent = useCallback((hex: string) => {
    const clean = hex.trim().toLowerCase();
    if (!hexToRgb(clean)) return;
    setAccentState(clean);
    try {
      localStorage.setItem(ACCENT_KEY, clean);
    } catch {
      /* ignore */
    }
  }, []);

  const resetAccent = useCallback(() => {
    setAccentState(DEFAULT_ACCENT);
    try {
      localStorage.removeItem(ACCENT_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo(() => ({ mode, resolved, setMode, toggle, accent, setAccent, resetAccent }), [mode, resolved, setMode, toggle, accent, setAccent, resetAccent]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
