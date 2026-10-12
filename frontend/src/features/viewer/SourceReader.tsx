/**
 * SourceReader — the left sidebar in reading mode.
 *
 * The sidebar is the single home for sources: the library list (sources +
 * chats) and, once a source is opened, its parsed text with cited lines
 * highlighted. No right-side popup: citation clicks land here, scrolled to
 * the cited block. Audio/video pin a compact player above the transcript.
 */

import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Download, FileText } from "lucide-react";
import { fetchSourceBlob, getSourceContent } from "../../lib/api";
import type { SourceOut, ViewerTarget } from "../../lib/types";
import { mediaKind } from "../../lib/sourceKind";
import { citationLabel, formatDuration, parseTimestamp, timeAgo } from "../../lib/utils";
import {
  enhanceTableMath,
  findPageAnchor,
  highlightSnippet,
  scrollElIntoView,
  spliceCiteMarkers,
} from "../../lib/highlight";
import MediaPane from "./MediaPane";
const PdfPane = lazy(() => import("./PdfPane"));
import { MarkdownBody } from "../chat/ChatPanel";

/** "3" → 3; anything else → null (never trust a locator into navigation). */
function parsePage(value?: string): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 ? n : null;
}

/**
 * Parsed markdown with the cited chunk lines highlighted and scrolled into
 * view. Targeting is deterministic (see spliceCiteMarkers); a DOM-level
 * highlight runs as backup for spans unsafe to wrap in source.
 */
function CitedMarkdown({
  text,
  snippet,
  page,
  nonce,
  jumpTick,
}: {
  text: string;
  snippet: string | null;
  page: number | null;
  nonce: number;
  jumpTick: number;
}) {
  const marked = useMemo(() => spliceCiteMarkers(text, snippet, page), [text, snippet, page]);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const attempt = () => {
      enhanceTableMath(root);
      let el = root.querySelector<HTMLElement>("#cite-target");
      if (el && el.tagName !== "MARK" && snippet) {
        el = highlightSnippet(root, snippet, "md-cite-mark") ?? el;
      }
      el ??= page !== null ? findPageAnchor(root, page) : null;
      if (el) {
        scrollElIntoView(el, false);
        return true;
      }
      return false;
    };
    // Re-apply after markdown layout settles. On first open, images/math can
    // change the parsed pane height after its initial render and offset a seek.
    const seek = () => { if (ref.current) attempt(); };
    const frame = requestAnimationFrame(seek);
    const retries = [150, 400, 800, 1400].map((delay) => window.setTimeout(seek, delay));
    return () => {
      cancelAnimationFrame(frame);
      retries.forEach(window.clearTimeout);
    };
  }, [marked, snippet, page, nonce, jumpTick]);
  return (
    <div ref={ref}>
      <MarkdownBody text={marked.text} />
    </div>
  );
}

