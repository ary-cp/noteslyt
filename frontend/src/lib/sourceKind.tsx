import { BookOpen, FileAudio, FileImage, FileSpreadsheet, FileText, FileVideo, Presentation } from "lucide-react";

export type SourceKind = "pdf" | "docx" | "pptx" | "xlsx" | "csv" | "md" | "txt" | "audio" | "video" | "image" | "other";

/** Viewer class for a stored format id — mirrors `media_kind()` in app/storage.py. */
export function mediaKind(sourceType: string): "pdf" | "video" | "audio" | "image" | "text" | "office" {
  if (sourceType === "video") return "video";
  if (sourceType === "audio") return "audio";
  if (sourceType === "image") return "image";
  if (sourceType === "pdf") return "pdf";
  if (sourceType === "md" || sourceType === "txt" || sourceType === "csv" || sourceType === "tsv") return "text";
  return "office";
}

export function SourceTypeIcon({ type, size = 16 }: { type: string; size?: number }) {
  const cls = "shrink-0 text-[var(--text-secondary)]";
  if (type === "pdf") return <BookOpen size={size} className={cls} />;
  if (type === "video") return <FileVideo size={size} className={cls} />;
  if (type === "audio") return <FileAudio size={size} className={cls} />;
  if (type === "image") return <FileImage size={size} className={cls} />;
  if (type === "pptx") return <Presentation size={size} className={cls} />;
  if (type === "xlsx" || type === "csv" || type === "tsv") return <FileSpreadsheet size={size} className={cls} />;
  return <FileText size={size} className={cls} />;
}

export function statusTone(status: string): "accent" | "danger" | "warning" | "neutral" {
  if (status === "ready") return "accent";
  if (status === "failed") return "danger";
  if (status === "queued" || status === "parsing" || status === "embedding") return "warning";
  return "neutral";
}

export function statusLabel(status: string): string {
  switch (status) {
    case "ready":
      return "Ready";
    case "failed":
      return "Failed";
    case "queued":
      return "Queued";
    case "parsing":
      return "Parsing";
    case "embedding":
      return "Indexing";
    default:
      return status;
  }
}
