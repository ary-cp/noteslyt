import { useEffect, useState } from "react";

/** Parse a server timestamp as UTC even when it carries no zone.
 *
 * Postgres timestamptz arrives with +00:00; SQLite TEXT rows arrive as naive
 * UTC ("2026-10-13 07:00:00"). `new Date()` reads the naive form as *local*
 * time, which skews every label by the UTC offset (the stuck "5h ago" in
 * IST). Normalizing here fixes all callers at once.
 */
export function parseServerDate(iso: string | null | undefined): number {
  if (!iso) return Number.NaN;
  let s = iso.trim().replace(" ", "T");
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) s += "Z";
  return new Date(s).getTime();
}

export function timeAgo(iso: string): string {
  const t = parseServerDate(iso);
  if (Number.isNaN(t)) return "";
  const diff = Date.now() - t;
  // Small future skew (server/client clocks) is still "just now".
  const s = Math.max(1, Math.floor(diff / 1000));
  if (s < 0 || s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(t).toLocaleDateString();
}

/** Absolute local time for tooltips, so hovering any "Xm ago" shows the real time. */
export function formatAbsolute(iso: string): string {
  const t = parseServerDate(iso);
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleString();
}

/** Ticks every `ms` so relative labels stay live without a page refresh. */
export function useNow(ms = 30000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

/** Live relative timestamp: recomputed on an interval, not just on data load. */
export function useTimeAgo(iso: string, ms = 30000): string {
  const now = useNow(ms);
  void now;
  return timeAgo(iso);
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDuration(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return "";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  return `${m}:${String(r).padStart(2, "0")}`;
}

/** "12:34" / "1:02:03" → seconds, for clickable segment locators. */
export function parseTimestamp(value: string): number | null {
  const parts = value.split(":").map((p) => Number(p));
  if (parts.some((n) => !Number.isFinite(n) || n < 0) || parts.length < 2 || parts.length > 3) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

export function citationLabel(kind: string, value: string): string {
  switch (kind) {
    case "image":
      return "Image";
    case "page":
      return `p. ${value}`;
    case "slide":
      return `Slide ${value}`;
    case "segment":
      return value;
    case "sheet":
      return `Sheet ${value}`;
    case "row":
      return `Row ${value}`;
    case "section":
      return value || "Section";
    default:
      return value || kind;
  }
}

export function useDebounced<T>(value: T, ms = 200): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    let timer = 0;
    // Fullscreen can cross a responsive breakpoint. Replacing the surrounding
    // layout then unmounts the fullscreen player and immediately exits it.
    // Keep the layout stable until fullscreen ends, then apply the new size.
    const fn = () => {
      window.clearTimeout(timer);
      if (document.fullscreenElement) return;
      // Browsers may dispatch fullscreenchange before restoring viewport size.
      // Let that resize settle rather than briefly mounting the desktop layout.
      timer = window.setTimeout(() => {
        if (!document.fullscreenElement) setMatch(mq.matches);
      }, 100);
    };
    mq.addEventListener("change", fn);
    document.addEventListener("fullscreenchange", fn);
    window.addEventListener("resize", fn);
    fn();
    return () => {
      mq.removeEventListener("change", fn);
      document.removeEventListener("fullscreenchange", fn);
      window.removeEventListener("resize", fn);
      window.clearTimeout(timer);
    };
  }, [query]);
  return match;
}
