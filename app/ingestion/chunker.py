"""
Locator-aware Markdown chunking.

The single most important property for trustworthy citations: a chunk NEVER
crosses a locator boundary. Every chunk therefore carries exactly one locator —
a page, a slide, a transcript timestamp, a sheet — and the LLM can only ever
cite a location that physically contains the text it saw.

Strategy (per unit):
  1. Split markdown into blocks (paragraphs / headings / table rows / lists).
  2. Track the most recent heading so each chunk knows its section.
  3. Greedily pack blocks up to `chunk_size` characters.
  4. Carry a `chunk_overlap` tail into the next chunk so sentences split at a
     boundary are still retrievable from both sides.
  5. A single oversized block (big table, wall of text) is hard-split.

`chunk_pages()` is kept as a thin wrapper so the PDF call site keeps working;
new file types call `chunk_units()`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .units import SourceUnit, units_from_pages

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")        # images carry no study value
_WS_RE = re.compile(r"[ \t]+")


@dataclass
class Chunk:
    text: str
    chunk_index: int          # global order within the source
    heading: str | None = None        # nearest preceding heading (section title)
    locator_kind: str = "page"
    locator_value: str = ""
    start_ts: float | None = None
    end_ts: float | None = None

    @property
    def page(self) -> int | None:
        """Back-compat: the page number for page-locator chunks, else None."""
        if self.locator_kind != "page":
            return None
        try:
            return int(self.locator_value)
        except (TypeError, ValueError):
            return None


def _clean(markdown: str) -> str:
    text = _IMAGE_RE.sub("", markdown)
    text = text.replace("\r\n", "\n").replace(" ", " ")
    text = _WS_RE.sub(" ", text)
    # collapse 3+ newlines to 2 so paragraph detection is stable
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _blocks(markdown: str) -> list[str]:
    """Paragraph-level split; headings always become their own block."""
    out: list[str] = []
    for para in markdown.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        lines = para.split("\n")
        buf: list[str] = []
        for line in lines:
            if _HEADING_RE.match(line):
                if buf:
                    out.append("\n".join(buf).strip())
                    buf = []
                out.append(line.strip())
            else:
                buf.append(line)
        if buf:
            out.append("\n".join(buf).strip())
    return [b for b in out if b]


def _hard_split(block: str, size: int) -> list[str]:
    """Split an oversized block on sentence/line boundaries where possible."""
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+|\n", block):
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 > size and current:
            pieces.append(current.strip())
            current = ""
        if len(sentence) > size:                       # pathological: no punctuation at all
            for i in range(0, len(sentence), size):
                pieces.append(sentence[i : i + size])
            continue
        current += (" " if current else "") + sentence
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _pack(
    units: list[SourceUnit],
    chunk_size: int,
    chunk_overlap: int,
) -> list[Chunk]:
    """Pack each unit's blocks into chunks, flushing at every unit boundary."""
    chunks: list[Chunk] = []
    heading: str | None = None
    index = 0

    def make(body: str) -> Chunk:
        nonlocal index
        chunk = Chunk(
            text=body,
            chunk_index=index,
            heading=heading,
            locator_kind=unit.locator_kind,
            locator_value=unit.locator_value,
            start_ts=unit.start_ts,
            end_ts=unit.end_ts,
        )
        index += 1
        return chunk

    for unit in units:
        text = _clean(unit.text)
        if not text:
            continue

        buffer: list[str] = []
        buffer_len = 0

        def flush() -> None:
            nonlocal buffer, buffer_len
            body = "\n\n".join(buffer).strip()
            if body:
                chunks.append(make(body))
            # overlap: keep the tail of the previous chunk as the head of the next
            tail = body[-chunk_overlap:] if chunk_overlap and len(body) > chunk_overlap else ""
            buffer = [tail] if tail else []
            buffer_len = len(tail)

        for block in _blocks(text):
            m = _HEADING_RE.match(block)
            if m:
                heading = m.group(1).strip()

            parts = [block] if len(block) <= chunk_size else _hard_split(block, chunk_size)
            for part in parts:
                if buffer_len + len(part) + 2 > chunk_size and buffer_len > chunk_overlap:
                    flush()
                buffer.append(part)
                buffer_len += len(part) + 2

        # end of unit: always flush — a chunk never spans two locators
        body = "\n\n".join(buffer).strip()
        if body:
            chunks.append(make(body))

    return chunks


def chunk_units(
    units: list[SourceUnit],
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> list[Chunk]:
    """Chunk any source type. `chunk_size` is characters; units are never merged."""
    return _pack(units, chunk_size, chunk_overlap)


def chunk_pages(pages, chunk_size: int = 1200, chunk_overlap: int = 200) -> list[Chunk]:
    """Back-compat wrapper for the PDF path."""
    return chunk_units(units_from_pages(pages), chunk_size, chunk_overlap)