export function SourceReader({
  source,
  target,
  onBack,
}: {
  source: SourceOut;
  target: ViewerTarget | null;
  onBack: () => void;
}) {
  const kind = mediaKind(source.source_type);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [rawText, setRawText] = useState<string | null>(null);
  const [error, setError] = useState("");

  const citedPage = target?.kind === "page" ? parsePage(target.value) : null;
  // Image-kind citations from rendered documents carry the page number as
  // their value — anchor the reader to that page's marker line.
  const citedImagePage =
    target?.kind === "image" && (source.source_type === "pdf" || source.source_type === "pptx")
      ? parsePage(target.value)
      : null;
  const anchorPage = citedPage ?? citedImagePage;
  const citedTime = target?.kind === "segment" ? (target.value ? parseTimestamp(target.value) : null) : null;
  const citedEnd =
    target?.kind === "segment" && target.end != null && citedTime !== null && target.end > citedTime
      ? target.end
      : null;
  const citedLabel =
    target?.kind === "segment" && citedTime !== null
      ? `${formatDuration(citedTime)}${citedEnd !== null ? `–${formatDuration(citedEnd)}` : ""}`
      : target?.kind && target?.value
        ? citationLabel(target.kind, target.value)
        : "";

  // Parsed / Original tabs (pdf + audio/video have both views). Citations open the original at the cited page or playback position.
  const hasOriginal = kind === "pdf" || kind === "video" || kind === "audio";
  const [tab, setTab] = useState<"parsed" | "original">(hasOriginal ? "original" : "parsed");
  const prevTarget = useRef(target?.nonce ?? -1);
  useEffect(() => {
    const n = target?.nonce ?? -1;
    if (prevTarget.current === n) return;
    prevTarget.current = n;
    setTab(hasOriginal ? "original" : "parsed");
  });

  useEffect(() => {
    if (kind === "image") return;
    let live = true;
    setMarkdown(null);
    getSourceContent(source.id)
      .then((content) => live && setMarkdown(content.markdown))
      .catch(() => live && setMarkdown(null));
    return () => {
      live = false;
    };
  }, [source.id, kind]);

  useEffect(() => {
    if (kind !== "video" && kind !== "audio" && kind !== "image" && kind !== "text" && kind !== "pdf") return;
    let live = true;
    let obj: string | null = null;
    setBlobUrl(null);
    setRawText(null);
    setError("");
    fetchSourceBlob(source.id)
      .then(async (blob) => {
        if (!live) return;
        if (kind === "text") {
          const t = await blob.text();
          if (live) setRawText(t);
        } else {
          obj = URL.createObjectURL(blob);
          if (live) setBlobUrl(obj);
        }
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
      if (obj) URL.revokeObjectURL(obj);
    };
  }, [source.id, kind]);

  const download = () => {
    fetchSourceBlob(source.id)
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = source.filename;
        a.click();
        URL.revokeObjectURL(url);
      })
      .catch(() => undefined);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" aria-label={`Reading ${source.filename}`}>
      {/* reader header */}
      <div className="shrink-0 border-b border-[var(--border)] px-3 pt-3 pb-2.5">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onBack}
            aria-label="Back to sources"
            title="Back to sources"
            className="inline-flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-[7px] text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]"
          >
            <ArrowLeft size={16} />
          </button>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[13px] font-semibold text-[var(--text-primary)]" title={source.filename}>
              {source.filename}
            </h2>
            <p className="text-xs text-[var(--text-muted)]">
              {source.source_type.toUpperCase()} · {source.status === "ready" ? "Ready" : source.status} · {timeAgo(source.created_at)}
            </p>
          </div>
          <button
            type="button"
            onClick={download}
            aria-label="Download source"
            title="Download source"
            className="inline-flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-[7px] text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]"
          >
            <Download size={15} />
          </button>
        </div>
        {citedLabel && (
          <p className="mt-2 rounded-[7px] border border-[var(--accent-border)] bg-[var(--accent-soft)]/50 px-2.5 py-1 text-[13px] font-medium text-[var(--accent)]">
            Cited {citedLabel}
          </p>
        )}
      </div>

      {/* reader body */}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {kind === "image" ? (
          blobUrl ? (
            <img src={blobUrl} alt={source.filename} className="w-full rounded-[8px] border border-[var(--border)] object-contain" />
          ) : (
            <ReaderLoading label="Loading image…" />
          )
        ) : kind === "text" ? (
          rawText !== null ? (
            source.source_type === "md" ? (
              <MarkdownBody text={rawText.slice(0, 60000)} />
            ) : (
              <pre className="rounded-[8px] border border-[var(--border)] bg-[var(--surface-secondary)] p-3.5 font-mono text-[13px] leading-relaxed whitespace-pre-wrap">
                {rawText.slice(0, 60000)}
              </pre>
            )
          ) : (
            <ReaderLoading label="Loading document…" />
          )
        ) : markdown !== null || hasOriginal ? (
          <div>
            {hasOriginal && (
              <div className="mb-3 flex gap-1 border-b border-[var(--border)]" role="tablist" aria-label="Source view">
                {(["parsed", "original"] as const).map((id) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={tab === id}
                    onClick={() => setTab(id)}
                    className={`cursor-pointer border-b-2 px-3 py-2 text-[13px] font-medium ${
                      tab === id
                        ? "border-[var(--accent)] text-[var(--accent)]"
                        : "border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                    }`}
                  >
                    {id === "parsed" ? "Parsed text" : "Original file"}
                  </button>
                ))}
              </div>
            )}
            <div hidden={tab !== "parsed" && hasOriginal}>
              <CitedMarkdown text={markdown ?? ""} snippet={target?.snippet ?? null} page={anchorPage} nonce={target?.nonce ?? 0} jumpTick={0} />
            </div>
            {hasOriginal && <div hidden={tab !== "original"}>
              <OriginalPane key={source.id} source={source} kind={kind} url={blobUrl} page={anchorPage} time={citedTime} stopAt={citedEnd}
                seekKey={target?.nonce ?? 0} active={tab === "original"} markdown={markdown ?? ""} snippet={target?.snippet} />
            </div>}
          </div>
        ) : (
          <div className="rounded-[8px] border border-[var(--border)] p-4 text-sm text-[var(--text-secondary)]">
            <p className="flex items-center gap-2">
              <FileText size={15} /> Parsed text is not available yet.
            </p>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              {source.status !== "ready"
                ? "This source is still processing — its text appears here when parsing finishes."
                : error || "Nothing to show."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/** Original file view inside the sidebar: PDF page, media player. */
function OriginalPane({ source, kind, url, page, time, stopAt, seekKey, active, markdown, snippet }: {
  source: SourceOut; kind: "pdf" | "video" | "audio"; url: string | null; page: number | null;
  time: number | null; stopAt: number | null; seekKey: number; active: boolean; markdown: string; snippet?: string;
}) {
  if (!url) return <ReaderLoading label="Loading file…" />;
  if (kind === "pdf") return <Suspense fallback={<ReaderLoading label="Opening PDF…" />}><PdfPane url={url} page={page} seekKey={seekKey} jumpTick={0} /></Suspense>;
  return <MediaPane title={source.filename} active={active} url={url} video={kind === "video"} time={time} stopAt={stopAt} seekKey={seekKey} jumpTick={0} markdown={markdown} snippet={snippet} />;
}

function ReaderLoading({ label }: { label: string }) {
  return (
    <div className="grid gap-2" aria-label={label}>
      <div className="h-4 w-2/3 animate-pulse rounded bg-[var(--surface-secondary)]" />
      <div className="h-32 animate-pulse rounded-[8px] bg-[var(--surface-secondary)]" />
      <div className="h-4 w-1/2 animate-pulse rounded bg-[var(--surface-secondary)]" />
    </div>
  );
}
