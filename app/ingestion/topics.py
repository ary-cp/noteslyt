"""
Topic extraction and assignment — project-level.

The adaptive engine needs discrete skills to track, so we derive them from the
material in two cheap steps:

  1. ONE LLM call reads a compact outline of the source (headings + the opening
     text of every unit) and returns 4-8 topics as JSON, each naming the locators
     where it is covered, plus 2-5 subtopics (each with its own locators and
     key concepts).
  2. Every chunk is assigned to its nearest topic by cosine similarity between the
     chunk embedding (already computed for RAG — free) and the embedding of
     "<topic name>: <description>". No per-chunk LLM calls, so ingestion stays
     fast even for 100-page decks.

**Topics belong to the project, not the file.** A topic's natural key is
`(project_id, slug)`, so uploading a slide deck that covers entropy into a project
that already has a PDF about entropy reuses the *same* topic row instead of
creating a duplicate. That is what keeps a project's "topics I am weak at" list
coherent across sources (`plan.md` section 1).

If the LLM output is unusable we keep one clearly-labeled unclassified topic.
Locator ranges are never presented as semantic topics.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import asdict, dataclass, field

import numpy as np

from ..llm import Embedder, LLMClient
from .units import SourceUnit

log = logging.getLogger(__name__)

# `locators` in the response replaces the old `pages` list. Examples are given so
# the model emits the right shape for whatever file type it is looking at.
TOPIC_SYSTEM_PROMPT = """You are an instructional designer building a syllabus from study material.
Identify the distinct knowledge components (topics) a student must master.
Inspect the supplied headings and text snippets to infer the actual subject matter. For a textbook, name concepts and principles; for a lecture, name the skills or ideas taught; for history, name events, periods, and causes. Page and slide labels only tell you where evidence occurs.
Rules:
- Return 2 to {max_topics} topics when the source contains enough distinct material; return fewer only for a genuinely narrow source.
- Each topic needs: "id" (short kebab-case slug), "name" (3-6 words), "description"
  (one sentence describing what the student should be able to do), and "locators"
  (list of where it is covered). A locator is {{"kind": <locator kind>, "value": <string>}}
  using the exact kind and value strings shown in the outline below.
- Topics must be specific to THIS material, not generic ("Introduction" is bad;
  "Eigenvalues of symmetric matrices" is good).
- Topic names must describe concepts, methods, people, events, or skills found in the text. Never use page numbers, slide numbers, timestamps, sheet names, or phrases like "page 1-10" as topic names.
- Use headings and evidence from across the outline. Do not invent topics unsupported by the outline.
- Prefer reusing the vocabulary already used in the material for the slug.
- Include at most 3 representative locators per topic and 2 per subtopic; do not enumerate every matching segment.
- Each topic also breaks into 2 to 5 subtopics: a teachable slice with its own
  id, name, description, locators (same shape, a subset of the evidence) and concepts
  (up to 6 key terms, formulas or facts, as short strings).
Output JSON of the form:
{{"topics": [{{"id": "...", "name": "...", "description": "...", "locators": [{{"kind": "page", "value": "3"}}], "subtopics": [{{"id": "...", "name": "...", "description": "...", "locators": [{{"kind": "page", "value": "3"}}], "concepts": ["..."]}}]}}]}}"""


@dataclass
class Topic:
    """A proposed topic. `slug` is the stable project-level key, not a row id."""

    slug: str
    name: str
    description: str = ""
    locators: list[dict] = field(default_factory=list)
    subtopics: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "topic"


def build_outline(units: list[SourceUnit], max_chars: int = 18000) -> str:
    """Headings and representative text sampled across the whole source."""
    lines: list[str] = []
    usable = [unit for unit in units if unit.text.strip()]
    if len(usable) > 60:
        stride = (len(usable) - 1) / 59
        usable = [usable[round(i * stride)] for i in range(60)]
    for unit in usable:
        text = unit.text.strip()
        headings = [h.strip("# ").strip() for h in re.findall(r"^\s{0,3}#{1,6}\s+.*$", text, flags=re.MULTILINE)]
        snippet = re.sub(
            r"\s+", " ", re.sub(r"^\s{0,3}#{1,6}\s+.*$", "", text, flags=re.MULTILINE)
        ).strip()
        if len(snippet) > 220:
            snippet = snippet[:150] + " … " + snippet[-60:]
        head = " | ".join(headings[:4])
        lines.append(
            f"[{unit.locator_kind}:{unit.locator_value}] {head}{' — ' if head and snippet else ''}{snippet}"
        )
    return "\n".join(lines)[:max_chars]


def fallback_topics(units: list[SourceUnit], max_topics: int = 6) -> list[Topic]:
    """Return one explicit catch-all; never disguise locators as concepts."""
    locators = [{"kind": u.locator_kind, "value": u.locator_value} for u in units if not u.is_empty]
    return [Topic(
        slug="unclassified-material",
        name="Unclassified material",
        description="Topic extraction could not identify concepts for this source yet.",
        locators=locators[:200],
        subtopics=[],
    )]


def _valid_locator(raw: object, valid: set[tuple[str, str]]) -> dict | None:
    """Keep a locator only if it matches a locator that actually exists."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind", "")).strip() or "section"
    value = str(raw.get("value", "")).strip()
    if not value:
        return None
    if valid and (kind, value) not in valid:
        return None
    return {"kind": kind, "value": value}


