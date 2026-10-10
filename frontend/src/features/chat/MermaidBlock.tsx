import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2, X, ZoomIn, ZoomOut } from "lucide-react";

/**
 * Renders a mermaid diagram with zoom + fullscreen viewing.
 *
 * Rendering notes:
 * - `initialize()` resets global config, so it is only called when the theme
 *   actually changes (concurrent blocks otherwise race each other).
 * - Every render uses a fresh diagram id; reusing an id throws.
 * - Follows the app theme via a `data-theme` observer so diagrams don't get
 *   stuck in the old theme after a toggle.
 */
let configuredTheme = "";

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;

/**
 * Repairs the systematic breakage LLMs introduce into `mindmap` blocks:
 * node labels split across lines (e.g. `root((Machine Learning 2` newline
 * `Course Topics))`, which parses as a parentless node) and column-0 lines
 * (mindmap children must be indented or they have no parent). Other diagram
 * types pass through untouched.
 */
export function repairMermaid(code: string): string {
  const lines = code.replace(/\r\n?/g, "\n").split("\n");
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  if (!lines.length) return code;
  const kind = lines[0].trim().split(/\s+/)[0].toLowerCase();
  if (kind !== "mindmap") return lines.join("\n");
  // 1. Join labels split across lines: a line with more "(" than ")" continues.
  const parenDepth = (s: string) => (s.match(/\(/g) ?? []).length - (s.match(/\)/g) ?? []).length;
  const joined: string[] = [];
  let pending = "";
  for (const line of lines.slice(1)) {
    const cur = pending ? `${pending} ${line.trim()}` : line;
    if (parenDepth(cur) > 0) {
      pending = cur;
      continue;
    }
    pending = "";
    joined.push(cur);
  }
  if (pending) joined.push(pending);
  // 2. Guarantee one root with everything nested under it: the first content
  //    line is the root (a missing root is added), the root sits at 2 spaces
  //    and every other node nests at 4+, preserving relative indentation for
  //    properly nested input while flattening flat topic lists under the root.
  const isDirective = (line: string) => /^(%%|---)/.test(line.trim());
  const body = [...joined];
  const firstContent = body.findIndex((l) => l.trim() && !isDirective(l));
  if (firstContent >= 0 && !/^\s*root\s*\(/i.test(body[firstContent])) {
    body.splice(firstContent, 0, "root((Diagram))");
  }
  const rootIdx = body.findIndex((l) => l.trim() && !isDirective(l));
  const indentOf = (l: string) => l.match(/^ */)?.[0].length ?? 0;
  const childIndents = body
    .filter((l, i) => i !== rootIdx && l.trim() && !isDirective(l))
    .map(indentOf);
  const minChild = childIndents.length ? Math.min(...childIndents) : 0;
  const fixed = body.map((line, i) => {
    if (!line.trim() || isDirective(line)) return line;
    if (i === rootIdx) return `  ${line.trim()}`;
    const rel = Math.max(0, indentOf(line) - minChild);
    return `${" ".repeat(4 + rel)}${line.trim()}`;
  });
  return ["mindmap", ...fixed].join("\n");
}

function MermaidCanvas({ code, theme, zoom }: { code: string; theme: string; zoom: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const baseId = useRef(`mmd-${Math.random().toString(36).slice(2)}`);
  const seq = useRef(0);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setError("");
    if (ref.current) ref.current.innerHTML = "";
    const renderId = `${baseId.current}-${seq.current++}`;
    import("mermaid")
      .then(({ default: mermaid }) => {
        const mermaidTheme: "default" | "dark" = theme === "light" ? "default" : "dark";
        if (configuredTheme !== mermaidTheme) {
          mermaid.initialize({ startOnLoad: false, theme: mermaidTheme, suppressErrorRendering: true });
          configuredTheme = mermaidTheme;
        }
        return mermaid.render(renderId, code);
      })
      .then(({ svg }) => {
        if (live && ref.current) ref.current.innerHTML = svg;
      })
      .catch((err: unknown) => {
        if (live) setError(err instanceof Error ? err.message : "Diagram could not be rendered.");
      });
    return () => {
      live = false;
    };
  }, [code, theme]);

  if (error) {
    return (
      <div className="m-3 rounded border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-3 text-xs text-[var(--text-secondary)]">
        <p className="font-medium text-[var(--danger)]">Diagram could not be rendered.</p>
        <p className="mt-1 font-mono break-words">{error.slice(0, 300)}</p>
        <p className="mt-1">The source is preserved below — check node names for special characters.</p>
      </div>
    );
  }
  return (
    <div className="overflow-auto">
      <div
        ref={ref}
        className="mermaid-canvas mx-auto p-3"
        style={{ width: `${zoom * 100}%`, minWidth: "100%" }}
        aria-label="Mermaid diagram"
      />
    </div>
  );
}

