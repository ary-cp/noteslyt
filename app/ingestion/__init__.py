"""
Ingestion: any source file -> locator-tagged units -> chunks -> vectors.

The unit of work is a `SourceUnit`, which knows where it came from (page, slide,
timestamp, sheet). Every downstream stage works in terms of that locator, which
is what makes citations exact.
"""

from .chunker import Chunk, chunk_pages, chunk_units
from .pipeline import IngestionError, IngestionPipeline, IngestResult
from .registry import ParsedSource, ParserError, parse_source, register, supported_types
from .units import SourceUnit, format_timestamp

__all__ = [
    "Chunk",
    "IngestResult",
    "IngestionError",
    "IngestionPipeline",
    "ParsedSource",
    "ParserError",
    "SourceUnit",
    "chunk_pages",
    "chunk_units",
    "format_timestamp",
    "parse_source",
    "register",
    "supported_types",
]