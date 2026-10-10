import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { ChevronLeft, ChevronRight, Search, RotateCw, PanelLeft, Minus, Plus } from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
const escapeHtml = (text: string) => text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
type RenderSpec = { page: number; width: number; rotation: number; key: string };

export default function PdfPane({ url, page: citedPage, seekKey, jumpTick }: {
  url: string; page: number | null; seekKey: number; jumpTick: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const pages = pdf?.numPages ?? 0;
  const [page, setPage] = useState(citedPage ?? 1);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [thumbnails, setThumbnails] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<number[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [rendered, setRendered] = useState<RenderSpec | null>(null);
  const spec = { page, width: Math.round(width * zoom), rotation, key: `${page}-${Math.round(width * zoom)}-${rotation}` };
  const requested = useRef(spec.key);
  requested.current = spec.key;
  const textCache = useRef(new Map<number, string>());
  useEffect(() => {
    if (citedPage !== null) setPage(Math.max(1, Math.min(citedPage, pages || citedPage)));
  }, [citedPage, pages, seekKey, jumpTick]);
  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(180, entry.contentRect.width - 20)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let live = true;
    const needle = query.trim().toLowerCase();
    setHits([]);
    if (!pdf || !needle) { setSearching(false); return; }
    setSearching(true);
    const timer = window.setTimeout(async () => {
      const found: number[] = [];
      try {
        for (let n = 1; n <= pdf.numPages && live; n++) {
          let text = textCache.current.get(n);
          if (text === undefined) {
            const documentPage = await pdf.getPage(n);
            const content = await documentPage.getTextContent();
            text = content.items.map((item) => "str" in item ? item.str : "").join(" ").toLowerCase();
            textCache.current.set(n, text);
          }
          if (text.includes(needle)) found.push(n);
        }
        if (live) { setHits(found); if (found.length) setPage(found[0]); }
      } catch { if (live) setError("Some pages could not be searched."); }
      finally { if (live) setSearching(false); }
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [pdf, query]);
  function go(n: number) { setPage(Math.max(1, Math.min(pages || 1, n))); }
  function searchNext(direction: number) {
    if (!hits.length) return;
    const index = hits.indexOf(page);
    go(hits[(index + direction + hits.length) % hits.length]);
  }
  function renderText({ str }: { str: string }) {
    const needle = query.trim().toLowerCase();
    if (!needle) return escapeHtml(str);
    let cursor = 0; let html = "";
    for (let index = str.toLowerCase().indexOf(needle); index >= 0; index = str.toLowerCase().indexOf(needle, cursor)) {
      html += escapeHtml(str.slice(cursor, index)) + '<mark class="pdf-search-hit">' + escapeHtml(str.slice(index, index + needle.length)) + '</mark>';
      cursor = index + needle.length;
    }
    return html + escapeHtml(str.slice(cursor));
  }
  const previewPages = Array.from({ length: Math.min(9, pages) }, (_, n) => Math.max(1, Math.min(page - 4, pages - 8)) + n);
  return <div ref={container} className="min-w-0" onKeyDown={(e) => {
    if ((e.target as HTMLElement).matches("input, select, textarea")) return;
    if (e.key === "ArrowRight") { e.preventDefault(); go(page + 1); }
    if (e.key === "ArrowLeft") { e.preventDefault(); go(page - 1); }
  }} tabIndex={0} aria-label="PDF document">
    <div className="viewer-toolbar sticky top-0 z-20 mb-3 space-y-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-2 shadow-sm">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <button type="button" aria-label="Toggle page thumbnails" aria-pressed={thumbnails} onClick={() => setThumbnails((v) => !v)} className="rounded-md p-2"><PanelLeft size={16} /></button>
        <button type="button" aria-label="Previous page" disabled={page <= 1 || !pages} onClick={() => go(page - 1)} className="rounded-md p-2"><ChevronLeft size={16} /></button>
        <label className="flex items-center gap-1">Page <input aria-label="PDF page" type="number" min={1} max={pages || 1} value={page} onChange={(e) => go(Number(e.target.value) || 1)} className="w-12 rounded border border-[var(--border)] bg-[var(--input)] p-1 text-center" /> / {pages || "…"}</label>
        <button type="button" aria-label="Next page" disabled={page >= pages} onClick={() => go(page + 1)} className="rounded-md p-2"><ChevronRight size={16} /></button>
        <button type="button" aria-label="Zoom out" disabled={zoom <= .5} onClick={() => setZoom((n) => Math.max(.5, n - .25))} className="ml-auto rounded-md p-2"><Minus size={15} /></button>
        <button type="button" title="Reset to fit width" onClick={() => setZoom(1)} className="rounded-md px-2 py-1">{zoom === 1 ? "Fit width" : `${Math.round(zoom * 100)}%`}</button>
        <button type="button" aria-label="Zoom in" disabled={zoom >= 3} onClick={() => setZoom((n) => Math.min(3, n + .25))} className="rounded-md p-2"><Plus size={15} /></button>
        <button type="button" aria-label="Rotate page" onClick={() => setRotation((n) => (n + 90) % 360)} className="rounded-md p-2"><RotateCw size={15} /></button>
      </div>
      <div className="flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--input)] px-2 text-xs">
        <Search size={14} className="shrink-0 text-[var(--text-muted)]" />
        <input aria-label="Search PDF" placeholder="Find in document…" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") searchNext(e.shiftKey ? -1 : 1); }} className="min-w-0 flex-1 bg-transparent py-2 outline-none" />
        {query && <><span className="text-[var(--text-muted)]">{searching ? "Searching…" : hits.length ? `${Math.max(0, hits.indexOf(page) + 1)}/${hits.length} pages` : "No matches"}</span><button type="button" aria-label="Previous search result" disabled={!hits.length} onClick={() => searchNext(-1)}><ChevronLeft size={15} /></button><button type="button" aria-label="Next search result" disabled={!hits.length} onClick={() => searchNext(1)}><ChevronRight size={15} /></button></>}
      </div>
    </div>
    {citedPage !== null && <button type="button" onClick={() => go(citedPage)} className="mb-2 text-xs text-[var(--accent)]">Cited page {citedPage} · Return to citation</button>}
    {error && <p role="alert" className="mb-2 text-xs text-[var(--danger)]">{error}</p>}
    <Document file={url} onLoadSuccess={(doc) => { setPdf(doc); setPage((n) => Math.min(n, doc.numPages)); setError(""); }}
      onLoadError={(e) => setError(e.message)} loading={<p className="min-h-80 p-4 text-sm">Opening document…</p>} error={<p className="p-4 text-sm">Unable to render PDF. Download the source to view it.</p>}>
      {thumbnails && <div className="mb-3 flex gap-2 overflow-x-auto rounded-lg bg-[var(--surface-secondary)] p-2" aria-label="Page thumbnails">
        {previewPages.map((n) => <button type="button" key={n} aria-label={`Go to page ${n}`} aria-current={page === n ? "page" : undefined} onClick={() => go(n)} className={`shrink-0 rounded-md border-2 p-1 text-xs ${page === n ? "border-[var(--accent)]" : "border-transparent"}`}><Page pageNumber={n} width={70} renderTextLayer={false} renderAnnotationLayer={false} loading={<div className="h-24 w-[70px]" />} /><span>{n}</span></button>)}
      </div>}
      <div className={`relative overflow-auto rounded-xl border bg-[#25282e] p-2 ${page === citedPage ? "border-[var(--accent)]" : "border-[var(--border)]"}`} aria-busy={rendered?.key !== spec.key}>
        {[...(rendered && rendered.key !== spec.key ? [rendered] : []), spec].map((item) => <div key={item.key} style={item.key === spec.key && rendered && rendered.key !== spec.key ? { position: "absolute", top: 8, left: 8, opacity: 0, pointerEvents: "none" } : undefined}>
          <Page pageNumber={item.page} width={item.width} rotate={item.rotation} customTextRenderer={renderText}
            onRenderSuccess={() => { if (requested.current === item.key) setRendered((prev) => prev?.key === item.key ? prev : item); }}
            onRenderError={(e) => setError(e.message)} loading={<div className="min-h-80 p-4 text-sm text-white">Rendering page…</div>} />
        </div>)}
        {rendered && rendered.key !== spec.key && <span className="absolute right-3 top-3 rounded bg-black/75 px-2 py-1 text-xs text-white">Rendering…</span>}
      </div>
    </Document>
  </div>;
}
