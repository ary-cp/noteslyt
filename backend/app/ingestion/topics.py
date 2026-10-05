"""
Knowledge-component ("topic") extraction and assignment.

The adaptive engine needs discrete skills to track. We derive them from the
material itself in two cheap steps:

  1. ONE LLM call reads a compact outline of the document (headings + first
     lines of every page) and returns 4-8 topics as JSON.
  2. Every chunk is assigned to its nearest topic by cosine similarity between
     the chunk embedding (already computed for RAG — free) and the embedding of
     "<topic name>: <description>". No per-chunk LLM calls, so ingestion stays
     fast even for 100-page decks.

If the LLM output is unusable, we fall back to page-range buckets so the quiz
still works — a demo must never depend on a model being in a good mood.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, asdict

import numpy as np

from ..llm import Embedder, LLMClient
from .parsers import ParsedPage

log = logging.getLogger(__name__)

TOPIC_SYSTEM_PROMPT = """You are an instructional designer building a syllabus from lecture material.
Identify the distinct knowledge components (topics) a student must master.
Rules:
- Between 4 and {max_topics} topics, ordered as they appear in the material.
- Each topic needs: "id" (short kebab-case slug), "name" (3-6 words), "description" (one sentence describing what the student should be able to do), "pages" (list of page numbers where it is covered).
- Topics must be specific to THIS material, not generic ("Introduction" is bad; "Eigenvalues of symmetric matrices" is good).
Output JSON of the form: {{"topics": [{{"id": "...", "name": "...", "description": "...", "pages": [1, 2]}}]}}"""


@dataclass
class Topic:
    id: str
    name: str
    description: str
    pages: list[int]

    def to_dict(self) -> dict:
        return asdict(self)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "topic"


def build_outline(pages: list[ParsedPage], max_chars: int = 7000) -> str:
    """Headings + opening text of each page — enough signal, few tokens."""
    lines: list[str] = []
    for page in pages:
        text = page.markdown.strip()
        if not text:
            continue
        headings = [h.strip("# ").strip() for h in re.findall(r"^\s{0,3}#{1,6}\s+.*$", text, flags=re.MULTILINE)]
        snippet = re.sub(r"\s+", " ", re.sub(r"^\s{0,3}#{1,6}\s+.*$", "", text, flags=re.MULTILINE)).strip()[:160]
        head = " | ".join(headings[:4])
        lines.append(f"[Page {page.page_number}] {head}{' — ' if head and snippet else ''}{snippet}")
    outline = "\n".join(lines)
    return outline[:max_chars]


def fallback_topics(pages: list[ParsedPage], max_topics: int = 6) -> list[Topic]:
    """Deterministic page-range buckets when the LLM cannot produce topics."""
    numbered = [p.page_number for p in pages if p.markdown.strip()] or [1]
    n_buckets = max(1, min(max_topics, math.ceil(len(numbered) / 3)))
    size = math.ceil(len(numbered) / n_buckets)
    topics: list[Topic] = []
    for i in range(n_buckets):
        bucket = numbered[i * size : (i + 1) * size]
        if not bucket:
            continue
        label = f"Pages {bucket[0]}-{bucket[-1]}" if len(bucket) > 1 else f"Page {bucket[0]}"
        topics.append(Topic(id=f"section-{i + 1}", name=label, description=f"Material covered on {label.lower()}.", pages=bucket))
    return topics


def extract_topics(llm: LLMClient, pages: list[ParsedPage], max_topics: int = 8) -> list[Topic]:
    outline = build_outline(pages)
    valid_pages = {p.page_number for p in pages}
    try:
        data = llm.complete_json(
            TOPIC_SYSTEM_PROMPT.format(max_topics=max_topics),
            f"Lecture outline (one line per page):\n{outline}",
        )
        topics: list[Topic] = []
        seen: set[str] = set()
        for raw in data.get("topics", []):
            name = str(raw.get("name", "")).strip()
            if not name:
                continue
            tid = _slugify(str(raw.get("id") or name))
            if tid in seen:
                tid = f"{tid}-{len(seen) + 1}"
            seen.add(tid)
            pages_list = sorted({int(p) for p in raw.get("pages", []) if str(p).lstrip("-").isdigit() and int(p) in valid_pages})
            topics.append(Topic(id=tid, name=name[:80], description=str(raw.get("description", "")).strip()[:300], pages=pages_list))
        if len(topics) >= 2:
            return topics[:max_topics]
        log.warning("Topic extraction returned %d topics; using fallback", len(topics))
    except Exception as exc:  # noqa: BLE001
        log.warning("Topic extraction failed (%s); using page-range fallback", exc)
    return fallback_topics(pages)


def assign_topics(
    chunk_embeddings: list[list[float]],
    chunk_pages: list[int],
    topics: list[Topic],
    embedder: Embedder,
    page_bonus: float = 0.15,
) -> list[str]:
    """
    Nearest-centroid assignment: returns one topic id per chunk.

    Similarity is semantic (cosine) plus a small bonus when the chunk's page is
    one the LLM explicitly listed for that topic — the two signals together are
    far more reliable than either alone.
    """
    if not chunk_embeddings:
        return []
    if len(topics) == 1:
        return [topics[0].id] * len(chunk_embeddings)

    topic_vecs = np.asarray(embedder.embed([f"{t.name}: {t.description}" for t in topics]), dtype=np.float32)
    chunk_vecs = np.asarray(chunk_embeddings, dtype=np.float32)

    # cosine similarity = normalised dot product
    topic_vecs /= np.linalg.norm(topic_vecs, axis=1, keepdims=True) + 1e-9
    chunk_vecs /= np.linalg.norm(chunk_vecs, axis=1, keepdims=True) + 1e-9
    sims = chunk_vecs @ topic_vecs.T                      # (n_chunks, n_topics)

    for t_idx, topic in enumerate(topics):
        if topic.pages:
            page_set = set(topic.pages)
            for c_idx, page in enumerate(chunk_pages):
                if page in page_set:
                    sims[c_idx, t_idx] += page_bonus

    best = sims.argmax(axis=1)
    return [topics[int(i)].id for i in best]
