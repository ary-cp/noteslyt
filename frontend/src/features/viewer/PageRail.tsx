import { useEffect, useRef, useState, type ReactNode } from "react";

/** Keep every page reachable, but only render previews near the visible rail. */
export function PageRail({ count, selected, onSelect, label = "page", preview }: {
  count: number; selected: number; onSelect: (page: number) => void;
  label?: string; preview: (page: number) => ReactNode;
}) {
  const rail = useRef<HTMLElement>(null);
  useEffect(() => {
    const root = rail.current;
    const item = root?.querySelector<HTMLElement>(`[data-page="${selected}"]`);
    if (!root || !item) return;
    const top = item.offsetTop;
    if (top < root.scrollTop) root.scrollTop = top;
    else if (top + item.offsetHeight > root.scrollTop + root.clientHeight)
      root.scrollTop = top + item.offsetHeight - root.clientHeight;
  }, [selected, count]);
  return <nav ref={rail} className="page-rail" aria-label={`${label === "page" ? "Page" : "Slide"} thumbnails`}>
    {Array.from({ length: count }, (_, i) => i + 1).map(n => <RailItem key={n} n={n} selected={n === selected} label={label} onSelect={onSelect} preview={preview} />)}
  </nav>;
}

function RailItem({ n, selected, label, onSelect, preview }: {
  n: number; selected: boolean; label: string; onSelect: (n: number) => void; preview: (n: number) => ReactNode;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { root: el.parentElement, rootMargin: "200px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return <button ref={ref} type="button" data-page={n} aria-label={`Go to ${label} ${n}`} aria-current={selected ? "page" : undefined} onClick={() => onSelect(n)} className="page-rail-item">
    <span className="page-rail-preview">{visible || selected ? preview(n) : null}</span>
    <span className="page-rail-number">{n}</span>
  </button>;
}
