"""
`SourceUnit` — the generalized "page".

Everything downstream of parsing (chunking, topics, citations, retrieval) works
in terms of a locator: *where in the original file does this text live?* A PDF
answers with a page, a slide deck with a slide, a lecture recording with a
timestamp range, a spreadsheet with a sheet.

Why generalize now rather than add columns per type:
  * `chunks` carries one `locator_kind` + `locator_value` instead of a growing
    pile of nullable `page`/`slide`/`start_ts` columns.
  * The chunker's core invariant becomes type-agnostic: **a chunk never crosses a
    locator boundary**. That is what makes a citation exact, and it holds for a
    page, a slide, a transcript segment or a sheet identically.
  * Adding a file type in Phase D means writing one parser, not touching the
    chunker, the schema or the citation code.

`ParsedPage` (PDF) converts to a `SourceUnit` with `locator_kind="page"`, so the
existing PDF path keeps working untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Locator kinds. Anything not listed here is still accepted — `locator_value` is
# free text — but these have rendering and citation semantics attached.
PAGE = "page"
SLIDE = "slide"
SEGMENT = "segment"
SHEET = "sheet"
ROW = "row"
SECTION = "section"
IMAGE = "image"


@dataclass
class SourceUnit:
    """
    One addressable piece of a source.

    `index` is the unit's position in reading order and is what the citation
    shows for kinds that have no natural label (e.g. a bare `segment`).
    """

    index: int
    text: str
    locator_kind: str = PAGE
    locator_value: str = ""
    start_ts: float | None = None
    end_ts: float | None = None
    assets: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.locator_value:
            self.locator_value = str(self.index)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    @property
    def page(self) -> int | None:
        """Back-compat for the PDF citation contract: the page number, or None."""
        if self.locator_kind != PAGE:
            return None
        try:
            return int(self.locator_value)
        except (TypeError, ValueError):
            return None

    def citation_label(self) -> str:
        """Human label for this unit — the section header in the parsed view.

        Segments render as a VTT-style range (`00:00:29.760 --> 00:00:36.500`)
        so the transcript shows both ends. Chat citations intentionally keep
        the short `[m:ss]` start form (see `app/citations.py`) — that
        contract is unchanged.
        """
        if self.locator_kind == SEGMENT and self.start_ts is not None:
            if self.end_ts is not None and self.end_ts > self.start_ts:
                return format_timestamp_range(self.start_ts, self.end_ts)
            return format_timestamp(self.start_ts)
        if self.locator_kind == PAGE:
            return f"p.{self.locator_value}"
        if self.locator_kind == SLIDE:
            return f"slide {self.locator_value}"
        if self.locator_kind == SHEET:
            return f"sheet: {self.locator_value}"
        if self.locator_kind == SECTION:
            return self.locator_value
        return f"{self.locator_kind} {self.locator_value}"


def format_timestamp(seconds: float) -> str:
    """`83.4` -> `1:23`. Matches the `[mm:ss]` citation format."""
    total = int(max(0.0, seconds))
    return f"{total // 60}:{total % 60:02d}"


def format_timestamp_ms(seconds: float) -> str:
    """`29.76` -> `00:00:29.760`. VTT-style, millisecond precision."""
    total_ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def format_timestamp_range(start: float, end: float) -> str:
    """`29.76, 36.5` -> `00:00:29.760 --> 00:00:36.500`. VTT cue range."""
    return f"{format_timestamp_ms(start)} --> {format_timestamp_ms(end)}"


def units_from_pages(pages) -> list[SourceUnit]:
    """Adapt the existing `ParsedPage` list into `SourceUnit`s (PDF path)."""
    return [
        SourceUnit(
            index=page.page_number,
            text=page.markdown,
            locator_kind=PAGE,
            locator_value=str(page.page_number),
        )
        for page in pages
    ]


@dataclass
class ParsedSource:
    """What a parser produced, plus anything the caller should be told."""

    units: list[SourceUnit]
    parser: str
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(u.text.strip() for u in self.units)

    @property
    def unit_count(self) -> int:
        return len(self.units)