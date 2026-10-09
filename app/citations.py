"""
Generalized citation labels + audit — Phase D.19.

Chat answers cite locators, not pages: `[Page 14] [Slide 7] [12:34]
[Sheet Operating Costs] [Section Laws] [Row 2:51] [Image 1]`. The contract
from the page-only era is preserved exactly:

  * a citation is stripped server-side unless that locator was actually
    retrieved (plan.md invariant 2);
  * `audit_citations` returns the cleaned answer plus ordered, deduped
    citations for the response payload.

Label table (canonical form the model must emit):

  page    → [Page 14]
  slide   → [Slide 7]
  segment → [12:34]            (m:ss of the unit's start; h:mm:ss when ≥ 1 h)
  sheet   → [Sheet Costs]
  row     → [Row 2:51]
  section → [Section Laws]
  image   → [Image 1]

Parsing order matters: prefixed forms are matched before the bare `[m:ss]`
timestamp, so a `[Row 2:30]` range can never be mistaken for a 2:30 timestamp.
Row ranges always carry their `Row` prefix for exactly this reason.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from .ingestion.units import format_timestamp

log = logging.getLogger(__name__)


class _ChunkLike(Protocol):
    locator_kind: str
    locator_value: str
    heading: str
    text: str
    start_ts: float | None
    source_id: str


@dataclass
class Citation:
    """One surviving citation — what `CitationOut` serializes."""

    locator_kind: str
    locator_value: str
    heading: str = ""
    snippet: str = ""
    page: int | None = None          # set for page locators (back-compat)
    source_id: str | None = None


@dataclass
class AuditResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return bool(self.citations)


def citation_label(locator_kind: str, locator_value: str, start_ts: float | None = None) -> str:
    """Canonical label for a locator — the only form the model may emit."""
    kind = (locator_kind or "").strip().lower()
    value = (locator_value or "").strip()
    if kind == "page":
        return f"[Page {value}]"
    if kind == "slide":
        return f"[Slide {value}]"
    if kind == "segment":
        if start_ts is not None:
            return f"[{format_timestamp(start_ts)}]"
        return f"[Segment {value}]"
    if kind == "sheet":
        return f"[Sheet {value}]"
    if kind == "row":
        return f"[Row {value}]"
    if kind == "section":
        return f"[Section {value}]"
    if kind == "image":
        return f"[Image {value}]"
    return f"[{locator_kind} {value}]"


# One named group per prefixed form + the bare timestamp. Keep in sync with
# `citation_label` above; the audit below depends on the group names.
CITATION_RE = re.compile(
    r"\[\s*Page\s+(?P<page>[^\]]+?)\s*\]"
    r"|\[\s*Slide\s+(?P<slide>[^\]]+?)\s*\]"
    r"|\[\s*Sheet\s+(?P<sheet>[^\]]+?)\s*\]"
    r"|\[\s*Section\s+(?P<section>[^\]]+?)\s*\]"
    r"|\[\s*Row\s+(?P<row>[^\]]+?)\s*\]"
    r"|\[\s*Image\s+(?P<image>[^\]]+?)\s*\]"
    r"|\[\s*Segment\s+(?P<segment>[^\]]+?)\s*\]"
    r"|\[\s*(?P<reference>\d{1,3})\s*\]"
    r"|\[\s*(?P<ts>\d{1,3}:\d{2}(?::\d{2})?)\s*\]",
    re.IGNORECASE,
)


def _resolve(match: re.Match, by_locator: dict, by_ts: dict) -> tuple[str, str] | None:
    """Map a regex match to a retrieved `(kind, value)` key, or None."""
    g = match.groupdict()
    if g["page"]:
        return ("page", g["page"].strip())
    if g["slide"]:
        return ("slide", g["slide"].strip())
    if g["sheet"]:
        return ("sheet", g["sheet"].strip())
    if g["section"]:
        return ("section", g["section"].strip())
    if g["row"]:
        return ("row", g["row"].strip())
    if g["image"]:
        value = g["image"].strip()
        key = ("image", value)
        if key in by_locator:
            return key
        # Vision models often number attached images instead of repeating a
        # filename. Resolve [Image 1] to the first retrieved image while still
        # emitting the canonical filename label in the audited answer.
        if value.isdigit():
            images = [candidate for candidate in by_locator if candidate[0] == "image"]
            index = int(value) - 1
            if 0 <= index < len(images):
                return images[index]
        return key
    if g["segment"]:
        return ("segment", g["segment"].strip())
    if g["reference"]:
        index = int(g["reference"]) - 1
        locators = list(by_locator)
        if 0 <= index < len(locators):
            return locators[index]
    if g["ts"]:
        return by_ts.get(g["ts"].strip())
    return None


def format_context(chunks: list, *, numbered: bool = False) -> str:
    """Each excerpt prefixed with its locator, optionally with a cite number."""
    blocks = []
    references: dict[tuple[str, str], int] = {}
    for c in chunks:
        locator = (c.locator_kind, c.locator_value)
        if locator not in references:
            references[locator] = len(references) + 1
        label = citation_label(c.locator_kind, c.locator_value, getattr(c, "start_ts", None))
        if numbered:
            label = f"[{references[locator]}] {label}"
        heading = f" — {c.heading}" if c.heading else ""
        blocks.append(f"{label}{heading}\n{c.text}")
    return "\n\n---\n\n".join(blocks)


def audit_citations(answer: str, chunks: list) -> AuditResult:
    """
    Strip every citation whose locator was not retrieved.

    Returns the cleaned answer + ordered, deduped citations. Surviving labels
    are normalised to canonical form; dangling punctuation left by a dropped
    citation ("word .") is tidied.
    """
    by_locator: dict[tuple[str, str], object] = {}
    for c in chunks:  # best-ranked chunk per locator wins
        by_locator.setdefault((c.locator_kind, c.locator_value), c)
    by_ts: dict[str, tuple[str, str]] = {}
    for (kind, value), c in by_locator.items():
        if kind == "segment" and getattr(c, "start_ts", None) is not None:
            by_ts.setdefault(format_timestamp(c.start_ts), (kind, value))

    seen: list[tuple[str, str]] = []

    def _replace(match: re.Match) -> str:
        key = _resolve(match, by_locator, by_ts)
        if key is None or key not in by_locator:
            log.warning("Dropped hallucinated citation %r", match.group(0))
            return ""
        if key not in seen:
            seen.append(key)
        chunk = by_locator[key]
        # Present compact numbered refs in answers. Their order is the same as
        # the returned citation list, which the frontend renders as footnotes.
        return f"[{seen.index(key) + 1}]"

    cleaned = CITATION_RE.sub(_replace, answer)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:!?])", r"\1", cleaned).strip()

    citations: list[Citation] = []
    for kind, value in seen:
        chunk = by_locator[(kind, value)]
        page = None
        if kind == "page":
            try:
                page = int(value)
            except (TypeError, ValueError):
                page = None
        citations.append(
            Citation(
                locator_kind=kind,
                locator_value=value,
                heading=getattr(chunk, "heading", "") or "",
                snippet=" ".join(getattr(chunk, "text", "").split())[:180],
                page=page,
                source_id=getattr(chunk, "source_id", None),
            )
        )
    return AuditResult(answer=cleaned, citations=citations)


__all__ = ["AuditResult", "CITATION_RE", "Citation", "audit_citations", "citation_label", "format_context"]
