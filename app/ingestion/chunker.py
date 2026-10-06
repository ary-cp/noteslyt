"""
Page-aware Markdown chunking.

The single most important property for trustworthy citations: a chunk NEVER
crosses a page boundary. Every chunk therefore carries one exact `page`, and
the LLM can only ever cite pages that physically contain the text it saw.

Strategy (per page):
  1. Split markdown into blocks (paragraphs / headings / table rows / lists).
  2. Track the most recent heading so each chunk knows its section.
  3. Greedily pack blocks up to `chunk_size` characters.
  4. Carry a `chunk_overlap` tail into the next chunk so sentences split at a
     boundary are still retrievable from both sides.
  5. A single oversized block (big table, wall of text) is hard-split.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parsers import ParsedPage

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")        # images carry no study value
_WS_RE = re.compile(r"[ \t]+")


@dataclass
class Chunk:
    text: str
    page: int
    chunk_index: int          # global order within the document
    heading: str | None       # nearest preceding heading (section title)


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


def chunk_pages(pages: list[ParsedPage], chunk_size: int = 1200, chunk_overlap: int = 200) -> list[Chunk]:
    chunks: list[Chunk] = []
    heading: str | None = None
    index = 0

    for page in pages:
        text = _clean(page.markdown)
        if not text:
            continue

        buffer: list[str] = []
        buffer_len = 0

        def flush() -> None:
            nonlocal buffer, buffer_len, index
            body = "\n\n".join(buffer).strip()
            if body:
                chunks.append(Chunk(text=body, page=page.page_number, chunk_index=index, heading=heading))
                index += 1
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

        # end of page: always flush — chunks never span pages
        body = "\n\n".join(buffer).strip()
        if body and len(body) > (chunk_overlap if chunks and chunks[-1].page == page.page_number else 0):
            chunks.append(Chunk(text=body, page=page.page_number, chunk_index=index, heading=heading))
            index += 1

    return chunks
