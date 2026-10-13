import { useEffect, useRef, useState } from "react";
import { PageRail } from "./PageRail";
import { Document, Page, pdfjs } from "react-pdf";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { ChevronLeft, ChevronRight, Search, RotateCw, PanelLeft, Minus, Plus, Maximize, Minimize, X, Bookmark } from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Bundle the pdf.js worker as a build asset (?url) so the same file is
// served from /assets when FastAPI hosts frontend/dist on port 8000.
// The previous `new URL("pdfjs-dist/...", import.meta.url)` form emitted a
// chunk the dev server rewrote, producing the "Failed to fetch dynamically
// imported module .../pdf.worker.min-*.mjs" fake-worker fallback in prod.
pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
const escapeHtml = (text: string) => text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
type RenderSpec = { page: number; width: number; rotation: number; key: string };

export default function PdfPane({ url, page: citedPage, seekKey, jumpTick }: {
  url: string; page: number | null; seekKey: number; jumpTick: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [bookmarks, setBookmarks] = useState<number[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => { if (searchOpen) searchInput.current?.focus(); }, [searchOpen]);
  const [fullscreen, setFullscreen] = useState(false);
  const [fit, setFit] = useState<"width" | "page">("page");
  const [aspect, setAspect] = useState(.75);
  const [height, setHeight] = useState(window.innerHeight);
  const [pageInput, setPageInput] = useState(String(citedPage ?? 1));
  const [width, setWidth] = useState(600);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const pages = pdf?.numPages ?? 0;
  const [page, setPage] = useState(citedPage ?? 1);
  const [zoom, setZoom] = useState(1);
  const [zoomOpen, setZoomOpen] = useState(false);
  const [rotation, setRotation] = useState(0);
  const [thumbnails, setThumbnails] = useState(true);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<number[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [rendered, setRendered] = useState<RenderSpec | null>(null);
  const pageWidth = Math.round((fit === "page" ? Math.min(width, Math.max(100, height - 20) * aspect) : width) * zoom);
  const spec = { page, width: pageWidth, rotation, key: `${page}-${pageWidth}-${rotation}` };
  const requested = useRef(spec.key);
  requested.current = spec.key;
  const textCache = useRef(new Map<number, string>());
  useEffect(() => { setPageInput(String(page)); if (viewport.current) viewport.current.scrollTop = 0; }, [page]);
  useEffect(() => {
    let live = true;
    void pdf?.getPage(page).then((p) => { const viewport = p.getViewport({ scale: 1, rotation }); if (live) setAspect(viewport.width / viewport.height); }).catch(() => undefined);
    return () => { live = false; };
  }, [pdf, page, rotation]);
  useEffect(() => {
    const update = () => {
      const entered = document.fullscreenElement === container.current;
      setFullscreen(entered);
      if (entered) container.current?.focus({ preventScroll: true });
    };
    document.addEventListener("fullscreenchange", update); window.addEventListener("resize", update);
    return () => { document.removeEventListener("fullscreenchange", update); window.removeEventListener("resize", update); };
  }, []);
  function toggleFullscreen() {
    const action = document.fullscreenElement ? document.exitFullscreen() : container.current?.requestFullscreen();
    void action?.catch(() => setError("Fullscreen is unavailable in this browser."));
  }
  function commitPage() {
    const n = Number(pageInput);
    const target = Number.isInteger(n) && n > 0 ? Math.max(1, Math.min(pages || 1, n)) : page;
    setPageInput(String(target));
    go(target);
  }

  useEffect(() => {
    if (citedPage !== null) setPage(Math.max(1, Math.min(citedPage, pages || citedPage)));
  }, [citedPage, pages, seekKey, jumpTick]);
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    let timer: number;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry.contentRect.width) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        setWidth(Math.max(80, entry.contentRect.width - 20));
        setHeight(entry.contentRect.height);
      }, 100);
    });
    observer.observe(el);
    return () => { observer.disconnect(); window.clearTimeout(timer); };
  }, [pdf]);
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
    if (index < 0) { go(direction > 0 ? hits.find((n) => n > page) ?? hits[0] : [...hits].reverse().find((n) => n < page) ?? hits[hits.length - 1]); return; }
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
  return <div ref={container} className="pdf-reader min-w-0" onKeyDown={(e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") { e.preventDefault(); setSearchOpen(true); searchInput.current?.focus(); return; }
    if (e.key === "Escape" && searchOpen) { setSearchOpen(false); setQuery(""); }
    if ((e.target as HTMLElement).matches("input, select, textarea")) return;
    if (e.key === "Home") { e.preventDefault(); go(1); }
    if (e.key === "End") { e.preventDefault(); go(pages); }
    if (e.key === "ArrowRight") { e.preventDefault(); go(page + 1); }
    if (e.key === "ArrowLeft") { e.preventDefault(); go(page - 1); }
  }} tabIndex={0} aria-label="PDF document">
    <div className="viewer-toolbar sticky top-0 z-20 mb-3 space-y-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-2 shadow-sm">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <button type="button" aria-label="Toggle page thumbnails" aria-pressed={thumbnails} onClick={() => setThumbnails((v) => !v)} className="rounded-md p-2"><PanelLeft size={16} /></button>
        <button type="button" aria-label="Previous page" disabled={page <= 1 || !pages} onClick={() => go(page - 1)} className="rounded-md p-2"><ChevronLeft size={16} /></button>
        <label className="flex items-center gap-1">Page <input aria-label="PDF page" type="number" min={1} max={pages || 1} value={pageInput} onChange={(e) => setPageInput(e.target.value)} onBlur={commitPage} onKeyDown={(e) => { if (e.key === "Enter") commitPage(); }} className="w-12 rounded border border-[var(--border)] bg-[var(--input)] p-1 text-center" /> / {pages || "…"}</label>
        <button type="button" aria-label="Next page" disabled={page >= pages} onClick={() => go(page + 1)} className="rounded-md p-2"><ChevronRight size={16} /></button>
        <button type="button" aria-label="Zoom out" disabled={zoom <= .5} onClick={() => setZoom((n) => Math.max(.5, n - .25))} className="ml-auto rounded-md p-2"><Minus size={15} /></button>
        <button type="button" aria-label="PDF zoom options" aria-expanded={zoomOpen} onClick={() => setZoomOpen((v) => !v)} className="min-w-20 rounded-md border border-[var(--border)] bg-[var(--input)] px-2 py-1.5">
          {zoom === 1 ? fit === "width" ? "Fit width" : "Fit page" : `${Math.round(zoom * 100)}%`}
        </button>
        <button type="button" aria-label="Zoom in" disabled={zoom >= 3} onClick={() => setZoom((n) => Math.min(3, n + .25))} className="rounded-md p-2"><Plus size={15} /></button>
        <button type="button" aria-label="Find in PDF" aria-expanded={searchOpen} onClick={() => { setSearchOpen((v) => !v); setQuery(""); }} className="rounded-md p-2"><Search size={15} /></button>
        <button type="button" aria-label={bookmarks.includes(page) ? "Remove page bookmark" : "Bookmark page"} aria-pressed={bookmarks.includes(page)} disabled={!pages} onClick={() => setBookmarks((items) => items.includes(page) ? items.filter((n) => n !== page) : [...items, page].sort((a,b) => a-b))} className="rounded-md p-2"><Bookmark size={15} fill={bookmarks.includes(page) ? "currentColor" : "none"} /></button>
        <button type="button" aria-label="Rotate page" onClick={() => setRotation((n) => (n + 90) % 360)} className="rounded-md p-2"><RotateCw size={15} /></button>
        <button type="button" aria-label={fullscreen ? "Exit PDF fullscreen" : "Fullscreen PDF"} onClick={toggleFullscreen} className="rounded-md p-2">{fullscreen ? <Minimize size={15} /> : <Maximize size={15} />}</button>
      </div>
      {zoomOpen && <div aria-label="Zoom presets" className="flex flex-wrap gap-1 border-t border-[var(--border)] pt-2">
        {(["width", "page"] as const).map((mode) => <button type="button" key={mode} aria-pressed={zoom === 1 && fit === mode} onClick={() => { setFit(mode); setZoom(1); setZoomOpen(false); }} className="rounded px-2 py-1 text-xs">{mode === "width" ? "Fit width" : "Fit page"}</button>)}
        {[.5, .75, 1.25, 1.5, 2, 3].map((value) => <button type="button" key={value} aria-pressed={zoom === value} onClick={() => { setZoom(value); setZoomOpen(false); }} className="rounded px-2 py-1 text-xs">{value * 100}%</button>)}
      </div>}
      {searchOpen && <div className="viewer-reveal flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--input)] px-2 text-xs">
        <Search size={14} className="shrink-0 text-[var(--text-muted)]" />
        <input ref={searchInput} aria-label="Search PDF" placeholder="Find in document…" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") searchNext(e.shiftKey ? -1 : 1); }} className="min-w-0 flex-1 bg-transparent py-2 outline-none" />
        {query && <><span className="text-[var(--text-muted)]">{searching ? "Searching…" : hits.length ? `${Math.max(0, hits.indexOf(page) + 1)}/${hits.length} pages` : "No matches"}</span><button type="button" aria-label="Previous search result" disabled={!hits.length} onClick={() => searchNext(-1)}><ChevronLeft size={15} /></button><button type="button" aria-label="Next search result" disabled={!hits.length} onClick={() => searchNext(1)}><ChevronRight size={15} /></button><button type="button" aria-label="Clear PDF search" onClick={() => setQuery("")}><X size={14} /></button></>}
      </div>}
    </div>
    {bookmarks.length > 0 && <nav aria-label="Bookmarked pages" className="mb-3 flex flex-wrap gap-1.5">{bookmarks.map((n) => <button type="button" key={n} onClick={() => go(n)} aria-label={`Bookmarked page ${n}`} className="rounded-full border border-[var(--border)] px-2 py-1 text-xs text-[var(--text-secondary)]">Page {n}</button>)}</nav>}
    {citedPage !== null && <button type="button" onClick={() => go(citedPage)} className="mb-2 text-xs text-[var(--accent)]">Cited page {citedPage} · Return to citation</button>}
    {error && <p role="alert" className="mb-2 text-xs text-[var(--danger)]">{error}</p>}
    <div className="pdf-viewport">
    <Document className="document-layout" file={url} onLoadSuccess={(doc) => { textCache.current.clear(); setRendered(null); setPdf(doc); setPage((n) => Math.min(n, doc.numPages)); setError(""); }}
      onLoadError={(e) => setError(e.message)} loading={<p className="min-h-80 p-4 text-sm">Opening document…</p>} error={<p className="p-4 text-sm">Unable to render PDF. Download the source to view it.</p>}>
      {thumbnails && <PageRail count={pages} selected={page} onSelect={go} preview={n => <Page pageNumber={n} width={76} renderTextLayer={false} renderAnnotationLayer={false} loading={null} />} />}
      <div ref={viewport} className="pdf-stage">
      <div className={`pdf-page-surface relative overflow-auto rounded-xl border bg-[#25282e] p-2 ${page === citedPage ? "border-[var(--accent)]" : "border-[var(--border)]"}`} aria-busy={rendered?.key !== spec.key}>
        {[...(rendered && rendered.key !== spec.key ? [rendered] : []), spec].map((item) => <div key={item.key} style={item.key === spec.key && rendered && rendered.key !== spec.key ? { position: "absolute", top: 8, left: 8, opacity: 0, pointerEvents: "none" } : undefined}>
          <Page pageNumber={item.page} width={item.width} rotate={item.rotation} customTextRenderer={renderText}
            onRenderSuccess={() => { if (requested.current === item.key) setRendered((prev) => prev?.key === item.key ? prev : item); }}
            onRenderError={(e) => setError(e.message)} loading={<div className="min-h-80 p-4 text-sm text-white">Rendering page…</div>} />
        </div>)}
        {rendered && rendered.key !== spec.key && <span className="absolute right-3 top-3 rounded bg-black/75 px-2 py-1 text-xs text-white">Rendering…</span>}
      </div>
      </div>
    </Document>
    </div>
  </div>;
}
