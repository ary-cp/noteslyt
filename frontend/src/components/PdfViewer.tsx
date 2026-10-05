/**
 * Right pane: continuous-scroll PDF viewer built on react-pdf (pdf.js).
 *
 * Responsibilities
 *   • Fit pages to the pane width; zoom with − / + (50 % – 250 %).
 *   • Track the page currently in view (IntersectionObserver) for the toolbar.
 *   • Lazy-render: only pages near the viewport get a canvas; the rest are
 *     fixed-height placeholders, so a 100-slide deck stays smooth.
 *   • `target` is the citation bridge: whenever App sets {page, nonce} we
 *     scroll that page into view and flash an accent ring around it.
 */

import { ChevronDown, ChevronUp, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

// Vite-friendly worker: bundled from node_modules, no CDN, works offline.
pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

export interface PageTarget {
  page: number;
  nonce: number; // changes on every click so re-citing the same page re-scrolls
}

interface Props {
  fileUrl: string;
  target: PageTarget | null;
}

const PAD = 24; // horizontal padding around pages, px
const ZOOM_STEPS = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 2, 2.5];

export function PdfViewer({ fileUrl, target }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());
  const ratios = useRef(new Map<number, number>()); // latest visible fraction per page

  const [numPages, setNumPages] = useState(0);
  const [containerWidth, setContainerWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [currentPage, setCurrentPage] = useState(1);
  const [aspect, setAspect] = useState(4 / 3); // width / height, refined after page 1 loads
  const [visible, setVisible] = useState<Set<number>>(new Set([1, 2, 3]));
  const [flash, setFlash] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Memoised so react-pdf doesn't re-fetch on every render.
  const file = useMemo(() => ({ url: fileUrl }), [fileUrl]);
  const pageWidth = Math.max(200, Math.floor((containerWidth - PAD * 2) * zoom));

  // ---- fit to pane width ---------------------------------------------------
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ---- which pages are (nearly) on screen -----------------------------------
  useEffect(() => {
    const root = containerRef.current;
    if (!root || numPages === 0) return;

    const near = new IntersectionObserver(
      (entries) => {
        setVisible((prev) => {
          const next = new Set(prev);
          for (const e of entries) {
            const n = Number((e.target as HTMLElement).dataset.page);
            if (e.isIntersecting) next.add(n);
            else next.delete(n);
          }
          return next;
        });
      },
      { root, rootMargin: "1200px 0px" },
    );
    // Observers only report pages whose visibility *changed*, so we keep the
    // latest ratio of every page and pick the global maximum each time.
    const inView = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          ratios.current.set(Number((e.target as HTMLElement).dataset.page), e.isIntersecting ? e.intersectionRatio : 0);
        }
        let bestPage = 0;
        let bestRatio = -1;
        for (const [p, r] of ratios.current) {
          if (r > bestRatio || (r === bestRatio && p < bestPage)) {
            bestPage = p;
            bestRatio = r;
          }
        }
        if (bestPage) setCurrentPage(bestPage);
      },
      { root, threshold: [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1] },
    );
    for (const el of pageRefs.current.values()) {
      near.observe(el);
      inView.observe(el);
    }
    return () => {
      near.disconnect();
      inView.disconnect();
    };
  }, [numPages]);

  // ---- citation bridge: scroll + flash ---------------------------------------
  const scrollToPage = useCallback((page: number, smooth = true) => {
    const el = pageRefs.current.get(page);
    const root = containerRef.current;
    if (!el || !root) return;
    // Align the page's top edge PAD px below the toolbar (scrollIntoView would
    // tuck it under the container padding).
    const top = el.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop - PAD;
    root.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
    setFlash(page);
    window.setTimeout(() => setFlash((f) => (f === page ? null : f)), 1700);
  }, []);

  useEffect(() => {
    if (!target || numPages === 0) return;
    const page = Math.min(Math.max(1, target.page), numPages);
    // next frame so placeholders exist even right after load
    const id = requestAnimationFrame(() => scrollToPage(page));
    return () => cancelAnimationFrame(id);
  }, [target, numPages, scrollToPage]);

  const step = (dir: 1 | -1) =>
    setZoom((z) => {
      const i = ZOOM_STEPS.findIndex((s) => Math.abs(s - z) < 1e-6);
      const next = ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, Math.max(0, (i === -1 ? 4 : i) + dir))];
      return next;
    });

  const registerPage = (n: number) => (el: HTMLDivElement | null) => {
    if (el) pageRefs.current.set(n, el);
    else pageRefs.current.delete(n);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Toolbar */}
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line bg-surface px-4 text-sm">
        <div className="flex items-center gap-1 text-ink-muted">
          <button
            type="button"
            className="rounded-md p-1 hover:bg-surface-muted disabled:opacity-30"
            onClick={() => scrollToPage(Math.max(1, currentPage - 1))}
            disabled={currentPage <= 1}
            aria-label="Previous page"
          >
            <ChevronUp size={16} />
          </button>
          <span className="tabular-nums">
            Page <span className="text-ink">{numPages ? currentPage : "–"}</span> / {numPages || "–"}
          </span>
          <button
            type="button"
            className="rounded-md p-1 hover:bg-surface-muted disabled:opacity-30"
            onClick={() => scrollToPage(Math.min(numPages, currentPage + 1))}
            disabled={currentPage >= numPages}
            aria-label="Next page"
          >
            <ChevronDown size={16} />
          </button>
        </div>
        <div className="flex items-center gap-1 text-ink-muted">
          <button type="button" className="rounded-md p-1 hover:bg-surface-muted" onClick={() => step(-1)} aria-label="Zoom out">
            <Minus size={16} />
          </button>
          <span className="w-12 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
          <button type="button" className="rounded-md p-1 hover:bg-surface-muted" onClick={() => step(1)} aria-label="Zoom in">
            <Plus size={16} />
          </button>
        </div>
      </div>

      {/* Pages */}
      <div ref={containerRef} className="min-h-0 flex-1 overflow-auto bg-surface-muted" style={{ padding: PAD }}>
        {loadError && <p className="text-center text-sm text-ink-muted">{loadError}</p>}
        <Document
          file={file}
          onLoadSuccess={({ numPages: n }) => {
            setNumPages(n);
            setLoadError(null);
          }}
          onLoadError={(e) => setLoadError(`Could not load the PDF: ${e.message}`)}
          loading={<p className="text-center text-sm text-ink-muted">Loading PDF…</p>}
          error={null}
          className="flex flex-col items-center gap-4"
        >
          {Array.from({ length: numPages }, (_, i) => i + 1).map((n) => (
            <div
              key={n}
              ref={registerPage(n)}
              data-page={n}
              className={`relative rounded-sm bg-white shadow-[0_1px_3px_rgba(0,0,0,0.08),0_0_0_1px_rgba(0,0,0,0.04)] ${flash === n ? "page-flash" : ""}`}
              style={{ width: pageWidth, minHeight: Math.round(pageWidth / aspect) }}
            >
              {visible.has(n) && containerWidth > 0 && (
                <Page
                  pageNumber={n}
                  width={pageWidth}
                  renderAnnotationLayer={false}
                  renderTextLayer
                  loading={null}
                  onLoadSuccess={(p) => {
                    if (n === 1) setAspect(p.originalWidth / p.originalHeight);
                  }}
                />
              )}
              <span className="pointer-events-none absolute bottom-2 right-2 rounded bg-ink/60 px-1.5 py-0.5 text-[10px] font-medium text-white">
                {n}
              </span>
            </div>
          ))}
        </Document>
      </div>
    </div>
  );
}