export default function MermaidBlock({ code }: { code: string }) {
  const [theme, setTheme] = useState(() =>
    typeof document === "undefined" ? "dark" : (document.documentElement.dataset.theme ?? "dark"),
  );
  const [zoom, setZoom] = useState(1);
  const [expanded, setExpanded] = useState(false);
  // Repair LLM mindmap breakage (split root labels, unindented nodes) once;
  // everything below renders, copies and downloads the repaired source.
  const diagram = useMemo(() => repairMermaid(code), [code]);

  useEffect(() => {
    const el = document.documentElement;
    const sync = () => setTheme(el.dataset.theme ?? "dark");
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  // Close fullscreen on Escape.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [expanded]);

  const zoomIn = useCallback(() => setZoom((z) => Math.min(MAX_ZOOM, Math.round((z + ZOOM_STEP) * 100) / 100)), []);
  const zoomOut = useCallback(() => setZoom((z) => Math.max(MIN_ZOOM, Math.round((z - ZOOM_STEP) * 100) / 100)), []);
  const resetZoom = useCallback(() => setZoom(1), []);

  const downloadSVG = useCallback((root: HTMLElement | null) => {
    const svg = root?.querySelector("svg")?.outerHTML;
    if (!svg) return;
    const blob = new Blob([svg], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "diagram.svg";
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  const copy = useCallback(() => {
    navigator.clipboard.writeText(diagram).catch(() => undefined);
  }, [diagram]);

  const toolbar = (inOverlay: boolean) => (
    <>
      <span className="flex items-center gap-0.5" role="group" aria-label="Diagram zoom">
        <button
          type="button"
          onClick={zoomOut}
          disabled={zoom <= MIN_ZOOM}
          aria-label="Zoom out"
          title="Zoom out"
          className="cursor-pointer rounded p-1.5 text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ZoomOut size={14} />
        </button>
        <button
          type="button"
          onClick={resetZoom}
          aria-label="Reset zoom to 100 percent"
          title="Reset zoom"
          className="min-w-[44px] cursor-pointer rounded px-1 py-0.5 text-center font-mono text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          {Math.round(zoom * 100)}%
        </button>
        <button
          type="button"
          onClick={zoomIn}
          disabled={zoom >= MAX_ZOOM}
          aria-label="Zoom in"
          title="Zoom in"
          className="cursor-pointer rounded p-1.5 text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ZoomIn size={14} />
        </button>
      </span>
      <span className="flex items-center gap-1">
        <button
          type="button"
          onClick={(e) => {
            const root = e.currentTarget.closest("[data-mermaid-root]") as HTMLElement | null;
            downloadSVG(root);
          }}
          aria-label="Download diagram as SVG"
          className="cursor-pointer rounded px-1.5 py-0.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          SVG
        </button>
        <button
          type="button"
          onClick={copy}
          className="cursor-pointer rounded px-1.5 py-0.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          Copy source
        </button>
        {!inOverlay && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            aria-label="View diagram full page"
            title="Full page"
            className="cursor-pointer rounded p-1.5 text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]"
          >
            <Maximize2 size={14} />
          </button>
        )}
      </span>
    </>
  );

  return (
    <div data-mermaid-root className="overflow-hidden rounded-[8px] border border-[var(--border)] bg-[var(--surface)]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-1.5">
        <span className="font-mono text-xs text-[var(--text-secondary)]">diagram</span>
        {toolbar(false)}
      </div>
      <MermaidCanvas code={diagram} theme={theme} zoom={zoom} />
      <details className="border-t border-[var(--border)] px-3 py-1.5">
        <summary className="cursor-pointer text-xs text-[var(--text-muted)]">View source</summary>
        <pre className="mt-1 overflow-x-auto font-mono text-xs">{diagram}</pre>
      </details>

      {expanded && (
        <div
          className="anim-fade fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Diagram full page view"
          onClick={(e) => {
            if (e.target === e.currentTarget) setExpanded(false);
          }}
        >
          <div data-mermaid-root className="flex max-h-full w-[min(1200px,96vw)] flex-col overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface)] shadow-2xl">
            <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-2">
              <span className="font-mono text-xs text-[var(--text-secondary)]">diagram · full page</span>
              <span className="flex items-center gap-2">
                {toolbar(true)}
                <button
                  type="button"
                  onClick={() => setExpanded(false)}
                  aria-label="Close full page view"
                  title="Close (Esc)"
                  className="cursor-pointer rounded p-1.5 text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]"
                >
                  <X size={15} />
                </button>
              </span>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              <MermaidCanvas code={diagram} theme={theme} zoom={zoom} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
