/**
 * Renders assistant Markdown and turns every `[Page N]` into a clickable chip.
 *
 * How the interactive citation works end-to-end:
 *   backend  → "…defined on [Page 14]."
 *   linkify  → "…defined on [Page 14](#page=14)."
 *   here     → <a href="#page=14"> is intercepted and rendered as a <button>
 *   onCite   → App bumps `targetPage` → PdfViewer scrolls to page 14
 */

import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { linkifyCitations, pageFromHref } from "../lib/citations";

interface Props {
  markdown: string;
  onCite: (page: number) => void;
}

function MarkdownMessageImpl({ markdown, onCite }: Props) {
  const components: Components = {
    a: ({ href, children, ...rest }) => {
      const page = pageFromHref(href);
      if (page !== null) {
        return (
          <button
            type="button"
            className="cite"
            title={`Open page ${page}`}
            onClick={(e) => {
              e.preventDefault();
              onCite(page);
            }}
          >
            {children}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" {...rest}>
          {children}
        </a>
      );
    },
  };

  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {linkifyCitations(markdown)}
      </ReactMarkdown>
    </div>
  );
}

export const MarkdownMessage = memo(MarkdownMessageImpl);
