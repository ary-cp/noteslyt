"""
Document / slide / spreadsheet / text parsers — Phase D.17.

Each function takes a stored file and returns a `ParsedSource` of `SourceUnit`s.
The registry in `registry.py` dispatches on `sources.source_type`; nothing
downstream (chunker, pipeline, topics, citations) knows which parser ran.

Locator discipline (plan.md invariant 1 — a chunk never crosses a locator):
  * docx / md / txt → `section` (one heading-delimited block)
  * pptx            → `slide`   (one slide, speaker notes included)
  * xlsx            → `sheet`   (small sheets) or `row` ranges (wide tables)
  * csv / tsv       → `row`     (groups of data rows)

Only the seven format ids listed in plan.md Phase D are registered here
(`docx pptx xlsx md csv txt` + `tsv` as csv's sibling). Legacy siblings
(`doc odt rtf epub ppt xls`) stay unregistered on purpose: the libraries below
cannot read them, and the upload endpoint refuses unregistered types with a
readable reason instead of failing mid-ingestion. `other` (e.g. `.zip`) is
likewise unregistered — `scripts/smoke_test.py` asserts a `.zip` is refused.
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import date, datetime
from pathlib import Path

from ..config import Settings
from .parsers import ParserError
from .units import ROW, SECTION, SHEET, SLIDE, ParsedSource, SourceUnit

log = logging.getLogger(__name__)

# Row-group sizing: one unit per data row would turn a 5k-row export into 5k
# embedding calls, so tabular data is grouped. A group is still ONE locator
# ("Sheet Rows 1-50"), so the chunker invariant holds.
_TABULAR_GROUP_ROWS = 50
_XLSX_SINGLE_SHEET_MAX_ROWS = 200
_CSV_MAX_ROWS = 2000
_XLSX_MAX_ROWS = 5000


def _read_text(path: Path) -> str:
    """Best-effort text decode. NUL bytes mean binary masquerading as text."""
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise ParserError(f"{path.name} looks like a binary file, not plain text.")
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    raise ParserError(f"Could not decode {path.name} as text.")


# =============================================================================
#  docx — one section per heading
# =============================================================================
def parse_docx(path: Path, settings: Settings) -> ParsedSource:  # noqa: ARG001
    """Word documents: headings delimit sections; tables become markdown."""
    try:
        from docx import Document
    except ImportError as exc:
        raise ParserError(f"python-docx is not installed: {exc}") from exc

    try:
        doc = Document(str(path))
    except Exception as exc:
        raise ParserError(f"Could not open {path.name} as .docx: {exc}") from exc

    sections: list[dict] = [{"title": "Top", "lines": []}]
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name if para.style is not None else "") or ""
        if style.startswith("Heading") and len(sections[-1]["lines"]) > 0:
            sections.append({"title": text[:80], "lines": []})
            continue
        if style.startswith("Heading") and not sections[-1]["lines"]:
            # A leading heading names the first section instead of opening an empty one.
            sections[-1]["title"] = text[:80]
            continue
        if "List" in style:
            sections[-1]["lines"].append(f"- {text}")
        elif style.startswith("Heading"):
            sections[-1]["lines"].append(f"## {text}")
        else:
            sections[-1]["lines"].append(text)

    for table in doc.tables:
        rows = [[c.text.strip().replace("|", "\\|") for c in row.cells] for row in table.rows]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        md = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * len(rows[0])) + " |"]
        md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        sections[-1]["lines"].append("\n".join(md))

    units = [
        SourceUnit(index=i + 1, text="\n\n".join(s["lines"]), locator_kind=SECTION, locator_value=s["title"] or f"Section {i + 1}")
        for i, s in enumerate(sections)
        if "\n\n".join(s["lines"]).strip()
    ]

    warnings: list[str] = []
    try:
        if doc.inline_shapes and not units:
            warnings.append("Only embedded images found; upload image files separately for visual retrieval.")
        elif doc.inline_shapes:
            warnings.append(f"{len(doc.inline_shapes)} embedded image(s) skipped; upload image files separately for visual retrieval.")
    except Exception:  # noqa: BLE001 — shape probing must never fail the parse
        pass
    return ParsedSource(units=units, parser="docx", warnings=warnings)


# =============================================================================
#  pptx — one unit per slide, speaker notes included
# =============================================================================
def parse_pptx(path: Path, settings: Settings) -> ParsedSource:  # noqa: ARG001
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise ParserError(f"python-pptx is not installed: {exc}") from exc

    try:
        prs = Presentation(str(path))
    except Exception as exc:
        raise ParserError(f"Could not open {path.name} as .pptx: {exc}") from exc

    units: list[SourceUnit] = []
    warnings: list[str] = []
    for n, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        title = ""
        try:
            if slide.shapes.title and slide.shapes.title.text.strip():
                title = slide.shapes.title.text.strip()
        except Exception:  # noqa: BLE001
            title = ""
        if title:
            parts.append(f"# {title}")
        for shape in slide.shapes:
            if not shape.has_text_frame or (shape.has_table):
                continue
            text = shape.text.strip()
            if not text or text == title:
                continue
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            if len(lines) > 1 or (shape.text_frame.paragraphs and shape.text_frame.paragraphs[0].level > 0):
                parts.extend(f"- {ln}" for ln in lines)
            else:
                parts.append(text)
        # Speaker notes — often where the actual explanation lives.
        try:
            notes = slide.notes_slide.placeholders[1].text.strip() if slide.has_notes_slide else ""
        except Exception:  # noqa: BLE001
            notes = ""
        if notes:
            parts.append(f"> Notes: {notes}")
        body = "\n\n".join(parts).strip()
        if not body:
            warnings.append(f"Slide {n} has no extractable text (image-only?).")
            continue
        units.append(SourceUnit(index=n, text=body, locator_kind=SLIDE, locator_value=str(n)))

    for i, u in enumerate(units):
        u.index = i + 1
    return ParsedSource(units=units, parser="pptx", warnings=warnings)


# =============================================================================
#  xlsx — sheet units, row-range units for wide tables
# =============================================================================
def _format_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_xlsx(path: Path, settings: Settings) -> ParsedSource:  # noqa: ARG001
    try:
        import openpyxl
    except ImportError as exc:
        raise ParserError(f"openpyxl is not installed: {exc}") from exc

    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise ParserError(f"Could not open {path.name} as .xlsx: {exc}") from exc

    units: list[SourceUnit] = []
    warnings: list[str] = []
    try:
        names = list(wb.sheetnames)
        if len(names) > 50:
            warnings.append(f"Only the first 50 of {len(names)} sheets were read.")
            names = names[:50]
        for name in names:
            ws = wb[name]
            rows = [[_format_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
            # Drop trailing empties; keep the rectangular shape for tables.
            rows = [r for r in rows if any(r)]
            if not rows:
                continue
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            if len(rows) > _XLSX_MAX_ROWS:
                warnings.append(f"Sheet '{name}': only the first {_XLSX_MAX_ROWS} of {len(rows)} rows were read.")
                rows = rows[:_XLSX_MAX_ROWS]

            header, data = rows[0], rows[1:]
            if not data or len(rows) <= _XLSX_SINGLE_SHEET_MAX_ROWS:
                table = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
                table += ["| " + " | ".join(r) + " |" for r in data]
                units.append(SourceUnit(index=len(units) + 1, text="\n".join(table), locator_kind=SHEET, locator_value=name))
            else:
                for start in range(0, len(data), _TABULAR_GROUP_ROWS):
                    group = data[start : start + _TABULAR_GROUP_ROWS]
                    lines = [f"Sheet '{name}' rows {start + 2}-{start + 1 + len(group)}."]
                    lines += [" | ".join(f"{h}: {v}" for h, v in zip(header, r) if v) for r in group]
                    units.append(
                        SourceUnit(
                            index=len(units) + 1,
                            text="\n".join(lines),
                            locator_kind=ROW,
                            locator_value=f"{name}!{start + 2}:{start + 1 + len(group)}",
                        )
                    )
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
    return ParsedSource(units=units, parser="xlsx", warnings=warnings)


# =============================================================================
#  csv / tsv — groups of data rows
# =============================================================================
def parse_csv(path: Path, settings: Settings, *, delimiter: str = ",") -> ParsedSource:
    text = _read_text(path)
    try:
        rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    except Exception as exc:
        raise ParserError(f"Could not parse {path.name} as CSV: {exc}") from exc
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ParsedSource(units=[], parser="csv")

    header, data = rows[0], rows[1:]
    if len(data) > _CSV_MAX_ROWS:
        truncated = len(data) - _CSV_MAX_ROWS
        data = data[:_CSV_MAX_ROWS]
        warn = f"Only the first {_CSV_MAX_ROWS} of {len(rows) - 1} data rows were read ({truncated} truncated)."
    else:
        warn = ""

    units: list[SourceUnit] = []
    for start in range(0, max(len(data), 1), _TABULAR_GROUP_ROWS):
        group = data[start : start + _TABULAR_GROUP_ROWS] or []
        if not group:
            # Header-only file: the header itself is the content.
            units.append(
                SourceUnit(index=1, text="Columns: " + ", ".join(header), locator_kind=ROW, locator_value="1:1")
            )
            break
        lines = [" | ".join(f"{h}: {v.strip()}" for h, v in zip(header, r) if v.strip()) for r in group]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        units.append(
            SourceUnit(
                index=len(units) + 1,
                text="\n".join(lines),
                locator_kind=ROW,
                locator_value=f"{start + 2}:{start + 1 + len(group)}",
            )
        )
    return ParsedSource(units=units, parser="csv", warnings=[warn] if warn else [])


def parse_csv_file(path: Path, settings: Settings) -> ParsedSource:
    return parse_csv(path, settings, delimiter=",")


def parse_tsv_file(path: Path, settings: Settings) -> ParsedSource:
    out = parse_csv(path, settings, delimiter="\t")
    out.parser = "tsv"
    return out


# =============================================================================
#  md / txt — heading-delimited sections
# =============================================================================
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")


def parse_md(path: Path, settings: Settings) -> ParsedSource:  # noqa: ARG001
    text = _read_text(path)
    sections: list[dict] = [{"title": path.stem or "Top", "lines": []}]
    for line in text.split("\n"):
        m = _HEADING_RE.match(line)
        if m and any("\n".join(sections[-1]["lines"]).strip()):
            sections.append({"title": m.group(1).strip()[:80], "lines": []})
        elif m and not "\n".join(sections[-1]["lines"]).strip():
            sections[-1]["title"] = m.group(1).strip()[:80]
            sections[-1]["lines"].append(line)
        else:
            sections[-1]["lines"].append(line)
    units = [
        SourceUnit(index=i + 1, text="\n".join(s["lines"]).strip(), locator_kind=SECTION, locator_value=s["title"] or f"Section {i + 1}")
        for i, s in enumerate(sections)
        if "\n".join(s["lines"]).strip()
    ]
    return ParsedSource(units=units, parser="md")


def parse_txt(path: Path, settings: Settings) -> ParsedSource:  # noqa: ARG001
    text = _read_text(path)
    lines = text.split("\n")
    if len(lines) <= 100:
        chunks = [lines]
    else:
        chunks = [lines[i : i + 100] for i in range(0, len(lines), 100)]
    units = [
        SourceUnit(
            index=i + 1,
            text="\n".join(c).strip(),
            locator_kind=SECTION,
            locator_value=f"lines {i * 100 + 1}-{i * 100 + len(c)}" if len(chunks) > 1 else "full text",
        )
        for i, c in enumerate(chunks)
        if "\n".join(c).strip()
    ]
    return ParsedSource(units=units, parser="txt")
