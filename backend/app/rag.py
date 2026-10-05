"""
Strict source-grounded RAG.

Three layers of defence against hallucination, in order of cost:

  1. Retrieval gate   — if the best chunk's cosine distance is above
                        MAX_DISTANCE we refuse *before* calling the LLM.
  2. Prompt contract  — the system prompt forbids outside knowledge, requires
                        a `[Page N]` citation after every claim, and defines
                        one exact refusal sentence.
  3. Citation audit   — after generation we parse every `[Page N]`, drop any
                        page that was not in the retrieved context (a model
                        cannot cite what it did not see), and retry once if the
                        answer carries no valid citation at all.

The frontend turns every surviving `[Page N]` into a clickable chip that
scrolls the PDF viewer to that page.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .config import Settings
from .llm import Embedder, LLMClient
from .vectorstore import RetrievedChunk, VectorStore

log = logging.getLogger(__name__)

REFUSAL = "I can't find this in your uploaded material."
CITATION_RE = re.compile(r"\[\s*Page\s+(\d+)\s*\]", re.IGNORECASE)

SYSTEM_PROMPT = f"""You are a precise study tutor. The student has uploaded their lecture material, and excerpts from it are provided as CONTEXT, each labelled with the page it comes from.

Rules you must follow without exception:
1. Answer ONLY from the CONTEXT. Do not use outside knowledge, even if you are confident.
2. After every factual statement, cite its page in exactly this form: [Page N]. Use only page numbers that appear in the CONTEXT labels. Multiple citations are fine: [Page 3] [Page 4].
3. If the CONTEXT does not contain what is needed to answer, reply with exactly this sentence and nothing else:
{REFUSAL}
4. Never guess, never pad, never apologise. Be concise and use Markdown (short paragraphs, lists, **bold** key terms, LaTeX with $...$ for maths if the material uses it).
5. Explain like a good tutor: define, then give the key idea, then (if present in the context) an example."""


@dataclass
class Citation:
    page: int
    heading: str
    snippet: str


@dataclass
class ChatResult:
    answer: str
    citations: list[Citation]
    grounded: bool                            # False when we refused
    retrieved_pages: list[int] = field(default_factory=list)
    best_distance: float | None = None


def format_context(chunks: list[RetrievedChunk]) -> str:
    """Each excerpt is prefixed with the label the model must cite."""
    blocks = []
    for c in chunks:
        heading = f" — {c.heading}" if c.heading else ""
        blocks.append(f"[Page {c.page}]{heading}\n{c.text}")
    return "\n\n---\n\n".join(blocks)


def audit_citations(answer: str, chunks: list[RetrievedChunk]) -> tuple[str, list[Citation]]:
    """
    Keep only citations whose page was actually retrieved; strip the rest.
    Returns (cleaned_answer, ordered unique citations).
    """
    allowed = {c.page for c in chunks}
    by_page: dict[int, RetrievedChunk] = {}
    for c in chunks:                         # best-ranked chunk per page wins
        by_page.setdefault(c.page, c)

    seen: list[int] = []

    def _replace(match: re.Match) -> str:
        page = int(match.group(1))
        if page not in allowed:
            log.warning("Dropped hallucinated citation [Page %d]", page)
            return ""
        if page not in seen:
            seen.append(page)
        return f"[Page {page}]"               # normalise spacing/case

    cleaned = CITATION_RE.sub(_replace, answer)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:!?])", r"\1", cleaned).strip()   # tidy "word ." left by a dropped citation
    citations = [
        Citation(page=p, heading=by_page[p].heading, snippet=by_page[p].text[:180].replace("\n", " ").strip())
        for p in seen
    ]
    return cleaned, citations


class RAGService:
    def __init__(self, settings: Settings, llm: LLMClient, embedder: Embedder, store: VectorStore):
        self.settings = settings
        self.llm = llm
        self.embedder = embedder
        self.store = store

    def answer(self, question: str, document_id: str, history: list[dict[str, str]] | None = None) -> ChatResult:
        # -- 1. retrieve ---------------------------------------------------
        query_vec = self.embedder.embed([question])[0]
        chunks = self.store.query(query_vec, k=self.settings.top_k, document_id=document_id)
        best = chunks[0].distance if chunks else None

        if not chunks or (best is not None and best > self.settings.max_distance):
            log.info("Refusing: best distance %s > %s", best, self.settings.max_distance)
            return ChatResult(REFUSAL, [], grounded=False, retrieved_pages=[], best_distance=best)

        # -- 2. generate ---------------------------------------------------
        context = format_context(chunks)
        user_message = f"CONTEXT:\n{context}\n\nSTUDENT QUESTION: {question}"
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Prior turns give conversational continuity ("explain that again
        # more simply") but retrieval always uses the *current* question.
        for turn in (history or [])[-self.settings.history_turns :]:
            if turn.get("role") in {"user", "assistant"} and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_message})

        raw = self.llm.chat(messages)

        # -- 3. audit ------------------------------------------------------
        pages = sorted({c.page for c in chunks})
        if _is_refusal(raw):
            return ChatResult(REFUSAL, [], grounded=False, retrieved_pages=pages, best_distance=best)

        answer, citations = audit_citations(raw, chunks)
        if not citations:
            # One corrective retry: the model answered but forgot to cite.
            log.info("No valid citations — retrying with reminder")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Your answer had no valid [Page N] citations. Rewrite it, citing the page label after every statement. If the context does not support the answer, reply with the exact refusal sentence."})
            retry = self.llm.chat(messages)
            if _is_refusal(retry):
                return ChatResult(REFUSAL, [], grounded=False, retrieved_pages=pages, best_distance=best)
            answer, citations = audit_citations(retry, chunks)

        return ChatResult(
            answer=answer,
            citations=citations,
            grounded=bool(citations),
            retrieved_pages=pages,
            best_distance=best,
        )


def _is_refusal(text: str) -> bool:
    """Tolerates trailing punctuation / quotes around the mandated sentence."""
    norm = re.sub(r"[^a-z ]", "", text.strip().lower())
    return norm.startswith(re.sub(r"[^a-z ]", "", REFUSAL.lower()))