def extract_topics(
    llm: LLMClient,
    units: list[SourceUnit],
    max_topics: int = 8,
    existing_slugs: list[str] | None = None,
    image_url: str | None = None,
) -> list[Topic]:
    """
    Propose topics for one source.

    `existing_slugs` are the project's current topics. They are passed in the
    prompt so the model prefers extending the project's vocabulary over
    inventing a near-duplicate for what is really the same concept.
    """
    outline = build_outline(units)
    valid = {(u.locator_kind, u.locator_value) for u in units if not u.is_empty}

    hint = ""
    if existing_slugs:
        hint = (
            "\nThis project already has these topics — reuse one of these slugs if the "
            f"material covers the same concept: {', '.join(existing_slugs[:24])}"
        )

    request = f"Material outline (one line per {sorted({k for k, _ in valid}) or ['unit']}):\n{outline}{hint}"
    if image_url:
        request = [{"type": "text", "text": request + "\nRead the original image to identify topics. Return only topic metadata, never a caption or transcription. If no teachable content is legible, return an empty topics list."},
                   {"type": "image_url", "image_url": {"url": image_url}}]
    try:
        try:
            data = llm.complete_json(
                TOPIC_SYSTEM_PROMPT.format(max_topics=max_topics), request,
                max_tokens=min(12288, max(4096, max_topics * 1024)),
            )
        except ValueError:
            # Long topic/subtopic trees can exceed output budgets or produce
            # malformed JSON. One compact retry is preferable to losing the
            # entire syllabus; provider failures still use the outer fallback.
            data = llm.complete_json(
                TOPIC_SYSTEM_PROMPT.format(max_topics=min(max_topics, 4)) +
                "\nKeep output compact: at most 4 topics, exactly 2 subtopics per topic, "
                "one representative locator per item, and descriptions under 12 words.",
                request, max_tokens=4096, json_mode=True,
            )
        topics: list[Topic] = []
        seen: set[str] = set()
        for raw in data.get("topics", []):
            name = str(raw.get("name", "")).strip()
            if not name:
                continue
            slug = _slugify(str(raw.get("id") or name))
            if slug in seen:
                slug = f"{slug}-{len(seen) + 1}"
            seen.add(slug)
            locators = [
                loc
                for loc in (_valid_locator(raw_loc, valid) for raw_loc in raw.get("locators", []) or [])
                if loc
            ]
            subs = _valid_subtopics(raw.get("subtopics"), valid)
            topics.append(
                Topic(
                    slug=slug,
                    name=name[:80],
                    description=str(raw.get("description", "")).strip()[:300],
                    locators=locators,
                    subtopics=subs,
                )
            )
        semantic = [t for t in topics if not re.fullmatch(
            r"(?:page|slide|sheet|segment|unit)\s+\S+", t.name.strip(), flags=re.IGNORECASE
        )]
        if semantic:
            return semantic[:max_topics]
        log.warning("Topic extraction returned no semantic topics; using unclassified fallback")
    except Exception as exc:  # noqa: BLE001 — a demo must not depend on the LLM
        log.warning("Topic extraction failed (%s); using unclassified fallback", exc)
    return [] if image_url else fallback_topics(units)


def assign_topics(
    chunk_embeddings: list[list[float]],
    chunk_locators: list[tuple[str, str]],
    topics: list[Topic],
    embedder: Embedder,
    locator_bonus: float = 0.05,
) -> list[str]:
    """
    Nearest-centroid assignment: returns one topic **slug** per chunk.

    Similarity is semantic (cosine) plus a small bonus when the chunk's locator
    is one the LLM explicitly listed for that topic. The two signals together are
    far more reliable than either alone.
    """
    if not chunk_embeddings:
        return []
    if not topics:
        return []
    if len(topics) == 1:
        return [topics[0].slug] * len(chunk_embeddings)

    topic_vecs = np.asarray(
        embedder.embed([f"{t.name}: {t.description}" for t in topics]), dtype=np.float32
    )
    chunk_vecs = np.asarray(chunk_embeddings, dtype=np.float32)
    if topic_vecs.ndim != 2 or topic_vecs.size == 0:
        return [topics[0].slug] * len(chunk_embeddings)

    # cosine similarity = normalised dot product
    topic_vecs /= np.linalg.norm(topic_vecs, axis=1, keepdims=True) + 1e-9
    chunk_vecs /= np.linalg.norm(chunk_vecs, axis=1, keepdims=True) + 1e-9
    sims = chunk_vecs @ topic_vecs.T                      # (n_chunks, n_topics)

    for t_idx, topic in enumerate(topics):
        if topic.locators:
            listed = {(loc.get("kind"), loc.get("value")) for loc in topic.locators}
            for c_idx, locator in enumerate(chunk_locators):
                if locator in listed:
                    sims[c_idx, t_idx] += locator_bonus

    best = sims.argmax(axis=1)
    return [topics[int(i)].slug for i in best]


