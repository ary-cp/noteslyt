"""
PDF -> per-page Markdown.

Two interchangeable parsers behind one tiny interface:

  FirecrawlParser  — POST /v2/parse (multipart upload). With the PDF parser
                     option `pages: true`, Firecrawl returns a `pages[]` array
                     where every item is `{pageNumber, markdown}`. That gives
                     us clean, structured markdown (tables, headings, OCR for
                     scanned slides) AND exact page attribution for citations.

  LocalParser      — PyMuPDF via `pymupdf4llm` with `page_chunks=True`.
                     Offline, free, no API key. Slightly rougher markdown.

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
#  Local (PyMuPDF)
# =============================================================================
class LocalParser:
    name = "local"

    def parse(self, pdf_path: Path) -> ParsedDocument:
        import pymupdf  # PyMuPDF
        import pymupdf4llm

        with pymupdf.open(pdf_path) as doc:
            total_pages = doc.page_count
            title = (doc.metadata or {}).get("title") or None
            # page_chunks=True -> one dict per page with markdown text + metadata
            chunks = pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False)

        pages: list[ParsedPage] = []
        for i, chunk in enumerate(chunks):
            meta = chunk.get("metadata", {}) or {}
            # pymupdf4llm reports a 1-based `page`; fall back to position if missing.
            page_number = int(meta.get("page") or (i + 1))
            pages.append(ParsedPage(page_number=page_number, markdown=chunk.get("text", "") or ""))

        warnings = []
        if not pages or all(not p.markdown.strip() for p in pages):
            warnings.append("No extractable text found — scanned PDF? Firecrawl's OCR mode would handle this.")
        return ParsedDocument(pages=pages, total_pages=total_pages, parser=self.name, title=title, warnings=warnings)


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
