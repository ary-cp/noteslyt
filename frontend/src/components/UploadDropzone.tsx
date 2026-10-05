/**
 * Full-screen first-run state: one headline, one dashed drop target.
 * While the backend ingests, we cycle through the pipeline stages so the wait
 * reads as progress rather than a frozen spinner.
 */

import { Loader2, Upload } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent } from "react";

interface Props {
  uploading: boolean;
  error: string | null;
  onUpload: (file: File) => void;
}

const STAGES = ["Parsing pages…", "Chunking by page…", "Embedding chunks…", "Mapping topics…", "Almost there…"];

export function UploadDropzone({ uploading, error, onUpload }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [stage, setStage] = useState(0);

  useEffect(() => {
    if (!uploading) {
      setStage(0);
      return;
    }
    const id = window.setInterval(() => setStage((s) => Math.min(s + 1, STAGES.length - 1)), 2800);
    return () => window.clearInterval(id);
  }, [uploading]);

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file && file.type === "application/pdf") onUpload(file);
  };

  return (
    <main className="flex h-screen items-center justify-center bg-surface px-6">
      <div className="w-full max-w-lg text-center">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">Study Companion</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">Learn from your own slides.</h1>
        <p className="mt-2 text-[15px] text-ink-muted">
          Upload lecture PDFs. Ask anything — every answer cites the page. Then let the adaptive quiz find what you
          haven&rsquo;t mastered yet.
        </p>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => !uploading && inputRef.current?.click()}
          className={`mt-8 flex cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed px-6 py-12 transition-colors ${
            dragging ? "border-accent bg-accent-soft" : "border-line bg-surface-muted hover:border-ink/30"
          } ${uploading ? "cursor-default" : ""}`}
        >
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUpload(f);
              e.target.value = "";
            }}
          />
          {uploading ? (
            <>
              <Loader2 size={22} className="animate-spin text-ink-muted" />
              <p className="mt-3 text-sm font-medium">{STAGES[stage]}</p>
              <p className="mt-1 text-xs text-ink-muted">Firecrawl → page-aware chunks → embeddings → knowledge map</p>
            </>
          ) : (
            <>
              <Upload size={22} strokeWidth={1.5} className="text-ink-muted" />
              <p className="mt-3 text-sm font-medium">Drop a PDF here, or click to browse</p>
              <p className="mt-1 text-xs text-ink-muted">Lecture slides, notes, textbook chapters · up to 50 MB</p>
            </>
          )}
        </div>

        {error && <p className="mt-4 text-sm text-ink-muted">{error}</p>}
      </div>
    </main>
  );
}
