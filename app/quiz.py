"""
Adaptive assessment over project topics — Phase F.

Flow per quiz:

    pick topics (explicit, weakest-focus, or diagnostic spread on cold start)
    → ground each topic in its chunks → generate (one LLM call)
    → validate answer-key shape and source locator locally (failures are
      regenerated once, then dropped) → novelty filter vs the learner's recent
      stems (F29; cosine ≥ max regenerates once, then drops) → persist.

Flow per answer:

    grade by type (index match | LLM rubric | tolerance) → full BKT update
    (F26 `apply_result`, unlike the bounded chat nudge) → cited feedback
    (F31) → on completion, a post-assessment report row (F31).

Every auxiliary LLM failure degrades to a readable `QuizError`, never a bare
500 — except grading itself, which has no fallback answer to give.
"""

from __future__ import annotations

import logging
import random
import re
from pydantic import BaseModel, ConfigDict

from .citations import citation_label, format_context
from .config import Settings
from .learner.bkt import BKTParams
from .llm import Embedder, LLMClient
from .repo.memory import ProjectMemoryRepo
from .repo.profile import ProfileRepo
from .repo.quizzes import QuizRepo
from .repo.sources import SourceRepo
from .repo.topics import TopicRepo
from .vectorstore import VectorStore

log = logging.getLogger(__name__)

QTYPES = ("mcq", "short_answer", "numerical")
DEFAULT_QTYPES = ("mcq", "short_answer", "numerical")

GENERATE_SYSTEM = """You write quiz questions grounded in study material. Output ONE JSON object:
{"questions": [{"type": "mcq|short_answer|numerical", "topic_slug": "...",
"stem": "...", "options": ["...","...","...","..."], "correct_index": 0,
"key_points": ["..."], "sample_answer": "...",
"value": 0.0, "tolerance": 0.0, "unit": "",
"explanation": "...", "locator": {"kind": "...", "value": "..."}}]}
Rules: exactly the requested number of questions, round-robin over the requested types;
MCQ needs 4 options with exactly one correct; short_answer needs 2-4 key_points plus a
sample_answer; numerical needs value, tolerance and unit ("" if unitless). Every question
must be answerable from the excerpts and cite one locator from the list, using its exact
kind and value. Explanations teach: restate the rule, then the application. Difficulty 0-1."""

FLASHCARD_SYSTEM = """You create study flashcards from the supplied study material. Output ONE JSON object:
{"cards": [{"front": "a concise question or prompt", "back": "a concise accurate answer",
"topic_slug": "...", "locator": {"kind": "...", "value": "..."}}]}
Create exactly the requested number of useful cards. Each card must teach one fact or concept
from the excerpts. Use the exact topic slug and locator supplied. Do not create quiz questions,
multiple-choice options, answer indexes, explanations, or rubrics."""


class _FlashcardLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    value: str


class _GeneratedFlashcard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    front: str
    back: str
    topic_slug: str
    locator: _FlashcardLocator


class _GeneratedFlashcardSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: list[_GeneratedFlashcard]

RUBRIC_SYSTEM = """You grade the answer against the rubric as JSON.
Given the stem, key points, a sample answer and the learner's answer, output
{"correct": true/false, "matched": [...], "missed": [...], "feedback": "..."}.
Correct when the learner states every key point in their own words (synonyms count,
extra correct detail is fine). Feedback is one or two sentences: name what landed,
then the single most important miss, if any."""


class QuizError(RuntimeError):
    """Readable assessment failure (bad topic, empty material, LLM gave up)."""


class QuizNotFoundError(RuntimeError):
    pass