def _valid_subtopic(raw: object, valid: set[tuple[str, str]]) -> dict | None:
    """One subtopic entry: slug, names, up to 6 concepts, locators that exist."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    if not name:
        return None
    locators = [
        loc
        for loc in (_valid_locator(raw_loc, valid) for raw_loc in raw.get("locators", []) or [])
        if loc
    ]
    concepts = [
        str(c).strip()[:80]
        for c in (raw.get("concepts", []) or [])
        if str(c).strip()
    ][:6]
    return {
        "slug": _slugify(str(raw.get("id") or name)),
        "name": name[:80],
        "description": str(raw.get("description", "")).strip()[:300],
        "concepts": concepts,
        "locators": locators[:60],
    }


def _valid_subtopics(raw: object, valid: set[tuple[str, str]]) -> list[dict]:
    """At most 5 subtopics; empty when the model invents nothing usable."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for entry in raw[:5]:
        sub = _valid_subtopic(entry, valid)
        if sub is None or sub["slug"] in seen:
            continue
        seen.add(sub["slug"])
        out.append(sub)
    return out


def subtopic_for_locator(subtopics: list[dict], locator: tuple[str, str]) -> str | None:
    """Slug of the subtopic evidencing `locator`, if any (unit to subtopic tag)."""
    kind, value = locator
    for sub in subtopics or []:
        for loc in sub.get("locators", []) or []:
            if (str(loc.get("kind")), str(loc.get("value"))) == (kind, value):
                return str(sub.get("slug", ""))
    return None


def merge_locators(existing: list[dict] | None, incoming: list[dict]) -> list[dict]:
    """
    Union of locator lists, deduped, order-preserving.

    Used when a second source in the project also covers a topic: the topic's
    "where is this covered" grows instead of being overwritten.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for loc in list(existing or []) + list(incoming or []):
        key = (str(loc.get("kind")), str(loc.get("value")))
        if key not in seen:
            seen.add(key)
            out.append({"kind": key[0], "value": key[1]})
    return out


def topics_to_json(topics: list[Topic]) -> str:  # pragma: no cover - debugging aid
    return json.dumps([t.to_dict() for t in topics], indent=2)


PREREQ_SYSTEM_PROMPT = """You order course topics by learning dependency.
Given the project's topics (slug, name, one-line description each), decide which
topics a student must understand BEFORE each other topic. A prerequisite means
"cannot be learned without first learning this" — not merely "related".
Rules:
- Use only the slugs listed below, exactly as written.
- Never list a topic as its own prerequisite.
- Keep the graph sparse: at most 2 prerequisites per topic, only confident links.
- If the topics are independent, return an empty edges list.
Output JSON of the form:
{"edges": [{"topic": "<slug>", "prereq": "<slug>"}]}"""


def infer_prereq_edges(
    llm: LLMClient,
    topics: list[Topic],
) -> list[tuple[str, str]]:
    """Infer (topic_slug, prereq_slug) pairs for a project's topics.

    One LLM call over the whole list. Raises on LLM failure — callers that
    must not fail (ingestion) catch it and keep the old graph.
    """
    known = {t.slug for t in topics}
    if len(known) < 2:
        return []
    listing = "\n".join(f"- {t.slug}: {t.name} — {t.description[:120]}" for t in topics)
    data = llm.complete_json(PREREQ_SYSTEM_PROMPT, f"Topics:\n{listing}")
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    per_topic: dict[str, int] = {}
    for raw in (data.get("edges") or [])[: 2 * len(topics)]:
        if not isinstance(raw, dict):
            continue
        topic, prereq = str(raw.get("topic", "")), str(raw.get("prereq", ""))
        if topic not in known or prereq not in known or topic == prereq:
            continue
        if (topic, prereq) in seen or per_topic.get(topic, 0) >= 2:
            continue
        seen.add((topic, prereq))
        per_topic[topic] = per_topic.get(topic, 0) + 1
        edges.append((topic, prereq))
    return edges
