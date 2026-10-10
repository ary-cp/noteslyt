import { BookOpen, FileAudio, FileImage, FileSpreadsheet, FileText, FileVideo, Presentation } from "lucide-react";

export type SourceKind = "pdf" | "docx" | "pptx" | "xlsx" | "csv" | "md" | "txt" | "audio" | "video" | "image" | "other";

/** Viewer class for a stored format id — mirrors `media_kind()` in app/storage.py. */
export function mediaKind(sourceType: string): "pdf" | "video" | "audio" | "image" | "text" | "office" {
  if (sourceType === "video" || sourceType === "mp4" || sourceType === "webm") return "video";
  if (sourceType === "audio" || sourceType === "mp3" || sourceType === "wav") return "audio";
  if (sourceType === "image" || sourceType === "png" || sourceType === "jpg" || sourceType === "jpeg" || sourceType === "webp") return "image";
  if (sourceType === "pdf") return "pdf";
  if (sourceType === "md" || sourceType === "txt" || sourceType === "csv" || sourceType === "tsv") return "text";
  return "office";
}

export function SourceTypeIcon({ type, size = 16 }: { type: string; size?: number }) {
  const cls = "shrink-0 text-[var(--text-secondary)]";
  const kind = mediaKind(type);
  if (kind === "pdf") return <BookOpen size={size} className={cls} />;
  if (kind === "video") return <FileVideo size={size} className={cls} />;
  if (kind === "audio") return <FileAudio size={size} className={cls} />;
  if (kind === "image") return <FileImage size={size} className={cls} />;
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
      return "Processing";
    case "embedding":
      return "Indexing";
    default:
      return status;
  }
}
