import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { MarkdownBody } from "../chat/ChatPanel";
import { PageRail } from "./PageRail";

export type Slide = { title: string; markdown: string; number?: number };

export function SlideDeck({ slides, page, seekKey }: { slides: Slide[]; page?: number | null; seekKey?: number }) {
  const [index, setIndex] = useState(0);
  const go = (n: number) => setIndex(Math.max(0, Math.min(slides.length - 1, n)));
  useEffect(() => {
    const found = slides.findIndex((slide, i) => (slide.number ?? i + 1) === page);
    if (found >= 0) setIndex(found);
  }, [page, seekKey, slides]);
  const current = Math.min(index, slides.length - 1);
  const slide = slides[current];
  if (!slide) return <p className="p-4 text-sm text-[var(--text-muted)]">No slide text is available.</p>;
  return <section className="slide-deck" tabIndex={0} aria-label="Slide deck" onKeyDown={e => {
    if ((e.target as HTMLElement).matches("input, textarea, select")) return;
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); go(current + 1); }
    if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(current - 1); }
    if (e.key === "Home") { e.preventDefault(); go(0); }
    if (e.key === "End") { e.preventDefault(); go(slides.length - 1); }
  }}>
    <div className="deck-toolbar">
      <button type="button" aria-label="Previous slide" disabled={current === 0} onClick={() => go(current - 1)}><ChevronLeft size={18} /></button>
      <span>Slide {slide.number ?? current + 1} · {current + 1} of {slides.length}</span>
      <button type="button" aria-label="Next slide" disabled={current === slides.length - 1} onClick={() => go(current + 1)}><ChevronRight size={18} /></button>
    </div>
    <div className="document-layout">
      <PageRail count={slides.length} selected={current + 1} onSelect={n => go(n - 1)} label="slide" preview={n => <span className="slide-thumbnail"><strong>{slides[n - 1].title}</strong><span>{slides[n - 1].markdown.replace(/[#*>`]/g, "").slice(0, 160)}</span></span>} />
      <article key={current} className="slide-stage" aria-label={`Slide ${slide.number ?? current + 1}`}>
        <div className="slide-paper"><h2>{slide.title}</h2><MarkdownBody text={slide.markdown} /></div>
      </article>
    </div>
  </section>;
}
