import { useEffect, useRef } from "react";

export default function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useRef(`mmd-${Math.random().toString(36).slice(2)}`);

  useEffect(() => {
    let live = true;
    import("mermaid")
      .then(({ default: mermaid }) => {
        mermaid.initialize({ startOnLoad: false, theme: document.documentElement.dataset.theme === "light" ? "default" : "dark" });
        return mermaid.render(id.current, code);
      })
      .then(({ svg }) => {
        if (live && ref.current) ref.current.innerHTML = svg;
      })
      .catch(() => {
        if (live && ref.current) {
          ref.current.innerHTML = "";
          const pre = document.createElement("pre");
          pre.className = "rounded border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-3 text-xs";
          pre.textContent = "Diagram could not be rendered. The source is preserved below.";
          ref.current.appendChild(pre);
        }
      });
    return () => {
      live = false;
    };
  }, [code]);

  const download = () => {
    const svg = ref.current?.querySelector("svg")?.outerHTML;
    if (!svg) return;
    const blob = new Blob([svg], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "diagram.svg";
    a.click();
    URL.revokeObjectURL(url);
  };

  const copy = () => {
    navigator.clipboard.writeText(code).catch(() => undefined);
  };

  return (
    <div className="overflow-hidden rounded-[8px] border border-[var(--border)] bg-[var(--surface)]">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-3 py-1.5">
        <span className="font-mono text-xs text-[var(--text-secondary)]">diagram</span>
        <span className="flex gap-1">
          <button type="button" onClick={copy} className="cursor-pointer rounded px-1.5 py-0.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            Copy source
          </button>
          <button type="button" onClick={download} className="cursor-pointer rounded px-1.5 py-0.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            SVG
          </button>
        </span>
      </div>
      <div ref={ref} className="overflow-x-auto p-3" aria-label="Mermaid diagram" />
      <details className="border-t border-[var(--border)] px-3 py-1.5">
        <summary className="cursor-pointer text-xs text-[var(--text-muted)]">View source</summary>
        <pre className="mt-1 overflow-x-auto font-mono text-xs">{code}</pre>
      </details>
    </div>
  );
}
