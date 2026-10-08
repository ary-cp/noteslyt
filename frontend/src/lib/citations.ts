/**
 * Citation helpers — generalized over locator kinds (Phase D.19).
 *
 * The backend emits one label form per locator:
 * `[Page 14] [Slide 7] [12:34] [Sheet Costs] [Section Laws] [Row 2:51]
 * [Image 1] [Segment 0:45]`. Before Markdown is rendered each one is
 * rewritten into a link with a sentinel href (`#cite=<label>`);
 * `MarkdownMessage` renders those links as chips and the click handler
 * jumps the viewer (PDF page, slide, media timestamp, …) instead of
 * navigating.
 */

export const CITATION_RE =
  /\[(Page [^\]]+|Slide [^\]]+|Sheet [^\]]+|Section [^\]]+|Row [^\]]+|Image [^\]]+|Segment [^\]]+|\d{1,3}:\d{2}(?::\d{2})?)\]/g;
export const CITATION_HREF_PREFIX = "#cite=";
/** Pre-D.19 hrefs used `#page=N`; still recognised. */
const LEGACY_HREF_PREFIX = "#page=";

export function linkifyCitations(markdown: string): string {
  return markdown.replace(
    CITATION_RE,
    (_m, label: string) => `[${label}](${CITATION_HREF_PREFIX}${encodeURIComponent(label)})`,
  );
}

export interface CitationTarget {
  kind: string;
  value: string;
}

/** Parse a citation href back into its locator. Handles legacy `#page=N`. */
export function citationTarget(href: string | undefined): CitationTarget | null {
  if (!href) return null;
  if (href.startsWith(LEGACY_HREF_PREFIX)) {
    const page = Number.parseInt(href.slice(LEGACY_HREF_PREFIX.length), 10);
    return Number.isFinite(page) && page > 0 ? { kind: "page", value: String(page) } : null;
  }
  if (!href.startsWith(CITATION_HREF_PREFIX)) return null;
  const label = decodeURIComponent(href.slice(CITATION_HREF_PREFIX.length));
  const prefixed = /^(Page|Slide|Sheet|Section|Row|Image|Segment)\s+(.+)$/i.exec(label);
  if (prefixed) return { kind: prefixed[1].toLowerCase(), value: prefixed[2].trim() };
  if (/^\d{1,3}:\d{2}(?::\d{2})?$/.test(label)) return { kind: "segment", value: label };
  return null;
}

/** Canonical label for a locator — mirrors `citation_label()` in app/citations.py. */
export function citationLabel(kind: string, value: string): string {
  const k = kind.toLowerCase();
  if (k === "page") return `[Page ${value}]`;
  if (k === "slide") return `[Slide ${value}]`;
  if (k === "sheet") return `[Sheet ${value}]`;
  if (k === "section") return `[Section ${value}]`;
  if (k === "row") return `[Row ${value}]`;
  if (k === "image") return `[Image ${value}]`;
  if (k === "segment") return /^\d{1,3}:\d{2}(?::\d{2})?$/.test(value) ? `[${value}]` : `[Segment ${value}]`;
  return `[${kind} ${value}]`;
}

/** Returns the page number if the href is a page citation, else null. */
export function pageFromHref(href: string | undefined): number | null {
  const target = citationTarget(href);
  if (!target || target.kind !== "page") return null;
  const page = Number.parseInt(target.value, 10);
  return Number.isFinite(page) && page > 0 ? page : null;
}
