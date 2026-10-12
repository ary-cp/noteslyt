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


# ------------------------------------------------- stored-parse round-trip
# Re-embedding must not pay for (or risk) a fresh parse/transcription when the
# last parse is already stored: `render_parsed_markdown` output inverts back
# into units here. Segment ranges are millisecond-precise, so timestamps
# survive; second-precision `[m:ss]` starts get their ends inferred from the
# next segment. Anything unrecognized (or an empty result) returns [] and the
# caller falls back to parsing the file — never a corrupt ingest.
#
# One known edge: a unit *body* containing a line that starts with "## "
# splits into an extra section unit here. That only reshapes chunks on the
# re-embed path (retrieval quality, not corruption), and real parses all but
# never emit such lines — accepted deliberately to keep the stored format
# byte-identical to what the viewer already shows.


def _parse_stored_ts(text: str) -> float | None:
    """`00:00:29.760` or `1:23` -> seconds. None when unparseable."""
    text = text.strip()
    if "-->" in text:
        return None
    parts = text.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            if not m.isdigit() or len(m) > 3:
                return None
            return int(m) * 60 + float(s)
    except ValueError:
        return None
    return None


def _split_stored_header(header: str) -> tuple[str, str, float | None, float | None] | None:
    """`## ...` label -> (kind, value, start_ts, end_ts). None when not a header we wrote."""
    import re

    if " --> " in header:
        left, _, right = header.partition(" --> ")
        start, end = _parse_stored_ts(left), _parse_stored_ts(right)
        if start is None or end is None:
            return None
        return (SEGMENT, format_timestamp(start), start, end)
    m = re.fullmatch(r"p\.(\d+)", header)
    if m:
        return (PAGE, m.group(1), None, None)
    m = re.fullmatch(r"slide (\d+)", header)
    if m:
        return (SLIDE, m.group(1), None, None)
    m = re.fullmatch(r"sheet: (.+)", header)
    if m:
        return (SHEET, m.group(1), None, None)
    m = re.fullmatch(r"image (.+)", header)
    if m:
        return (IMAGE, m.group(1), None, None)
    m = re.fullmatch(r"row (.+)", header)
    if m:
        return (ROW, m.group(1), None, None)
    start = _parse_stored_ts(header)
    if start is not None:
        return (SEGMENT, format_timestamp(start), start, None)
    return None


def units_from_markdown(markdown: str) -> tuple[list[SourceUnit], str]:
    """Invert `render_parsed_markdown` output -> (units, parser name).

    Returns ([], "") when nothing parses, so callers fall back to the file.
    Section-style headers that match no known form become `section` units
    with the whole header as the value (mirrors how md/txt parsers read).
    """
    units: list[SourceUnit] = []
    parser = ""
    current_header: str | None = None
    current_lines: list[str] = []
    title_seen = False

    def flush() -> None:
        if current_header is None:
            return
        text = "\n".join(current_lines).strip()
        if not text:
            return
        split = _split_stored_header(current_header)
        if split is None:
            kind, value, start, end = SECTION, current_header, None, None
        else:
            kind, value, start, end = split
        units.append(SourceUnit(
            index=len(units) + 1, text=text,
            locator_kind=kind, locator_value=value,
            start_ts=start, end_ts=end,
        ))

    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        if not title_seen:
            if line.startswith("# ") and not line.startswith("## "):
                title_seen = True
                continue
            if not line.strip():
                continue
            # No title line (old rows): treat everything as units.
            title_seen = True
        if line.startswith("<!-- parser:") and line.rstrip().endswith("-->"):
            parser = line.split("<!-- parser:", 1)[1].rsplit("-->", 1)[0].strip()
            continue
        if line.lstrip().startswith("…(truncated"):
            continue
        if line.startswith("## "):
            # A new unit. Known forms invert precisely; anything else becomes
            # a `section` unit carrying the whole header (mirrors md/txt).
            flush()
            current_header = line[3:].strip()
            current_lines = []
            continue
        if current_header is None:
            continue
        current_lines.append(raw_line)
    flush()

    # Second-precision starts inherit their end from the next segment start.
    for i, unit in enumerate(units):
        if (unit.locator_kind == SEGMENT and unit.start_ts is not None
                and unit.end_ts is None):
            nxt = units[i + 1] if i + 1 < len(units) else None
            if (nxt is not None and nxt.locator_kind == SEGMENT
                    and nxt.start_ts is not None and nxt.start_ts > unit.start_ts):
                unit.end_ts = nxt.start_ts
    return units, parser


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