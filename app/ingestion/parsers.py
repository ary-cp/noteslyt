"""
PDF -> per-page Markdown.

Two interchangeable parsers behind one tiny interface:

  FirecrawlParser  — POST /v2/parse (multipart upload). With the PDF parser
                     option `pages: true`, Firecrawl returns a `pages[]` array
                     where every item is `{pageNumber, markdown}`. That gives
                     us clean, structured markdown (tables, headings, OCR for
                     scanned slides) AND exact page attribution for citations.

  LocalParser      — pdf-inspector (`extract_pages_markdown`, per-page
                     Markdown + classification). anydoc (`to_markdown`)
                     is the automatic fallback if pdf-inspector fails.
                     Both are offline, free, no API key.

`parse_pdf()` picks one according to `PARSER` in .env and falls back to the
local parser if Firecrawl is unreachable, so an ingestion never dies on a
network hiccup during a live demo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from ..config import Settings

log = logging.getLogger(__name__)


@dataclass
class ParsedPage:
    page_number: int     # 1-based physical page — this is what citations show
    markdown: str


@dataclass
class ParsedDocument:
    pages: list[ParsedPage]
    total_pages: int
    parser: str                      # "firecrawl" | "local"
    title: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(p.markdown.strip() for p in self.pages)


class DocumentParser(Protocol):
    name: str

    def parse(self, pdf_path: Path) -> ParsedDocument: ...


class ParserError(RuntimeError):
    pass


# =============================================================================
#  Firecrawl
# =============================================================================
class FirecrawlParser:
    name = "firecrawl"

    def __init__(self, api_key: str, api_url: str = "https://api.firecrawl.dev", max_pages: int = 100):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.max_pages = max_pages

    def parse(self, pdf_path: Path) -> ParsedDocument:
        options = {
            "formats": ["markdown"],
            "parsers": [
                {
                    "type": "pdf",
                    "mode": "auto",        # text extraction, OCR only when needed
                    "pages": True,         # <-- per-page markdown = exact citations
                    "maxPages": self.max_pages,
                }
            ],
        }
        with pdf_path.open("rb") as fh:
            # `options` is sent as a JSON-typed multipart part, exactly as the
            # documented curl does: -F 'options={...};type=application/json'
            response = httpx.post(
                f"{self.api_url}/v2/parse",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={
                    "file": (pdf_path.name, fh, "application/pdf"),
                    "options": (None, json.dumps(options), "application/json"),
                },
                timeout=httpx.Timeout(300.0, connect=15.0),
            )

        if response.status_code != 200:
            raise ParserError(f"Firecrawl HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if not payload.get("success", False):
            raise ParserError(f"Firecrawl error: {payload.get('error') or payload}")

        data = payload.get("data", {})
        meta = data.get("metadata", {}) or {}
        raw_pages = data.get("pages") or []

        pages = [
            ParsedPage(page_number=int(p.get("pageNumber", i + 1)), markdown=p.get("markdown", "") or "")
            for i, p in enumerate(raw_pages)
        ]
        warnings: list[str] = []
        if not pages:
            # Defensive: if the server ignored `pages: true`, keep the document
            # usable by attributing everything to page 1.
            warnings.append("Firecrawl returned no per-page output; citations will be approximate.")
            pages = [ParsedPage(1, data.get("markdown", "") or "")]

        total = int(meta.get("totalPages") or meta.get("numPages") or len(pages))
        if meta.get("numPages") and meta.get("totalPages") and meta["numPages"] < meta["totalPages"]:
            warnings.append(f"Only the first {meta['numPages']} of {meta['totalPages']} pages were parsed (FIRECRAWL_MAX_PAGES).")

        return ParsedDocument(pages=pages, total_pages=total, parser=self.name, title=meta.get("title"), warnings=warnings)


# =============================================================================
#  Local (pdf-inspector / anydoc)
# =============================================================================
class LocalParser:
    name = "local"

    def parse(self, pdf_path: Path) -> ParsedDocument:
        try:
            return self._parse_with_pdf_inspector(pdf_path)
        except Exception as exc:  # noqa: BLE001 — fall back to anydoc
            log.warning("pdf-inspector failed (%s); falling back to anydoc", exc)
            return self._parse_with_anydoc(pdf_path)

    def _parse_with_pdf_inspector(self, pdf_path: Path) -> ParsedDocument:
        import pdf_inspector

        result = pdf_inspector.extract_pages_markdown(str(pdf_path))
        pages = [
            ParsedPage(page_number=int(getattr(p, "page", i)) + 1, markdown=p.markdown or "")
            for i, p in enumerate(result.pages)
        ]
        warnings: list[str] = []
        if result.pages_needing_ocr:
            warnings.append(
                "Pages needing OCR skipped by local parser: "
                + ", ".join(map(str, result.pages_needing_ocr))
                + ". Set FIRECRAWL_API_KEY for OCR."
            )
        if not pages or all(not p.markdown.strip() for p in pages):
            warnings.append("No extractable text found — scanned PDF? Firecrawl's OCR mode would handle this.")
        return ParsedDocument(
            pages=pages,
            total_pages=len(result.pages),
            parser=self.name,
            title=None,
            warnings=warnings,
        )

    def _parse_with_anydoc(self, pdf_path: Path) -> ParsedDocument:
        import anydoc

        markdown = anydoc.to_markdown(str(pdf_path))
        warnings = ["Parsed with anydoc fallback; page attribution is approximate (everything on page 1)."]
        if not markdown.strip():
            warnings.append("No extractable text found — scanned PDF? Firecrawl's OCR mode would handle this.")
        return ParsedDocument(
            pages=[ParsedPage(page_number=1, markdown=markdown or "")],
            total_pages=1,
            parser=self.name,
            title=None,
            warnings=warnings,
        )


# =============================================================================
#  Selection + fallback
# =============================================================================
def build_parsers(settings: Settings) -> list[DocumentParser]:
    """Ordered list: first that succeeds wins."""
    firecrawl = (
        FirecrawlParser(settings.firecrawl_api_key, settings.firecrawl_api_url, settings.firecrawl_max_pages)
        if settings.firecrawl_api_key
        else None
    )
    if settings.parser == "local":
        return [LocalParser()]
    if settings.parser == "firecrawl":
        if firecrawl is None:
            raise ParserError("PARSER=firecrawl but FIRECRAWL_API_KEY is not set")
        return [firecrawl]
    # auto
    return [p for p in (firecrawl, LocalParser()) if p is not None]


def parse_pdf(pdf_path: Path, settings: Settings) -> ParsedDocument:
    errors: list[str] = []
    for parser in build_parsers(settings):
        try:
            log.info("Parsing %s with %s", pdf_path.name, parser.name)
            parsed = parser.parse(pdf_path)
            if errors:
                parsed.warnings.append("Fell back to local parser: " + "; ".join(errors))
            return parsed
        except Exception as exc:  # noqa: BLE001 — we want to try the next parser
            log.warning("Parser %s failed: %s", parser.name, exc)
            errors.append(f"{parser.name}: {exc}")
    raise ParserError("All parsers failed: " + "; ".join(errors))
