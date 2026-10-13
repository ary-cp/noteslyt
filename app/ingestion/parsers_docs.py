"""
Document / slide / spreadsheet / text parsers — Phase D.17.

Each function takes a stored file and returns a `ParsedSource` of `SourceUnit`s.
The registry in `registry.py` dispatches on `sources.source_type`; nothing
downstream (chunker, pipeline, topics, citations) knows which parser ran.

Locator discipline (plan.md invariant 1 — a chunk never crosses a locator):
  * docx / md / txt → `section` (one heading-delimited block)
  * pptx            → `slide`   (one slide, speaker notes included)
  * xlsx            → `row` ranges, with sheet name and column labels repeated
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
import io
import logging
import re
from datetime import date, datetime
from html import escape
from pathlib import Path

from ..config import Settings
from .parsers import ParserError
from .units import ROW, SECTION, SHEET, SLIDE, ParsedSource, SourceUnit

log = logging.getLogger(__name__)

# Row-group sizing: one unit per data row would turn a 5k-row export into 5k
# embedding calls, so tabular data is grouped. A group is still ONE locator
# ("Sheet Rows 1-50"), so the chunker invariant holds.
_TABULAR_GROUP_ROWS = 50
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
#  xlsx — row-range units with self-contained column labels
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


def _table_cell(value: str) -> str:
    # Keep a literal pipe or multiline cell inside its Markdown table column.
    return escape(value, quote=False).replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def parse_xlsx(path: Path, settings: Settings) -> ParsedSource:  # noqa: ARG001
    try:
        import openpyxl
    except ImportError as exc:
        raise ParserError(f"openpyxl is not installed: {exc}") from exc

    wb = formulas = None
    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        formulas = openpyxl.load_workbook(str(path), read_only=True, data_only=False)
    except Exception as exc:
        if wb is not None:
            wb.close()
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
            rows = []
            row_numbers = []
            missing_formulas = 0
            missing_examples = []
            # Bound iteration before reading, including sheets with formatting
            # extending to Excel's final row. Keep original row coordinates.
            last_row_to_read = min(ws.max_row or _XLSX_MAX_ROWS, _XLSX_MAX_ROWS)
            values = ws.iter_rows(max_row=last_row_to_read, values_only=True)
            expressions = formulas[name].iter_rows(max_row=last_row_to_read)
            for row_number, (cached, original) in enumerate(zip(values, expressions), 1):
                row = []
                for value, cell in zip(cached, original):
                    if value is None and cell.data_type == "f":
                        formula = getattr(cell.value, "text", cell.value)
                        value = f"Formula {formula} (result unavailable; not calculated)"
                        missing_formulas += 1
                        if len(missing_examples) < 5:
                            missing_examples.append(cell.coordinate)
                    row.append(_format_cell(value))
                if any(row):
                    rows.append(row)
                    row_numbers.append(row_number)
            if missing_formulas:
                warnings.append(
                    f"Sheet '{name}': {missing_formulas} formula cell(s) have no saved result "
                    f"({', '.join(missing_examples)}). Formula text was preserved, not calculated. "
                    "Recalculate and save the workbook in Excel or LibreOffice, then upload it again for numeric results."
                )
            if (ws.max_row or 0) > _XLSX_MAX_ROWS:
                warnings.append(f"Sheet '{name}': only the first {_XLSX_MAX_ROWS} worksheet rows were read.")
            if not rows:
                continue
            width = max(i + 1 for r in rows for i, value in enumerate(r) if value)
            rows = [r[:width] + [""] * max(0, width - len(r)) for r in rows]

            header, data = rows[0], rows[1:]
            # Unnamed columns still contain source evidence. Give them stable
            # physical column labels instead of silently dropping their cells.
            header = [label or f"Column {i + 1}" for i, label in enumerate(header)]
            # Keep spreadsheet records self-contained for embedding. A whole
            # small sheet serialized as Markdown is later split by character
            # count, often detaching rows from their column names. Instead,
            # repeat column labels on bounded row groups for every sheet.
            for start in range(0, max(len(data), 1), _TABULAR_GROUP_ROWS):
                group = data[start : start + _TABULAR_GROUP_ROWS]
                first_row = row_numbers[start + 1] if group else row_numbers[0]
                last_row = row_numbers[start + len(group)] if group else row_numbers[0]
                lines = [f"Sheet: {name}. Columns: {', '.join(h for h in header if h)}."]
                lines += [
                    " | ".join(f"{h}: {v}" for h, v in zip(header, row) if h and v)
                    for row in group
                ]
                lines = [line for line in lines if line]
                units.append(
                    SourceUnit(
                        index=len(units) + 1,
                        text="\n".join(lines),
                        locator_kind=ROW,
                        locator_value=f"{name}!{first_row}:{last_row}",
                    )
                )
    finally:
        for book in (wb, formulas):
            try:
                book.close()
            except Exception:  # noqa: BLE001
                pass
    return ParsedSource(units=units, parser="xlsx", warnings=warnings)


# =============================================================================
#  csv / tsv — groups of data rows
# =============================================================================
def parse_csv(path: Path, settings: Settings, *, delimiter: str = ",") -> ParsedSource:
    text = _read_text(path)
    try:
        rows = []
        for number, row in enumerate(csv.reader(io.StringIO(text, newline=None), delimiter=delimiter), 1):
            if any(c.strip() for c in row):
                rows.append((number, row))
                if len(rows) > _CSV_MAX_ROWS + 1:
                    break
    except Exception as exc:
        raise ParserError(f"Could not parse {path.name} as CSV: {exc}") from exc
    if not rows:
        return ParsedSource(units=[], parser="csv")

    header_number, header = rows[0]
    data = rows[1:]
    if len(data) > _CSV_MAX_ROWS:
        data = data[:_CSV_MAX_ROWS]
        warn = f"Only the first {_CSV_MAX_ROWS} nonempty data rows were read. Additional rows were truncated."
    else:
        warn = ""
    width = max([len(header), *(len(row) for _, row in data)])
    header = [(header[i].strip() if i < len(header) else "") or f"Column {i + 1}" for i in range(width)]

    units: list[SourceUnit] = []
    for start in range(0, max(len(data), 1), _TABULAR_GROUP_ROWS):
        group = data[start : start + _TABULAR_GROUP_ROWS] or []
        if not group:
            # Header-only file: the header itself is the content.
            units.append(
                SourceUnit(index=1, text="Columns: " + ", ".join(header), locator_kind=ROW, locator_value=f"{header_number}:{header_number}")
            )
            break
        lines = [" | ".join(f"{h}: {v.strip()}" for h, v in zip(header, r) if v.strip()) for _, r in group]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        units.append(
            SourceUnit(
                index=len(units) + 1,
                text="\n".join(lines),
                locator_kind=ROW,
                locator_value=f"{group[0][0]}:{group[-1][0]}",
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
