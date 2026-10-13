import { useEffect, useRef, useState, type ReactNode } from "react";

/** Keep every page reachable, but only render previews near the visible rail. */
export function PageRail({ count, selected, onSelect, label = "page", preview }: {
  count: number; selected: number; onSelect: (page: number) => void;
  label?: string; preview: (page: number, onAspect: (ratio: number) => void) => ReactNode;
}) {
  const rail = useRef<HTMLElement>(null);
  useEffect(() => {
    const root = rail.current;
    if (!root) return;
    const reveal = () => {
      const item = root.querySelector<HTMLElement>(`[data-page="${selected}"]`);
      if (!item) return;
      const top = item.offsetTop;
      if (top < root.scrollTop) root.scrollTop = top;
      else if (top + item.offsetHeight > root.scrollTop + root.clientHeight)
        root.scrollTop = top + item.offsetHeight - root.clientHeight;
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(root);
    return () => observer.disconnect();
  }, [selected, count]);
  return <div className="page-navigator">
    <div className="page-rail-heading"><span>{label === "page" ? "Pages" : "Slides"}</span><span>{count}</span></div>
    <nav ref={rail} className="page-rail" aria-label={`${label === "page" ? "Page" : "Slide"} thumbnails`}>
    {Array.from({ length: count }, (_, i) => i + 1).map(n => <RailItem key={n} n={n} selected={n === selected} label={label} onSelect={onSelect} preview={preview} />)}
    </nav>
  </div>;
}

function RailItem({ n, selected, label, onSelect, preview }: {
  n: number; selected: boolean; label: string; onSelect: (n: number) => void; preview: (n: number, onAspect: (ratio: number) => void) => ReactNode;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  const [aspect, setAspect] = useState(label === "slide" ? 16 / 9 : 4 / 3);
  useEffect(() => {
    const item = ref.current;
    const root = item?.parentElement;
    if (!selected || !item || !root) return;
    // PDF metadata can change a placeholder from landscape to portrait.
    // Keep the selected page number visible after that size adjustment.
    if (item.offsetTop < root.scrollTop) root.scrollTop = item.offsetTop;
    else if (item.offsetTop + item.offsetHeight > root.scrollTop + root.clientHeight)
      root.scrollTop = item.offsetTop + item.offsetHeight - root.clientHeight;
  }, [selected, aspect]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { root: el.parentElement, rootMargin: "200px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return <button ref={ref} type="button" data-page={n} aria-label={`Go to ${label} ${n}`} aria-current={selected ? "page" : undefined} onClick={() => onSelect(n)} className="page-rail-item" title={`${label === "page" ? "Page" : "Slide"} ${n}`}>
    <span className="page-rail-preview" style={{ aspectRatio: aspect }}>{visible || selected ? preview(n, setAspect) : null}</span>
    <span className="page-rail-number">{n}</span>
  </button>;
}
