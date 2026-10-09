"""
Parser registry: file type -> list of units.

Phase C registers the PDF parsers that already existed. Phase D adds docx, pptx,
xlsx, md/csv/txt and the audio/video transcription path — each one is a single
function returning `list[SourceUnit]`, with no changes needed to the chunker,
the schema, the pipeline or the citation code.

Selection follows the same shape as the PDF parsers: an ordered list of
candidates, first success wins, with the failure reasons surfaced as warnings so
a partial ingest is still explainable.

`PARSER=auto|firecrawl|local` continues to govern the PDF parsers only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..config import Settings
from .parsers import ParserError, build_parsers, parse_pdf
from .parsers_docs import (
    parse_csv_file,
    parse_docx,
    parse_md,
    parse_pptx,
    parse_tsv_file,
    parse_txt,
    parse_xlsx,
)
from .parsers_media import parse_audio, parse_image, parse_video
from .units import ParsedSource, SourceUnit, units_from_pages

log = logging.getLogger(__name__)

ParserFn = Callable[[Path, Settings], list[SourceUnit]]


# =============================================================================
#  Registered parsers, keyed by the `sources.source_type` enum
# =============================================================================
def _parse_pdf(path: Path, settings: Settings) -> ParsedSource:
    """The existing Firecrawl/pdf-inspector/anydoc chain, adapted to units."""
    parsed = parse_pdf(path, settings)
    return ParsedSource(units=units_from_pages(parsed.pages), parser=parsed.parser, warnings=parsed.warnings)


_REGISTRY: dict[str, list[tuple[str, ParserFn]]] = {
    # PDF has up to three ordered candidates; `auto` decides which are enabled.
    "pdf": [
        ("pdf-auto", _parse_pdf),
    ],
    # Phase D.17 — one parser per format id. Legacy siblings (doc/odt/rtf/epub,
    # ppt/xls) and `other` (.zip, …) stay unregistered: the upload endpoint
    # refuses them with a readable reason instead of failing mid-ingestion.
    "docx": [("docx", parse_docx)],
    "pptx": [("pptx", parse_pptx)],
    "xlsx": [("xlsx", parse_xlsx)],
    "csv": [("csv", parse_csv_file)],
    "tsv": [("tsv", parse_tsv_file)],
    "md": [("md", parse_md)],
    "txt": [("txt", parse_txt)],
    # Phase D.18 — lecture audio/video via the transcription endpoint.
    # Needs the lifespan to call `set_transcriber()`; without it these fail
    # with a readable reason instead of an AttributeError.
    "audio": [("whisper", parse_audio)],
    "video": [("whisper-video", parse_video)],
    # Image files are indexed as visual vectors by the configured multimodal
    # embedding endpoint; no captioning pass is used.
    "image": [("multimodal-image-embedding", parse_image)],
}


def register(source_type: str, name: str, fn: ParserFn) -> None:
    """Add a parser for a source type. Used by Phase D and by tests."""
    _REGISTRY.setdefault(source_type, []).append((name, fn))


def supported_types() -> list[str]:
    return sorted(_REGISTRY)


def parse_source(path: Path, source_type: str, settings: Settings) -> ParsedSource:
    """
    Parse any registered file type into units.

    Raises `ParserError` when no parser handles the type or all of them fail, so
    the caller can mark the source `failed` with a reason the learner can read.
    """
    candidates = _REGISTRY.get(source_type)
    if not candidates:
        raise ParserError(
            f"No parser registered for '{source_type}'. Supported: {', '.join(supported_types())}."
        )

    errors: list[str] = []
    for name, fn in candidates:
        try:
            log.info("Parsing %s (%s) with %s", path.name, source_type, name)
            result = fn(path, settings)
            if isinstance(result, list):
                result = ParsedSource(units=result, parser=name)
            if errors:
                result.warnings.append("Fell back to " + name + ": " + "; ".join(errors))
            return result
        except Exception as exc:  # noqa: BLE001 — try the next parser
            log.warning("Parser %s failed on %s: %s", name, path.name, exc)
            errors.append(f"{name}: {exc}")
    raise ParserError(f"All parsers failed for {path.name}: " + "; ".join(errors))


__all__ = [
    "ParsedSource",
    "ParserError",
    "SourceUnit",
    "build_parsers",
    "parse_source",
    "register",
    "supported_types",
]
