/**
 * Header of the left pane: document identity, Chat / Quiz toggle, upload.
 * One row, soft bottom border, nothing decorative.
 */

import { FileText, Loader2, Plus, Trash2 } from "lucide-react";
import { useRef } from "react";
import type { DocumentInfo } from "../lib/types";

export type Mode = "chat" | "quiz";

interface Props {
  doc: DocumentInfo;
  documents: DocumentInfo[];
  mode: Mode;
  uploading: boolean;
  onMode: (m: Mode) => void;
  onSelect: (doc: DocumentInfo) => void;
  onUpload: (file: File) => void;
  onDelete: (id: string) => void;
}

export function TopBar({ doc, documents, mode, uploading, onMode, onSelect, onUpload, onDelete }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-4">
      {/* Document identity */}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <FileText size={16} strokeWidth={1.75} className="shrink-0 text-ink-muted" />
        {documents.length > 1 ? (
          <select
            value={doc.id}
            onChange={(e) => {
              const next = documents.find((d) => d.id === e.target.value);
              if (next) onSelect(next);
            }}
            className="min-w-0 max-w-[16rem] truncate bg-transparent text-sm font-medium outline-none"
          >
            {documents.map((d) => (
              <option key={d.id} value={d.id}>
                {d.filename}
              </option>
            ))}
          </select>
        ) : (
          <span className="truncate text-sm font-medium" title={doc.filename}>
            {doc.filename}
          </span>
        )}
        <span className="hidden shrink-0 text-[11px] text-ink-muted lg:inline">
          {doc.total_pages} pages · {doc.topics.length} topics · {doc.parser}
        </span>
      </div>

      {/* Mode toggle */}
      <div className="flex shrink-0 rounded-lg border border-line bg-surface-muted p-0.5 text-xs font-medium">
        {(["chat", "quiz"] as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onMode(m)}
            className={`rounded-md px-3 py-1 capitalize transition-colors ${
              mode === m ? "bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]" : "text-ink-muted hover:text-ink"
            }`}
          >
            {m}
          </button>
        ))}
      </div>

      {/* Upload / delete */}
      <div className="flex shrink-0 items-center gap-1">
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
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          title="Upload another PDF"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-surface-muted hover:text-ink disabled:opacity-50"
        >
          {uploading ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
        </button>
        <button
          type="button"
          onClick={() => {
            if (window.confirm(`Remove "${doc.filename}" and its learning data?`)) onDelete(doc.id);
          }}
          title="Remove this document"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-surface-muted hover:text-ink"
        >
          <Trash2 size={15} />
        </button>
      </div>
    </header>
  );
}
