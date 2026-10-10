import { useEffect, useRef, useState } from "react";
import { Download, FileText, X } from "lucide-react";
import { fetchSourceBlob, getSourceContent } from "../../lib/api";
import type { SourceOut, ViewerTarget } from "../../lib/types";
import { mediaKind } from "../../lib/sourceKind";
import { citationLabel, formatDuration, parseTimestamp, timeAgo } from "../../lib/utils";
import { MarkdownBody } from "../chat/ChatPanel";

export function SourceViewerDrawer({
  target,
  sources,
  onClose,
  onRemove,
}: {
  target: ViewerTarget | null;
  sources: SourceOut[];
  onClose: () => void;
  onRemove: (id: string) => void;
}) {
  const source = sources.find((s) => s.id === target?.sourceId) ?? null;
  const [jumpTick, setJumpTick] = useState(0);

  useEffect(() => {
    if (!target) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [target, onClose]);

  if (!target || !source) return null;

  const kind = mediaKind(source.source_type);
  const citedPage = target.kind === "page" ? parsePage(target.value) : null;
  const citedTime = target.kind === "segment" ? (target.value ? parseTimestamp(target.value) : null) : null;
  const citedEnd = target.kind === "segment" && target.end != null && citedTime != null && target.end > citedTime
    ? target.end
    : null;
  const jumpable = (kind === "pdf" && citedPage !== null) || ((kind === "video" || kind === "audio") && citedTime !== null);
  const citedLabel = target.kind === "segment" && citedTime !== null
    ? (citedEnd !== null ? `${formatDuration(citedTime)}–${formatDuration(citedEnd)}` : citationLabel(target.kind, target.value ?? ""))
    : (target.kind && target.value ? citationLabel(target.kind, target.value) : "");

  return (
    <div className="anim-fade fixed inset-0 z-[55] flex justify-end bg-black/50" style={{ zIndex: 55 }} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="anim-rise flex h-full w-full max-w-[560px] flex-col border-l border-[var(--border)] bg-[var(--surface)]" role="dialog" aria-modal="true" aria-label={source.filename}>
        <div className="flex items-start gap-2 border-b border-[var(--border)] px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold">{source.filename}</h2>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              {source.source_type.toUpperCase()} · {source.status === "ready" ? "Ready" : source.status} · {timeAgo(source.created_at)}
              {source.duration_s ? ` · ${formatDuration(source.duration_s)}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
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
            }}
            aria-label="Download source"
            className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-[7px] text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)]"
          >
            <Download size={15} />
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close viewer"
            className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-[7px] text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)]"
          >
            <X size={16} />
          </button>
        </div>
        {target.kind && target.value && (
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--accent-border)] bg-[var(--accent-soft)]/50 px-4 py-1.5 text-[13px]">
            <span className="font-medium text-[var(--accent)]">Cited {citedLabel}</span>
            {jumpable && (
              <button
                type="button"
                onClick={() => setJumpTick((t) => t + 1)}
                className="cursor-pointer rounded-md px-2 py-0.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)]"
              >
                {citedEnd !== null ? "Replay clip" : "Jump to cited position"}
              </button>
            )}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <ViewerBody
            key={source.id}
            source={source}
            locatorKind={target.kind}
            locatorValue={target.value}
            locatorEnd={target.end}
            nonce={target.nonce}
            jumpTick={jumpTick}
          />
          {source.status_detail && source.status !== "ready" && (
            <p className="mt-3 rounded-[8px] border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-3 text-[13px]">
              {source.status_detail}
            </p>
          )}
        </div>
        <div className="flex items-center justify-between border-t border-[var(--border)] px-4 py-2.5">
          <span className="text-xs text-[var(--text-muted)]">{source.chunk_count} indexed {source.chunk_count === 1 ? "passage" : "passages"}</span>
          <button type="button" onClick={() => { onRemove(source.id); onClose(); }} className="cursor-pointer rounded-md px-2 py-1 text-xs text-[var(--danger)] hover:bg-[var(--danger-soft)]">
            Remove from project
          </button>
        </div>
      </div>
    </div>
  );
}

function ViewerBody({
  source,
  locatorKind,
  locatorValue,
  locatorEnd,
  nonce,
  jumpTick,
}: {
  source: SourceOut;
  locatorKind?: string;
  locatorValue?: string;
  locatorEnd?: number | null;
  nonce: number;
  jumpTick: number;
}) {
  const kind = mediaKind(source.source_type);
  // Deep links open on the Original tab: only it can jump (pdf #page fragment,
  // media currentTime). Parsed text has no page/timestamp anchors.
  const seekable =
    (kind === "pdf" && locatorKind === "page" && parsePage(locatorValue) !== null) ||
    ((kind === "video" || kind === "audio") && locatorKind === "segment" && (locatorValue ? parseTimestamp(locatorValue) : null) !== null);
  const [tab, setTab] = useState<"parsed" | "original">(kind === "image" || seekable ? "original" : "parsed");
  useEffect(() => {
    if (seekable) setTab("original");
  }, [seekable, nonce]);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [parsedError, setParsedError] = useState("");
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (kind === "image") return;
    let live = true;
    setMarkdown(null);
    setParsedError("");
    getSourceContent(source.id)
      .then((content) => live && setMarkdown(content.markdown))
      .catch((e: unknown) => live && setParsedError(e instanceof Error ? e.message : String(e)));
    return () => { live = false; };
  }, [source.id, kind]);

  useEffect(() => {
    let live = true;
    let obj: string | null = null;
    setUrl(null);
    setText(null);
    setError("");
    fetchSourceBlob(source.id)
      .then(async (blob) => {
        if (!live) return;
        if (kind === "text") {
          const t = await blob.text();
          if (live) setText(t);
        } else {
          obj = URL.createObjectURL(blob);
          if (live) setUrl(obj);
        }
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
      if (obj) URL.revokeObjectURL(obj);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id]);

  return (
    <div>
      {kind !== "image" && (
        <div className="mb-3 flex gap-1 border-b border-[var(--border)]" role="tablist" aria-label="Source view">
          {([ ["parsed", "Text content"], ["original", "Original"] ] as const).map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
              className={`border-b-2 px-3 py-2 text-[13px] font-medium ${tab === id ? "border-[var(--accent)] text-[var(--accent)]" : "border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]"}`}>
              {label}
            </button>
          ))}
        </div>
      )}
      {kind !== "image" && tab === "parsed" ? (
        markdown !== null ? (
          <div>
            {parsedError && <p className="mb-3 rounded-[8px] bg-[var(--surface-secondary)] p-3 text-xs text-[var(--text-muted)]">{parsedError}</p>}
            <MarkdownBody text={markdown} />
          </div>
        ) : parsedError ? (
          <div className="rounded-[8px] border border-[var(--border)] p-4 text-sm text-[var(--text-secondary)]">
            <p>Parsed content is not available yet.</p>
            <p className="mt-1 text-xs">{parsedError}</p>
            {source.status !== "ready" && <p className="mt-2 text-xs">This source is still processing. The parsed view will be available when parsing finishes.</p>}
          </div>
        ) : <LoadingBlock label="Loading parsed content…" />
      ) : <OriginalBody
        source={source}
        kind={kind}
        url={url}
        text={text}
        error={error}
        page={locatorKind === "page" ? parsePage(locatorValue) : null}
        time={locatorKind === "segment" ? (locatorValue ? parseTimestamp(locatorValue) : null) : null}
        stopAt={locatorKind === "segment" && locatorEnd != null ? locatorEnd : null}
        seekKey={nonce}
        jumpTick={jumpTick}
      />}
    </div>
  );
}

function OriginalBody({
  source,
  kind,
  url,
  text,
  error,
  page,
  time,
  stopAt,
  seekKey,
  jumpTick,
}: {
  source: SourceOut;
  kind: ReturnType<typeof mediaKind>;
  url: string | null;
  text: string | null;
  error: string;
  page: number | null;
  time: number | null;
  stopAt: number | null;
  seekKey: number;
  jumpTick: number;
}) {
  if (error) return <p className="text-[13px] text-[var(--danger)]">{error}</p>;
  if (kind === "text") {
    if (text === null) return <LoadingBlock label="Loading document…" />;
    const isMd = source.source_type === "md";
    return isMd ? <MarkdownBody text={text.slice(0, 60000)} /> : (
      <pre className="rounded-[8px] border border-[var(--border)] bg-[var(--surface-secondary)] p-3.5 font-mono text-[13px] leading-relaxed whitespace-pre-wrap">{text.slice(0, 60000)}</pre>
    );
  }
  if (url === null) return <LoadingBlock label="Loading preview…" />;
  if (kind === "pdf") {
    return <PdfPane url={url} title={source.filename} page={page} seekKey={seekKey} jumpTick={jumpTick} />;
  }
  if (kind === "video") {
    return (
      <div>
        <VideoPane url={url} time={time} stopAt={stopAt} seekKey={seekKey} jumpTick={jumpTick} />
        <TranscriptHint />
      </div>
    );
  }
  if (kind === "audio") {
    return (
      <div className="rounded-[8px] border border-[var(--border)] bg-[var(--surface-secondary)] p-4">
        <div className="mb-3 flex items-center gap-2 text-[var(--text-secondary)]">
          <FileText size={16} /> {source.filename}
        </div>
        <AudioPane url={url} time={time} stopAt={stopAt} seekKey={seekKey} jumpTick={jumpTick} />
        <TranscriptHint />
      </div>
    );
  }
  if (kind === "image") {
    return <img src={url} alt={source.filename} className="max-h-[70vh] w-full rounded-[8px] border border-[var(--border)] object-contain" />;
  }
  return (
    <div className="rounded-[8px] border border-[var(--border)] p-4 text-center">
      <p className="text-sm text-[var(--text-secondary)]">No inline preview for {source.source_type}.</p>
      <a href={url} download={source.filename} className="mt-2 inline-block text-sm text-[var(--accent)] underline underline-offset-2">
        Download {source.filename}
      </a>
    </div>
  );
}

/** "3" → 3; anything else → null (never trust a locator into a URL fragment). */
function parsePage(value?: string): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 ? n : null;
}

/** Browser PDF viewers honor #page=N — the zero-dependency exact-page jump. */
function PdfPane({ url, title, page, seekKey, jumpTick }: { url: string; title: string; page: number | null; seekKey: number; jumpTick: number }) {
  const src = page !== null ? `${url}#page=${page}` : url;
  return (
    <iframe
      key={`${seekKey}-${jumpTick}-${page ?? "all"}`}
      src={src}
      title={title}
      className="h-[70vh] w-full rounded-[8px] border border-[var(--border)] bg-white"
    />
  );
}

function seekMedia(el: HTMLMediaElement | null, time: number) {
  if (!el) return;
  const apply = () => {
    try {
      el.currentTime =
        Number.isFinite(el.duration) && el.duration > 0 ? Math.min(time, Math.max(0, el.duration - 0.25)) : time;
    } catch {
      /* metadata not ready — the loadedmetadata listener below retries */
    }
    void el.play().catch(() => undefined);
  };
  if (el.readyState >= 1) apply();
  else el.addEventListener("loadedmetadata", apply, { once: true });
}

function VideoPane({ url, time, stopAt, seekKey, jumpTick }: { url: string; time: number | null; stopAt: number | null; seekKey: number; jumpTick: number }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (time !== null) seekMedia(ref.current, time);
  }, [url, time, seekKey, jumpTick]);
  useEffect(() => {
    const el = ref.current;
    if (!el || stopAt === null || time === null || stopAt <= time) return;
    const onTime = () => {
      if (el.currentTime >= stopAt) el.pause();
    };
    el.addEventListener("timeupdate", onTime);
    return () => el.removeEventListener("timeupdate", onTime);
  }, [url, time, stopAt, seekKey, jumpTick]);
  return <video ref={ref} src={url} controls preload="metadata" className="w-full rounded-[8px] border border-[var(--border)]" />;
}

function AudioPane({ url, time, stopAt, seekKey, jumpTick }: { url: string; time: number | null; stopAt: number | null; seekKey: number; jumpTick: number }) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    if (time !== null) seekMedia(ref.current, time);
  }, [url, time, seekKey, jumpTick]);
  useEffect(() => {
    const el = ref.current;
    if (!el || stopAt === null || time === null || stopAt <= time) return;
    const onTime = () => {
      if (el.currentTime >= stopAt) el.pause();
    };
    el.addEventListener("timeupdate", onTime);
    return () => el.removeEventListener("timeupdate", onTime);
  }, [url, time, stopAt, seekKey, jumpTick]);
  return <audio ref={ref} src={url} controls preload="metadata" className="w-full" />;
}

function LoadingBlock({ label }: { label: string }) {
  return (
    <div className="grid gap-2" aria-label={label}>
      <div className="h-4 w-2/3 animate-pulse rounded bg-[var(--surface-secondary)]" />
      <div className="h-32 animate-pulse rounded-[8px] bg-[var(--surface-secondary)]" />
      <div className="h-4 w-1/2 animate-pulse rounded bg-[var(--surface-secondary)]" />
    </div>
  );
}

function TranscriptHint() {
  return <p className="mt-2 text-xs text-[var(--text-muted)]">Segments from transcription become clickable citations in chat.</p>;
}