class QuizService:
    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        embedder: Embedder,
        store: VectorStore,
        quizzes: QuizRepo,
        memory: ProjectMemoryRepo,
        topics: TopicRepo,
        sources: SourceRepo,
        profiles: ProfileRepo,
    ):
        self.settings = settings
        self.llm = llm
        self.embedder = embedder
        self.store = store
        self.quizzes = quizzes
        self.memory = memory
        self.topics = topics
        self.sources = sources
        self.profiles = profiles
        self.bkt = BKTParams(
            p_init=settings.bkt_p_init,
            p_transit=settings.bkt_p_transit,
            p_slip=settings.bkt_p_slip,
            p_guess=settings.bkt_p_guess,
        )

    # ================================================================ create
    def create_quiz(
        self,
        user_id: str,
        project_id: str,
        topic_id: str | None = None,
        n_questions: int | None = None,
        qtypes: list[str] | None = None,
        max_questions: int = 10,
        source_ids: list[str] | None = None,
    ) -> dict:
        n = n_questions or self.settings.quiz_questions
        n = max(1, min(n, max_questions))
        types = [t for t in (qtypes or list(DEFAULT_QTYPES)) if t in QTYPES] or ["mcq"]

        ready_ids = self.sources.ready_ids(project_id)
        if source_ids is not None:
            selected = set(source_ids)
            ready_ids = [source_id for source_id in ready_ids if source_id in selected]
        if not ready_ids:
            raise QuizError("Select at least one ready source before generating a quiz.")
        topics = (
            self.topics.list_for_sources(project_id, ready_ids)
            if source_ids is not None
            else self.topics.list_for_project(project_id)
        )
        if not topics:
            raise QuizError("This project has no topics yet — upload material first.")
        by_slug = {t.slug: t for t in topics}
        by_id = {t.id: t for t in topics}

        if topic_id is not None:
            focus = by_id.get(topic_id)
            if focus is None:
                raise QuizNotFoundError(topic_id)
            plan = [(focus, n)]
        else:
            rows = self.memory.list_for_project(user_id, project_id)
            attempts = {r.topic_id: r.attempts for r in rows}
            if all(attempts.get(t.id, 0) == 0 for t in topics):
                # Diagnostic cold start (F31): spread across topics, one each.
                plan = [(t, 1) for t in sorted(topics, key=lambda t: t.name)[:n]]
            else:
                mastery = {r.topic_id: float(r.p_mastery) for r in rows}
                weakest = min(topics, key=lambda t: (mastery.get(t.id, self.bkt.p_init), t.name))
                plan = [(weakest, n)]

        contexts: dict[str, tuple[object, list]] = {}
        # Quiz grounds on processed material only — the same ready boundary as
        # chat. A project mid-upload keeps working from what is already ready.
        for topic, count in plan:
            chunks = self.store.get_by_topic(
                topic.id, project_id=project_id, limit=8, source_ids=ready_ids
            )
            if not chunks:
                vec = self.embedder.embed([f"{topic.name}: {topic.description}"])[0]
                chunks = self.store.query(
                    vec, project_id=project_id, k=self.settings.top_k,
                    source_ids=ready_ids,
                )
            if not chunks:
                raise QuizError(f"No retrievable material for topic '{topic.name}'.")
            contexts[topic.slug] = (topic, chunks)

        # Ask for the learner's requested total in one structured response.
        # The previous fixed batches of five caused unnecessary model calls
        # and made generation slower without improving answer checking.
        generated = self._generate_round(plan, contexts, types, n, avoid=[])
        verified, stats = self._verify_all(generated, contexts)
        kept: list[dict] = list(verified)
        for _ in range(self.settings.quiz_regen_rounds):
            shortfall = n - len(kept)
            if shortfall <= 0:
                break
            regen = self._generate_round(
                plan, contexts, types, shortfall,
                avoid=[q["stem"] for q in kept],
            )
            verified_more, more = self._verify_all(regen, contexts)
            stats["regenerated"] += shortfall
            stats["verification_failed"] += more["verification_failed"]
            kept.extend(verified_more)

        final = self._novelty_filter(user_id, project_id, kept)
        stats["duplicates_dropped"] = len(kept) - len(final)
        kept = final[:n]
        if not kept:
            raise QuizError(
                "Could not produce a verified, novel quiz from this material "
                f"(verification failed: {stats['verification_failed']}, "
                f"duplicates: {stats['duplicates_dropped']}). Try again."
            )

        stems = [q["stem"] for q in kept]
        vectors = self.embedder.embed(stems)
        slugs = {q["topic_slug"] for q in kept}
        single_topic = None
        if len(slugs) == 1:
            only = next(iter(slugs))
            single_topic = by_slug[only].id
        quiz = self.quizzes.create_quiz(project_id, user_id, single_topic)
        out_questions = []
        for seq, (q, vec) in enumerate(zip(kept, vectors)):
            topic = by_slug[q["topic_slug"]]
            source_id = self._source_for(q, contexts)
            if q["type"] == "mcq":
                options, key = _shuffled_options(q["options"], int(q["correct_index"]))
            else:
                options, key = [], {}
            record = self.quizzes.add_question(
                quiz.id, seq,
                topic_id=topic.id, source_id=source_id, qtype=q["type"], stem=q["stem"],
                options=options if q["type"] == "mcq" else [],
                answer_key=_answer_key(q, key),
                explanation=q.get("explanation", ""),
                locator_kind=q["locator"]["kind"], locator_value=q["locator"]["value"],
                difficulty=float(q.get("difficulty", 0.5)),
                stem_embedding=list(vec),
            )
            out_questions.append(_public_question(record, topic.slug))

        focus_row = None
        if single_topic is not None:
            mem = self.memory.list_for_project(user_id, project_id)
            focus_row = next((r for r in mem if r.topic_id == single_topic), None)
        return {
            "quiz_id": quiz.id,
            "topic": _memory_dict(focus_row, self.memory) if focus_row else None,
            "questions": out_questions,
            "verification": {
                "requested": n,
                "generated": stats["generated"],
                "verified": len(kept),
                "regenerated": stats["regenerated"],
                "dropped": stats["verification_failed"] + stats["duplicates_dropped"],
            },
        }

    def create_flashcards(
        self, user_id: str, project_id: str, topic_id: str | None, n_cards: int,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Generate a flashcard artifact directly; do not create a quiz or questions."""
        import uuid

        ready_ids = self.sources.ready_ids(project_id)
        if source_ids is not None:
            selected = set(source_ids)
            ready_ids = [source_id for source_id in ready_ids if source_id in selected]
        if not ready_ids:
            raise QuizError("Select at least one ready source before generating flashcards.")
        topics = (
            self.topics.list_for_sources(project_id, ready_ids)
            if source_ids is not None
            else self.topics.list_for_project(project_id)
        )
        if not topics:
            raise QuizError("This project has no topics yet — upload material first.")
        by_id = {t.id: t for t in topics}
        if topic_id:
            selected = by_id.get(topic_id)
            if selected is None:
                raise QuizNotFoundError(topic_id)
            chosen = [selected]
        else:
            chosen = sorted(topics, key=lambda t: t.name)[:min(8, len(topics))]

        contexts: dict[str, tuple[object, list]] = {}
        for topic in chosen:
            chunks = self.store.get_by_topic(topic.id, project_id=project_id, limit=8, source_ids=ready_ids)
            if not chunks:
                vec = self.embedder.embed([f"{topic.name}: {topic.description}"])[0]
                chunks = self.store.query(vec, project_id=project_id, k=self.settings.top_k, source_ids=ready_ids)
            if chunks:
                contexts[topic.slug] = (topic, chunks)
        if not contexts:
            raise QuizError("No retrievable material is ready for flashcards.")

        sections = []
        for slug, (topic, chunks) in contexts.items():
            locators = sorted({(c.locator_kind, c.locator_value) for c in chunks})
            sections.append(
                f"TOPIC {slug} ({topic.name})\nLOCATORS: "
                + ", ".join(f"{kind}:{value}" for kind, value in locators)
                + "\n" + format_context(chunks)[:3200]
            )
        prompt = (f"Generate exactly {n_cards} flashcards, spread across these topics.\n\n"
                  + "\n\n".join(sections))
        try:
            data = self.llm.complete_structured(
                FLASHCARD_SYSTEM, prompt,
                response_model=_GeneratedFlashcardSet,
                max_tokens=min(12000, max(3000, n_cards * 350)),
            )
        except Exception as exc:
            raise QuizError(f"Structured flashcard generation failed: {exc}") from exc

        cards = []
        for item in data.cards:
            front, back = item.front.strip(), item.back.strip()
            slug = item.topic_slug
            if not front or not back or slug not in contexts:
                continue
            topic, chunks = contexts[slug]
            match = next((c for c in chunks if c.locator_kind == item.locator.kind
                          and str(c.locator_value) == str(item.locator.value)), chunks[0])
            cards.append({
                "id": str(uuid.uuid4()), "front": front[:1000], "back": back[:2000],
                "topic": topic.name, "locator_kind": match.locator_kind,
                "locator_value": match.locator_value,
            })
            if len(cards) == n_cards:
                break
        if len(cards) < n_cards:
            raise QuizError(
                f"The model returned {len(cards)} of {n_cards} usable flashcards. Try again."
            )
        flashcards_id = str(uuid.uuid4())
        log.info("Generated %d/%d flashcards", len(cards), n_cards)
        return {"flashcards_id": flashcards_id, "cards": cards}

    # ================================================================ answer
    def answer(
        self, user_id: str, quiz_id: str, question_id: str, answer: int | float | str,
    ) -> dict:
        quiz = self.quizzes.get_quiz_owned(quiz_id, user_id)
        if quiz is None:
            raise QuizNotFoundError(quiz_id)
        question = self.quizzes.get_question(question_id)
        if question is None or question.quiz_id != quiz.id:
            raise QuizNotFoundError(question_id)

        prior = self.quizzes.find_answer(quiz.id, question.id, user_id)
        topic = self._topic_memory(user_id, quiz.project_id, question.topic_id)
        if prior is not None:  # idempotent replay — feedback recomputed, BKT untouched
            _, _, feedback = self._grade(question, answer)
            payload = self._answer_payload(quiz, question, topic, prior.correct,
                                           prior.given, prior.p_before, prior.p_after)
            payload["feedback"] = feedback
            return payload

        correct, expected, feedback = self._grade(question, answer)
        mem_before = self.memory.get(user_id, quiz.project_id, question.topic_id or "")
        p_before = float(mem_before.p_mastery) if mem_before else self.bkt.p_init
        if question.topic_id:
            updated = self.memory.apply_result(
                user_id, quiz.project_id, question.topic_id, correct, self.bkt
            )
            p_after = float(updated["p_mastery"])
        else:
            p_after = p_before
        self.quizzes.record_answer(
            quiz.id, question.id, user_id, _given_text(answer), correct, p_before, p_after,
        )
        topic = self._topic_memory(user_id, quiz.project_id, question.topic_id)
        payload = self._answer_payload(quiz, question, topic, correct,
                                       _given_text(answer), p_before, p_after)
        payload["feedback"] = feedback
        payload["assessment_id"] = self._maybe_assess(user_id, quiz)
        return payload

    # =============================================================== learner
    def learner(self, user_id: str, project_id: str) -> dict:
        rows = self.memory.list_for_project(user_id, project_id)
        topics = [_memory_dict(r, self.memory) for r in rows]
        weak = sorted(topics, key=lambda t: (t["p_mastery"], t["name"]))[:5]
        coverage = {"not_started": 0, "in_progress": 0, "mastered": 0}
        for t in topics:
            coverage[t["coverage_status"]] = coverage.get(t["coverage_status"], 0) + 1
        return {
            "project_id": project_id,
            "topics": topics,
            "weakest": weak,
            "coverage": coverage,
            "streak": self.memory.streak(user_id, project_id),
            "recent_quizzes": self.quizzes.recent_quizzes(user_id, project_id),
        }

    def report(self, user_id: str, assessment_id: str) -> dict:
        row = self.quizzes.get_assessment_owned(assessment_id, user_id)
        if row is None:
            raise QuizNotFoundError(assessment_id)
        return {
            "id": row.id,
            "project_id": row.project_id,
            "label": row.label,
            "quiz_ids": row.quiz_ids or [],
            "report": row.report or {},
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }

    # ------------------------------------------------------- generation
    def _generate_round(self, plan, contexts, types, n, avoid: list[str]) -> list[dict]:
        sections = []
        for topic, _count in plan:
            _t, chunks = contexts[topic.slug]
            sections.append(
                f"TOPIC {topic.slug} ({topic.name}):\n"
                f"Locators: {', '.join(sorted({(c.locator_kind + ':' + c.locator_value) for c in chunks}))}\n"
                + format_context(chunks)[:4000]
            )
        type_list = ", ".join(types)
        avoid_note = ""
        if avoid:
            avoid_note = "\nDo NOT repeat these stems:\n" + "\n".join(f"- {s[:160]}" for s in avoid[:20])
        request_text = (
            f"Write exactly {n} questions, round-robin over types [{type_list}].\n"
            f"Distribute across the topic sections below.\n{avoid_note}\n\n"
            + "\n\n".join(sections)
        )
        data = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                data = self.llm.complete_json(
                    GENERATE_SYSTEM,
                    request_text if attempt == 0 else (
                        request_text + "\n\nReturn a compact, syntactically valid JSON object only. "
                        "Include every required field for each question and no markdown."
                    ),
                    max_tokens=min(16384, max(4096, n * 700)),
                    json_mode=True,
                )
                break
            except Exception as exc:  # retry malformed/truncated provider output once
                last_error = exc
                log.warning("Structured question output failed on attempt %d: %s", attempt + 1, exc)
        if data is None:
            raise QuizError(f"Question generation failed after structured-output retry: {last_error}") from last_error
        raw = data.get("questions", [])
        valid_shapes = _shape_questions(raw, {t.slug for t, _ in plan}, types)
        log.info("Generated %d/%d well-shaped questions", len(valid_shapes), len(raw))
        return valid_shapes

    def _verify_all(self, questions: list[dict], contexts) -> tuple[list[dict], dict]:
        stats = {"generated": len(questions), "verification_failed": 0, "regenerated": 0}
        kept = []
        for q in questions:
            _topic, chunks = contexts[q["topic_slug"]]
            if self._verify_question(q, chunks):
                kept.append(q)
            else:
                stats["verification_failed"] += 1
        log.info("Quiz structural checks: %d/%d passed", len(kept), len(questions))
        return kept, stats

    def _verify_question(self, q: dict, chunks: list) -> bool:
        """Check response shape, answer-key range and exact source locator locally."""
        locator = q.get("locator") or {}
        if not q.get("stem", "").strip() or q.get("type") not in QTYPES:
            return False
        if not any(getattr(c, "locator_kind", None) == locator.get("kind")
                   and str(getattr(c, "locator_value", "")) == str(locator.get("value"))
                   for c in chunks):
            return False
        if q["type"] == "mcq":
            options = q.get("options")
            key = q.get("correct_index")
            return (isinstance(options, list) and len(options) == 4
                    and all(isinstance(x, str) and x.strip() for x in options)
                    and len({x.strip().casefold() for x in options}) == 4
                    and isinstance(key, int) and not isinstance(key, bool) and 0 <= key < len(options))
        if q["type"] == "short_answer":
            return bool(q.get("key_points")) and bool(str(q.get("sample_answer", "")).strip())
        return isinstance(q.get("value"), (int, float)) and isinstance(q.get("tolerance"), (int, float))

    def _novelty_filter(self, user_id: str, project_id: str, kept: list[dict]) -> list[dict]:
        """Drop stems within cosine ≥ max of the learner's recent stems (F29)."""
        if not kept:
            return kept
        recent = self.quizzes.recent_stems(user_id, project_id)
        if not recent:
            return kept
        import numpy as np

        new_vecs = np.asarray(self.embedder.embed([q["stem"] for q in kept]), dtype=np.float32)
        old_vecs = np.asarray(recent, dtype=np.float32)
        new_vecs /= np.linalg.norm(new_vecs, axis=1, keepdims=True) + 1e-9
        old_vecs /= np.linalg.norm(old_vecs, axis=1, keepdims=True) + 1e-9
        sims = new_vecs @ old_vecs.T
        out, seen_idx = [], []
        for i, q in enumerate(kept):
            if sims[i].max() >= self.settings.quiz_similarity_max:
                log.info("Dropped near-duplicate stem %r", q["stem"][:80])
                continue
            if seen_idx and (new_vecs[i] @ new_vecs[seen_idx].T).max() >= self.settings.quiz_similarity_max:
                log.info("Dropped within-quiz duplicate stem %r", q["stem"][:80])
                continue
            seen_idx.append(i)
            out.append(q)
        return out

    # ---------------------------------------------------------- grading
    def _grade(self, question, answer: int | float | str) -> tuple[bool, object, str]:
        if question.qtype == "mcq":
            if isinstance(answer, bool) or not isinstance(answer, int):
                raise QuizError("MCQ answers must be an option index (integer).")
            key = (question.answer_key or {}).get("correct_index")
            options = question.options or []
            if not 0 <= answer < len(options):
                raise QuizError(f"Option index out of range (0–{len(options) - 1}).")
            correct = answer == key
            return correct, options[key] if key is not None and key < len(options) else None, (
                f"Correct. {question.explanation}" if correct
                else f"Not quite — the answer is {options[key] if key is not None else '?'}. {question.explanation}"
            ).strip()
        if question.qtype == "short_answer":
            if not isinstance(answer, str) or not answer.strip():
                raise QuizError("Short answers must be non-empty text.")
            return self._grade_short(question, answer.strip())
        if question.qtype == "numerical":
            value = _parse_number(answer)
            if value is None:
                raise QuizError("Numerical answers must be a number.")
            key = question.answer_key or {}
            target = float(key.get("value", 0.0))
            tol = float(key.get("tolerance", self.settings.quiz_default_tolerance))
            unit = str(key.get("unit", ""))
            correct = abs(value - target) <= tol
            ukey = f" {unit}" if unit else ""
            return correct, f"{target}{ukey}", (
                f"Correct: {value}{ukey} is within ±{tol} of {target}{ukey}. {question.explanation}"
                if correct else
                f"You answered {value}{ukey}; the value is {target}{ukey} (±{tol}). {question.explanation}"
            ).strip()
        raise QuizError(f"Unknown question type '{question.qtype}'.")

    def _grade_short(self, question, answer: str) -> tuple[bool, object, str]:
        key = question.answer_key or {}
        try:
            data = self.llm.complete_json(
                RUBRIC_SYSTEM,
                f"STEM: {question.stem}\nKEY POINTS: {key.get('key_points', [])}\n"
                f"SAMPLE ANSWER: {key.get('sample_answer', '')}\nLEARNER ANSWER: {answer}",
                max_tokens=512,
                json_mode=True,
            )
        except Exception as exc:
            raise QuizError(f"Short-answer grading is unavailable right now: {exc}") from exc
        correct = bool(data.get("correct", False))
        sample = str(key.get("sample_answer", ""))
        feedback = str(data.get("feedback", "")).strip()
        return correct, sample, (
            f"{feedback} {question.explanation}".strip() if feedback else question.explanation
        )

    # ---------------------------------------------------------- payloads
    def _topic_memory(self, user_id: str, project_id: str, topic_id: str | None) -> dict | None:
        if not topic_id:
            return None
        rows = self.memory.list_for_project(user_id, project_id)
        row = next((r for r in rows if r.topic_id == topic_id), None)
        return _memory_dict(row, self.memory) if row else None

    def _source_for(self, q: dict, contexts) -> str | None:
        _topic, chunks = contexts[q["topic_slug"]]
        for c in chunks:
            if (c.locator_kind, c.locator_value) == (q["locator"]["kind"], q["locator"]["value"]):
                return c.source_id
        return chunks[0].source_id if chunks else None

    def _answer_payload(self, quiz, question, topic, correct, given, p_before, p_after) -> dict:
        source_name = None
        if question.source_id:
            row = self.sources.get(question.source_id)
            source_name = row.filename if row else None
        assessment = self.quizzes.find_assessment_for_quiz(quiz.id, quiz.user_id)
        return {
            "correct": correct,
            "given": given,
            "expected": _expected_answer(question),
            "explanation": question.explanation,
            "feedback": "",
            "citation": {
                "label": citation_label(question.locator_kind, question.locator_value),
                "source": source_name,
            },
            "p_before": p_before,
            "p_after": p_after,
            "topic": topic,
            "assessment_id": assessment.id if assessment else None,
        }

    def _maybe_assess(self, user_id: str, quiz) -> str | None:
        """On the final answer, freeze a post-assessment report (F31)."""
        questions = self.quizzes.list_questions(quiz.id)
        answers = {a.question_id: a for a in self.quizzes.answers_for_quiz(quiz.id, user_id)}
        if len(answers) < len(questions) or not questions:
            return None
        existing = self.quizzes.find_assessment_for_quiz(quiz.id, user_id)
        if existing is not None:
            return existing.id
        detail = []
        deltas: dict[str, dict] = {}
        for q in questions:
            a = answers[q.id]
            tm = self._topic_memory(user_id, quiz.project_id, q.topic_id)
            d = deltas.setdefault(q.topic_id or "", {"slug": "", "before": [], "after": []})
            if tm:
                d["slug"] = tm["slug"]
            d["before"].append(a.p_before)
            d["after"].append(a.p_after)
            detail.append({
                "question_id": q.id, "stem": q.stem, "qtype": q.qtype,
                "given": a.given, "correct": a.correct,
                "citation": citation_label(q.locator_kind, q.locator_value),
            })
        correct = sum(1 for q in questions if answers[q.id].correct)
        mem = self.memory.list_for_project(user_id, quiz.project_id)
        weakest = min(mem, key=lambda r: (float(r.p_mastery), r.name)) if mem else None
        label = f"Quiz — {correct}/{len(questions)} correct"
        report = {
            "score": {"correct": correct, "total": len(questions)},
            "questions": detail,
            "mastery_delta": [
                {"slug": v["slug"] or "(untagged)",
                 "p_before": round(sum(v["before"]) / len(v["before"]), 4) if v["before"] else None,
                 "p_after": round(sum(v["after"]) / len(v["after"]), 4) if v["after"] else None}
                for v in deltas.values()
            ],
            "next_up": weakest.slug if weakest else None,
        }
        row = self.quizzes.create_assessment(quiz.project_id, user_id, label, [quiz.id], report)
        try:
            self.profiles.observe_study_pattern(user_id, quizzes=1)
        except Exception:  # noqa: BLE001
            log.warning("Study-pattern quiz count failed for %s", user_id, exc_info=True)
        return row.id


def _public_question(record, slug: str) -> dict:
    """Client-safe question: keys stay server-side until grading."""
    return {
        "id": record.id,
        "topic_id": record.topic_id,
        "topic_slug": slug,
        "stem": record.stem,
        "qtype": record.qtype,
        "options": list(record.options or []) if record.qtype == "mcq" else [],
        "locator": citation_label(record.locator_kind, record.locator_value),
        "difficulty": record.difficulty,
    }


def _memory_dict(row, memory: ProjectMemoryRepo) -> dict:
    return {
        "topic_id": row.topic_id,
        "slug": row.slug,
        "name": row.name,
        "description": row.description or "",
        "p_mastery": round(float(row.p_mastery), 4),
        "attempts": row.attempts,
        "correct": row.correct,
        "coverage_status": row.coverage_status,
        "label": memory.mastery_label(float(row.p_mastery)),
        "last_practiced_at": row.last_practiced_at.isoformat() if row.last_practiced_at else None,
    }


def _shape_questions(raw: list, slugs: set[str], types: list[str]) -> list[dict]:
    """Keep only well-formed questions citing a listed topic slug."""
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        qtype = str(item.get("type", "mcq"))
        slug = str(item.get("topic_slug", ""))
        stem = str(item.get("stem", "")).strip()
        loc = item.get("locator") or {}
        if qtype not in types or slug not in slugs or not stem:
            continue
        if not isinstance(loc, dict) or not str(loc.get("value", "")).strip():
            continue
        q: dict = {
            "type": qtype, "topic_slug": slug, "stem": stem[:2000],
            "explanation": str(item.get("explanation", ""))[:2000],
            "difficulty": _clamp01(item.get("difficulty", 0.5)),
            "locator": {"kind": str(loc.get("kind", "page")).strip() or "page",
                        "value": str(loc.get("value", "")).strip()},
        }
        if qtype == "mcq":
            options = [str(o)[:500] for o in (item.get("options") or [])][:4]
            try:
                key = int(item.get("correct_index", -1))
            except (TypeError, ValueError):
                key = -1
            if len(options) != 4 or not 0 <= key < 4:
                continue
            q["options"] = options
            q["correct_index"] = key
        elif qtype == "short_answer":
            points = [str(p)[:300] for p in (item.get("key_points") or [])][:5]
            sample = str(item.get("sample_answer", "")).strip()
            if not points or not sample:
                continue
            q["key_points"] = points
            q["sample_answer"] = sample[:2000]
        elif qtype == "numerical":
            value = _parse_number(item.get("value"))
            if value is None:
                continue
            q["value"] = value
            q["tolerance"] = _positive_float(item.get("tolerance"), None)
            q["unit"] = str(item.get("unit", ""))[:20]
        out.append(q)
    return out


def _shuffled_options(options: list[str], key: int) -> tuple[list[str], int]:
    """Shuffle so the key is not always first; remap the answer index."""
    order = list(range(len(options)))
    random.shuffle(order)
    return [options[i] for i in order], order.index(key)


def _answer_key(q: dict, remapped_key: int) -> dict:
    if q["type"] == "mcq":
        return {"correct_index": remapped_key}
    if q["type"] == "short_answer":
        return {"key_points": q["key_points"], "sample_answer": q["sample_answer"]}
    if q["type"] == "numerical":
        return {"value": q["value"], "tolerance": q.get("tolerance"), "unit": q.get("unit", "")}
    return {}


def _expected_answer(question) -> object:
    key = question.answer_key or {}
    if question.qtype == "mcq":
        idx = key.get("correct_index")
        options = question.options or []
        return options[idx] if isinstance(idx, int) and 0 <= idx < len(options) else None
    if question.qtype == "short_answer":
        return key.get("sample_answer")
    if question.qtype == "numerical":
        unit = f" {key.get('unit')}" if key.get("unit") else ""
        return f"{key.get('value')}{unit}"
    return None


def _given_text(answer: int | float | str) -> str:
    return str(answer)[:2000]


def _parse_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            return float(text)
        except ValueError:
            pass
        if re.fullmatch(r"-?\d+\s*/\s*-?\d+", text):
            num, den = (float(p) for p in re.split(r"\s*/\s*", text))
            return num / den if den else None
    return None


def _positive_float(value: object, default: float | None) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _clamp01(value: object) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
