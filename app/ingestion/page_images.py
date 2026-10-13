"""Document page rendering for visual retrieval.

Every PDF page is rendered to a PNG so charts, diagrams and scanned figures
are embedded as images alongside the parsed text — retrieval then matches
both what a page *says* and what it *shows*. PNG bytes live on disk next to
the upload (`FileStorage.page_image_*`); only vectors go to pgvector.

Office documents (.pptx/.docx/.xlsx) render via LibreOffice headless
conversion to PDF first, then the same PNG path. Without LibreOffice they
stay text-only with a warning (see `render_pages`).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Render scale: 2x ≈ 144 DPI. Crisp enough for charts/diagrams, small enough
# that a hundred pages stay cheap on disk and fast to embed.
RENDER_SCALE = 2.0


def render_pdf_pages(path: Path, pages: list[int], scale: float = RENDER_SCALE) -> list[tuple[int, bytes, int, int]]:
    """Render 1-based `pages` of a PDF to PNG bytes: [(page_no, png, w, h)]."""
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError(f"Page rendering needs pymupdf: {exc}") from exc
    out: list[tuple[int, bytes, int, int]] = []
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise RuntimeError(f"Could not open {path.name} for rendering: {exc}") from exc
    with doc:
        for page_no in pages:
            if page_no < 1 or page_no > doc.page_count:
                continue
            try:
                pix = doc[page_no - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale))
                out.append((page_no, pix.tobytes("png"), pix.width, pix.height))
            except Exception as exc:  # noqa: BLE001 — one bad page skips itself
                log.warning("Page %d render skipped for %s: %s", page_no, path.name, exc)
    return out


def has_libreoffice() -> bool:
    """Whether office documents can be rendered (LibreOffice headless conversion)."""
    return shutil.which("soffice") is not None


def _convert_office_to_pdf(path: Path, workdir: Path) -> Path:
    """Convert one office document to PDF via LibreOffice. Returns the PDF path."""
    try:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(workdir), str(path)],
            capture_output=True,
            timeout=300,
            check=False,
        )
    except Exception as exc:
        raise RuntimeError(f"Office conversion failed for {path.name}: {exc}") from exc
    converted = sorted(workdir.glob("*.pdf"))
    if not converted:
        raise RuntimeError(f"LibreOffice produced no PDF for {path.name}.")
    return converted[0]


def render_pptx_slides(path: Path, slides: list[int], scale: float = RENDER_SCALE) -> list[tuple[int, bytes, int, int]]:
    """Render 1-based `slides` of a deck via LibreOffice → PDF → PNG.

    Slide N maps to PDF page N of the conversion. Raises with a readable
    reason when LibreOffice is missing or the conversion fails.
    """
    if not has_libreoffice():
        raise RuntimeError("Slide rendering needs LibreOffice (soffice) on PATH.")
    with tempfile.TemporaryDirectory(prefix="noteslyt-slides-") as tmp:
        converted = _convert_office_to_pdf(path, Path(tmp))
        return render_pdf_pages(converted, slides, scale)


def render_office_documents(
    path: Path, slides: list[int] | None = None, limit: int | None = None,
    scale: float = RENDER_SCALE,
) -> list[tuple[int, bytes, int, int]]:
    """Render a .docx/.xlsx to PNGs via LibreOffice → PDF → PNG.

    `slides` (1-based PDF ordinals) wins when given; otherwise all converted
    pages render, capped at `limit` when set. Wide workbook sheets render at
    the converter's default print layout — page breaks follow print setup,
    not sheet/row groups, so chunks stay generic `image N`.
    """
    if not has_libreoffice():
        raise RuntimeError("Document rendering needs LibreOffice (soffice) on PATH.")
    with tempfile.TemporaryDirectory(prefix="noteslyt-office-") as tmp:
        converted = _convert_office_to_pdf(path, Path(tmp))
        numbers = list(slides) if slides else _all_pdf_pages(converted, limit)
        return render_pdf_pages(converted, numbers, scale)


def _all_pdf_pages(path: Path, limit: int | None) -> list[int]:
    """1-based page numbers of a PDF, capped at `limit` when set."""
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError(f"Page rendering needs pymupdf: {exc}") from exc
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise RuntimeError(f"Could not open {path.name} for rendering: {exc}") from exc
    with doc:
        total = doc.page_count
    numbers = list(range(1, total + 1))
    if limit is not None and limit > 0:
        numbers = numbers[:limit]
    return numbers


def render_pages(path: Path, source_type: str, numbers: list[int] | None) -> list[tuple[int, bytes, int, int]]:
    """Render pages/slides/sheets for a parsed source; empty list when unsupported."""
    if source_type == "pdf":
        return render_pdf_pages(path, numbers or [])
    if source_type == "pptx":
        return render_pptx_slides(path, numbers or [])
    if source_type in ("docx", "xlsx"):
        # numbers=None means "all converted pages" (pipeline passes None for
        # office docs, whose units carry section/sheet locators, not ordinals).
        return render_office_documents(path, slides=numbers or None)
    return []
