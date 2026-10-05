/**
 * Citation helpers.
 *
 * The backend guarantees citations look exactly like `[Page 14]`. Before the
 * Markdown is rendered we rewrite each one into a link with a sentinel href
 * (`#page=14`); `MarkdownMessage` then renders those links as chips and the
 * click handler scrolls the PDF viewer instead of navigating.
 */

export const CITATION_RE = /\[Page (\d+)\]/g;
export const CITATION_HREF_PREFIX = "#page=";

export function linkifyCitations(markdown: string): string {
  return markdown.replace(CITATION_RE, (_m, page: string) => `[Page ${page}](${CITATION_HREF_PREFIX}${page})`);
}

/** Returns the page number if the href is one of our citation links, else null. */
export function pageFromHref(href: string | undefined): number | null {
  if (!href || !href.startsWith(CITATION_HREF_PREFIX)) return null;
  const page = Number.parseInt(href.slice(CITATION_HREF_PREFIX.length), 10);
  return Number.isFinite(page) && page > 0 ? page : null;
}
