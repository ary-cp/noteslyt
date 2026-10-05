"""
Adaptive assessment.

    mastery_snapshot()  every topic of a document with the student's P(L)
    pick_target()       the weakest topic (fewest attempts breaks ties, so
                        untouched topics get explored early)
    generate()          MCQs grounded ONLY in that topic's own chunks — every
                        question carries the page it was written from, so a
                        wrong answer links straight to the slide to re-read
    grade()             checks the answer server-side, runs the BKT update,
                        persists the new belief and returns the delta

Answer keys never leave the server until the student has committed.
"""

from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass, field

from ..config import Settings
from ..db import Database, MasteryRecord
from ..llm import Embedder, LLMClient
from ..rag import format_context
from ..vectorstore import VectorStore
from . import bkt

log = logging.getLogger(__name__)

QUIZ_SYSTEM_PROMPT = """You write exam-quality multiple-choice questions strictly from the CONTEXT excerpts provided (lecture material, each labelled [Page N]).
Rules:
- Exactly {n} questions, each testing understanding of the topic "{topic}" — prefer application and reasoning over trivia.
- Each question has exactly 4 options; exactly one is correct; distractors must be plausible misconceptions.
- Every question must be answerable from the CONTEXT alone and must record the page it is based on.
- "explanation": 1-2 sentences saying why the correct option is right, referencing the material.
Output JSON: {{"questions": [{{"question": "...", "options": ["...", "...", "...", "..."], "correct_index": 0, "explanation": "...", "page": 3}}]}}"""


@dataclass
class TopicMastery:
    topic_id: str
    name: str
    description: str
    p_mastery: float
    attempts: int
    correct: int
    label: str

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class Quiz:
    id: str
    topic: TopicMastery
    questions: list[dict]           # public view: no correct_index / explanation
    mastery: list[TopicMastery] = field(default_factory=list)


@dataclass
class GradeResult:
    correct: bool
    correct_index: int
    explanation: str
    page: int
    topic: TopicMastery
    p_before: float
    p_after: float
    mastery: list[TopicMastery]


class QuizService:
    def __init__(self, settings: Settings, llm: LLMClient, embedder: Embedder, store: VectorStore, db: Database):
        self.settings = settings
        self.llm = llm
        self.embedder = embedder
        self.store = store
        self.db = db
        self.params = bkt.BKTParams(settings.bkt_p_init, settings.bkt_p_transit, settings.bkt_p_slip, settings.bkt_p_guess)

    # ----------------------------------------------------------- learner
    def mastery_snapshot(self, user_id: str, document_id: str) -> list[TopicMastery]:
        doc = self.db.get_document(document_id)
        if doc is None:
            raise KeyError(document_id)
        records = self.db.get_mastery(user_id, document_id)
        out: list[TopicMastery] = []
        for t in doc.topics:
            rec = records.get(t["id"])
            p = rec.p_mastery if rec else self.params.p_init
            out.append(TopicMastery(
                topic_id=t["id"], name=t["name"], description=t.get("description", ""),
                p_mastery=p, attempts=rec.attempts if rec else 0, correct=rec.correct if rec else 0, label=bkt.label(p),
            ))
        return out

    @staticmethod
    def pick_target(snapshot: list[TopicMastery]) -> TopicMastery:
        """Weakest belief first; among equals, the least-practised topic."""
        return min(snapshot, key=lambda t: (round(t.p_mastery, 3), t.attempts))

    # ---------------------------------------------------------- generate
    def generate(self, user_id: str, document_id: str, topic_id: str | None = None, n_questions: int | None = None) -> Quiz:
        n = n_questions or self.settings.quiz_questions
        snapshot = self.mastery_snapshot(user_id, document_id)
        target = next((t for t in snapshot if t.topic_id == topic_id), None) if topic_id else None
        target = target or self.pick_target(snapshot)

        chunks = self.store.get_by_topic(document_id, target.topic_id, limit=8)
        if len(chunks) < 3:
            # Thin topic: keep its own chunks first, then widen the net with a
            # semantic search on the topic description for extra context.
            vec = self.embedder.embed([f"{target.name}: {target.description}"])[0]
            seen = {c.id for c in chunks}
            extra = [c for c in self.store.query(vec, k=6, document_id=document_id) if c.id not in seen]
            chunks = chunks + extra[: max(0, 6 - len(chunks))]
        if not chunks:
            raise ValueError("No material found for this topic.")

        allowed_pages = {c.page for c in chunks}
        data = self.llm.complete_json(
            QUIZ_SYSTEM_PROMPT.format(n=n, topic=target.name),
            f"CONTEXT:\n{format_context(chunks)}",
            max_tokens=2500,
        )
        questions = self._validate(data.get("questions", []), allowed_pages, fallback_page=chunks[0].page)[:n]
        if not questions:
            raise ValueError("The model returned no usable questions — try again.")

        quiz_id = uuid.uuid4().hex
        self.db.save_quiz(quiz_id, user_id, document_id, target.topic_id, questions)
        public = [{k: q[k] for k in ("id", "question", "options", "page")} for q in questions]
        return Quiz(id=quiz_id, topic=target, questions=public, mastery=snapshot)

    @staticmethod
    def _validate(raw_questions: list, allowed_pages: set[int], fallback_page: int) -> list[dict]:
        rng = random.Random()
        out: list[dict] = []
        for raw in raw_questions:
            try:
                options = [str(o).strip() for o in raw["options"]]
                correct = int(raw["correct_index"])
                if len(options) != 4 or not (0 <= correct < 4) or not str(raw.get("question", "")).strip():
                    continue
                # Shuffle so the correct option is not always "A".
                order = list(range(4))
                rng.shuffle(order)
                page = int(raw.get("page", fallback_page))
                out.append({
                    "id": uuid.uuid4().hex[:8],
                    "question": str(raw["question"]).strip(),
                    "options": [options[i] for i in order],
                    "correct_index": order.index(correct),
                    "explanation": str(raw.get("explanation", "")).strip(),
                    "page": page if page in allowed_pages else fallback_page,
                })
            except (KeyError, TypeError, ValueError):
                continue
        return out

    # ------------------------------------------------------------- grade
    def grade(self, user_id: str, quiz_id: str, question_id: str, selected_index: int) -> GradeResult:
        quiz = self.db.get_quiz(quiz_id)
        if quiz is None or quiz["user_id"] != user_id:
            raise KeyError(quiz_id)
        question = next((q for q in quiz["questions"] if q["id"] == question_id), None)
        if question is None:
            raise KeyError(question_id)

        correct = int(selected_index) == question["correct_index"]
        document_id, topic_id = quiz["document_id"], quiz["topic_id"]

        rec = self.db.get_mastery(user_id, document_id).get(topic_id) or MasteryRecord(
            user_id=user_id, document_id=document_id, topic_id=topic_id, p_mastery=self.params.p_init
        )
        p_before = rec.p_mastery
        rec.p_mastery = bkt.update(p_before, correct, self.params)
        rec.attempts += 1
        rec.correct += int(correct)
        self.db.upsert_mastery(rec)

        snapshot = self.mastery_snapshot(user_id, document_id)
        topic = next(t for t in snapshot if t.topic_id == topic_id)
        return GradeResult(
            correct=correct, correct_index=question["correct_index"], explanation=question["explanation"],
            page=question["page"], topic=topic, p_before=p_before, p_after=rec.p_mastery, mastery=snapshot,
        )
