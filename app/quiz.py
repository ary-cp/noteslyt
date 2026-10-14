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

import json
import logging
import base64
from types import SimpleNamespace
import random
import re
from pydantic import BaseModel, ConfigDict

from .citations import citation_label
from .config import Settings
from .ingestion.topics import extract_topics
from .ingestion.units import SourceUnit
from .learner.bkt import BKTParams, calibrate
from .learner.sm2 import is_due
from .llm import Embedder, LLMClient
from .repo.memory import ProjectMemoryRepo
from .repo.profile import ProfileRepo
from .repo.quizzes import QuizRepo
from .repo.sources import SourceRepo
from .repo.topics import TopicRepo
from .vectorstore import VectorStore
from .storage import FileStorage, content_type_for

log = logging.getLogger(__name__)

QTYPES = ("mcq", "short_answer", "numerical")
DEFAULT_QTYPES = ("mcq", "short_answer", "numerical")

GENERATE_SYSTEM = """You write quiz questions grounded in study material. Output ONE JSON object:
{"questions": [{"type": "mcq|short_answer|numerical", "topic_slug": "...",
"stem": "...", "options": ["...","...","...","..."], "correct_index": 0,
"key_points": ["..."], "sample_answer": "...",
"value": 0.0, "tolerance": 0.0, "unit": "", "difficulty": 0.0-1.0,
"explanation": "...", "locator": {"kind": "...", "value": "...", "source_id": "..."}}]}
Rules: exactly the requested number of questions, round-robin over the requested types;
MCQ needs 4 options with exactly one correct; short_answer needs 2-4 key_points plus a
sample_answer; numerical needs value, tolerance and unit ("" if unitless). Every question
must be answerable from the excerpts and cite one locator from the list, using its exact
kind, value and source_id. Every stem must be self-contained: include all scenario facts,
numbers, definitions and example details needed to answer. Never ask "in the papaya example",
"as discussed above", or refer to an unseen diagram/table without reproducing the necessary
information in the stem. If that context is missing, ask a different concept question.
Test understanding and application, not recall of incidental names from an example.
MCQ options must be distinct, plausible and mutually exclusive; check the keyed answer by
solving the question before returning it. correct_index is a zero-based integer (0-3).
Use Markdown and LaTeX math delimited by $...$ or $$...$$ in stems, options and explanations.
Wrap complete quantities and units in math delimiters too; never leave raw m/s^2, x^2, subscripts or LaTeX commands in plain text.
Escape every LaTeX backslash correctly for JSON. Never wrap math in code fences.
Explanations teach: restate the rule, then the application. Rate each question difficulty 0.0 (simple recall) to 1.0 (multi-step application)."""

FLASHCARD_SYSTEM = """You create study flashcards from the supplied study material. Output ONE JSON object:
{"cards": [{"front": "a concise question or prompt", "back": "a concise accurate answer",
"topic_slug": "...", "locator": {"kind": "...", "value": "...", "source_id": "..."}}]}
Aim for the requested number of distinct useful cards; return fewer if the material is insufficient. Each card must teach one fact or concept
from the excerpts. Include the full scenario when referencing an example. Format equations with $...$ or $$...$$ LaTeX and JSON-escape backslashes. Use the exact topic slug and locator supplied. Do not create quiz questions,
multiple-choice options, answer indexes, explanations, or rubrics."""


class _FlashcardLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    value: str
    source_id: str


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

QUIZ_REPORT_SYSTEM = """You write a personalised quiz report as JSON. Given the score,
per-topic BKT mastery movement, the IRT ability movement, and the misconception
points, output {"summary": "...", "strengths": [...], "weaknesses": [...]}.
Summary is 3-4 sentences: overall confidence, which topics improved or slipped,
and the single most useful next step. Strengths/weaknesses are at most 4 short
phrases each, grounded in the evidence given."""


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
        self._ensure_image_topics(project_id, ready_ids)
        topics = (
            self.topics.list_for_sources(project_id, ready_ids)
            if source_ids is not None
            else self.topics.list_for_project(project_id)
        )
        if not topics:
            raise QuizError("No study topics could be identified in these sources. Use clearer images or add text-based study material.")
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
                linked_ids = [str(row["source_id"]) for row in self.topics.sources_for(topic.id) if str(row["source_id"]) in ready_ids]
                if not linked_ids:
                    raise QuizError(f"No selected ready source covers {topic.name}.")
                vec = self.embedder.embed([f"{topic.name}: {topic.description}"])[0]
                chunks = self.store.query(
                    vec, project_id=project_id, k=self.settings.top_k,
                    source_ids=linked_ids,
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
        try:
            vectors: list = self.embedder.embed(stems)
        except Exception as exc:  # noqa: BLE001 — novelty vectors are nice-to-have
            log.warning("Stem embedding skipped, storing questions without vectors: %s", exc)
            vectors = [None] * len(kept)
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
                stem_embedding=list(vec) if vec is not None else None,
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
        """Generate from source content, including original image pixels."""
        import uuid

        n_cards = max(1, min(n_cards, 20))
        ready_ids = self.sources.ready_ids(project_id)
        if source_ids is not None:
            ready_ids = [sid for sid in ready_ids if sid in set(source_ids)]
        if not ready_ids:
            raise QuizError("Select at least one ready source before generating flashcards.")
        contexts = {}
        if topic_id:
            topic = next((t for t in self.topics.list_for_sources(project_id, ready_ids) if t.id == topic_id), None)
            if topic is None:
                raise QuizError("This topic is not available in the selected sources.")
            chunks = self.store.get_by_topic(topic.id, project_id=project_id, limit=8, source_ids=ready_ids)
            if chunks:
                contexts[topic.slug] = (topic.name, chunks)
        else:
            # Source grouping supports cards even when topic extraction has
            # not run or could not identify concepts from an image.
            for row in self.sources.ready_chunks(project_id, ready_ids[:8]):
                sid = str(row["source_id"])
                if sid not in contexts:
                    source = self.sources.get(sid)
                    contexts[sid] = (source.filename, [])
                contexts[sid][1].append(SimpleNamespace(**{**row, "source_id": sid}))
        if not contexts:
            raise QuizError("No indexed content is available in the selected sources yet.")

        sections, images = [], []
        storage = FileStorage(self.settings.uploads_dir)
        seen_images = set()
        for slug, (name, chunks) in contexts.items():
            sections.append(f"TOPIC {slug} ({name})")
            for chunk in chunks:
                sections.append(f"SOURCE {chunk.source_id} LOCATOR {chunk.locator_kind}:{chunk.locator_value}\n{chunk.text[:1800]}")
                if chunk.locator_kind == "image" and chunk.source_id not in seen_images:
                    source = self.sources.get(chunk.source_id)
                    path = storage.open_for_read(project_id, source.id, source.filename) if source else None
                    if path is None:
                        raise QuizError("An original image is missing. Upload it again before generating flashcards.")
                    mime = content_type_for(source.filename).split(";")[0]
                    url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
                    images.extend([
                        {"type": "text", "text": f"Image for TOPIC {slug}, SOURCE {chunk.source_id}, LOCATOR {chunk.locator_kind}:{chunk.locator_value}"},
                        {"type": "image_url", "image_url": {"url": url}},
                    ])
                    seen_images.add(chunk.source_id)
        context = "\n\n".join(sections)
        cards, seen = [], set()
        last_error = None
        for attempt in range(3):
            missing = n_cards - len(cards)
            if not missing:
                break
            prompt = (f"Generate {missing} distinct flashcards using only the source content below. "
                      "Copy the exact topic slug and locator kind, value and source_id. "
                      "For images, read the attached image itself; metadata is not study content. "
                      "Return fewer cards if the source cannot support more useful facts.\n\n" + context)
            if cards:
                prompt += "\nDo not repeat these fronts:\n" + "\n".join(c["front"] for c in cards)
            user = [{"type": "text", "text": prompt}, *images] if images else prompt
            try:
                data = self.llm.complete_structured(
                    FLASHCARD_SYSTEM, user, response_model=_GeneratedFlashcardSet,
                    max_tokens=min(12000, max(3000, missing * 400)),
                )
            except (ValueError, TypeError) as exc:
                last_error = exc
                continue
            except Exception as exc:
                raise QuizError("The model could not generate flashcards. Check that your provider is available"
                                + (" and the selected chat model supports images." if images else ".")) from exc
            for item in data.cards:
                front, back = item.front.strip(), item.back.strip()
                key = " ".join(front.casefold().split())
                if not front or not back or key in seen or item.topic_slug not in contexts:
                    continue
                name, chunks = contexts[item.topic_slug]
                match = next((c for c in chunks if (c.source_id, c.locator_kind, str(c.locator_value)) ==
                              (item.locator.source_id, item.locator.kind, item.locator.value)), None)
                if match is None:
                    continue
                seen.add(key)
                cards.append({"id": str(uuid.uuid4()), "front": front[:1000], "back": back[:2000],
                              "topic": name, "locator_kind": match.locator_kind,
                              "locator_value": str(match.locator_value), "source_id": match.source_id})
                if len(cards) == n_cards:
                    break
        if not cards:
            raise QuizError("No valid flashcards were returned. Try fewer cards or clearer source material."
                            + (f" {last_error}" if last_error else ""))
        return {"flashcards_id": str(uuid.uuid4()), "cards": cards, "requested": n_cards}

    def saved_answers(self, user_id: str, quiz_id: str) -> dict:
        quiz = self.quizzes.get_quiz_owned(quiz_id, user_id)
        if quiz is None:
            raise QuizNotFoundError(quiz_id)
        questions = {q.id: q for q in self.quizzes.list_questions(quiz.id)}
        result = {}
        for answer in self.quizzes.answers_for_quiz(quiz.id, user_id):
            question = questions.get(answer.question_id)
            if question:
                topic = self._topic_memory(user_id, quiz.project_id, question.topic_id)
                result[question.id] = self._answer_payload(quiz, question, topic, answer.correct,
                    answer.given, answer.p_before, answer.p_after)
                result[question.id]["feedback"] = answer.feedback
        return result

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

        def replay(prior):
            topic = self._topic_memory(user_id, quiz.project_id, question.topic_id)
            payload = self._answer_payload(quiz, question, topic, prior.correct,
                                           prior.given, prior.p_before, prior.p_after)
            payload["feedback"] = prior.feedback or (("Correct. " if prior.correct else "Not quite. ") + question.explanation)
            return payload

        prior = self.quizzes.find_answer(quiz.id, question.id, user_id)
        if prior is not None:
            return replay(prior)
        # Provider calls stay outside the lock. Recheck after acquiring it:
        # another request may already have committed this answer.
        correct, expected, feedback, missed = self._grade(question, answer)
        with self.quizzes.learner_write(user_id, quiz.project_id):
            prior = self.quizzes.find_answer(quiz.id, question.id, user_id)
            if prior is not None:
                return replay(prior)
            mem_before = (self.memory.get(user_id, quiz.project_id, question.topic_id)
                          if question.topic_id else None)
            p_before = float(mem_before.p_mastery) if mem_before else self.bkt.p_init
            p_after = p_before
            if question.topic_id:
                guess, slip = calibrate(question.qtype, len(question.options or []),
                                      question.difficulty, self.bkt)
                updated = self.memory.apply_result(user_id, quiz.project_id, question.topic_id,
                                                correct, self.bkt, guess=guess, slip=slip)
                p_after = float(updated["p_mastery"])
            # IRT ability step (global, per graded answer) — alongside BKT.
            from .learner.irt import update_ability

            _prof = self.profiles.get(user_id)
            _ability_before = float(getattr(_prof, "irt_ability", 0.0) or 0.0) if _prof else 0.0
            try:
                _difficulty = float(getattr(question, "difficulty", 0.5) or 0.5)
            except (TypeError, ValueError):
                _difficulty = 0.5
            _ability_after = update_ability(_ability_before, correct, _difficulty)
            try:
                self.profiles.update_ability(user_id, _ability_after)
            except Exception:  # noqa: BLE001 — ability must never block grading
                log.warning("IRT ability store skipped for %s", user_id, exc_info=True)
            self.quizzes.record_answer(quiz.id, question.id, user_id, _given_text(answer), correct, p_before, p_after, feedback=feedback, missed=missed)
            topic = self._topic_memory(user_id, quiz.project_id, question.topic_id)
            payload = self._answer_payload(
                quiz, question, topic, correct, _given_text(answer), p_before, p_after,
                ability_before=_ability_before, ability_after=_ability_after,
            )
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
            "due_review": sum(1 for t in topics if t.get("due_at") and is_due(t["due_at"])),
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

    def _image_parts(self, project_id, chunks):
        parts, seen = [], set()
        storage = FileStorage(self.settings.uploads_dir)
        from .storage import read_image_chunk_bytes
        for chunk in chunks:
            if chunk.locator_kind != "image":
                continue
            key = f"{chunk.source_id}:{chunk.locator_value}"
            if key in seen:
                continue
            seen.add(key)
            source = self.sources.get(chunk.source_id)
            if source is None:
                continue
            found = read_image_chunk_bytes(
                storage, project_id, source, chunk.locator_kind, chunk.locator_value
            )
            if found is None:
                if getattr(source, "source_type", "") in ("pdf", "pptx"):
                    log.warning("Quiz image %s has no page render; skipped.", key)
                    continue
                raise QuizError("An original image is missing. Upload it again before generating a quiz.")
            data, mime = found
            url = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
            parts.extend([{"type": "text", "text": f"SOURCE {source.id} LOCATOR {chunk.locator_kind}:{chunk.locator_value}"},
                          {"type": "image_url", "image_url": {"url": url}}])
        return parts

    def _ensure_image_topics(self, project_id, ready_ids):
        """Backfill saved visual topics for images indexed before topic extraction existed."""
        existing = self.topics.list_for_project(project_id)
        for source_id in ready_ids:
            source = self.sources.get(source_id)
            if not source or source.source_type != "image" or self.topics.list_for_sources(project_id, [source_id]):
                continue
            chunks = self.sources.ready_chunks(project_id, [source_id], per_source=1)
            chunks = [SimpleNamespace(**{**row, "source_id": str(row["source_id"])}) for row in chunks]
            if not chunks:
                continue
            parts = self._image_parts(project_id, chunks)
            units = [SourceUnit(index=i, text=c.text, locator_kind=c.locator_kind, locator_value=c.locator_value) for i, c in enumerate(chunks)]
            proposed = extract_topics(self.llm, units, self.settings.max_topics,
                                      existing_slugs=[t.slug for t in existing], image_url=parts[-1]["image_url"]["url"])
            for topic in proposed:
                row = self.topics.upsert(project_id, topic.slug, topic.name, topic.description)
                self.topics.link_source(row.id, source_id, topic.locators)
                existing.append(row)

    # ------------------------------------------------------- generation
    def _generate_round(self, plan, contexts, types, n, avoid: list[str]) -> list[dict]:
        sections = []
        all_chunks = []
        for topic, _count in plan:
            _t, chunks = contexts[topic.slug]
            all_chunks.extend(chunks)
            sections.append(
                f"TOPIC {topic.slug} ({topic.name}):\n"
                + _subtopic_line(topic)
                + f"Locators: {', '.join(sorted({(c.source_id + ' / ' + c.locator_kind + ':' + c.locator_value) for c in chunks}))}\n"
                + "\n\n".join(f"SOURCE {c.source_id} LOCATOR {c.locator_kind}:{c.locator_value}\n{c.text[:3000]}" for c in chunks)
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
        images = self._image_parts(plan[0][0].project_id, all_chunks) if any(c.locator_kind == "image" for c in all_chunks) else []
        data = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                prompt = request_text + ("\nReturn valid JSON only, with all fields." if attempt else "")
                data = self.llm.complete_json(
                    GENERATE_SYSTEM,
                    [{"type": "text", "text": prompt}, *images] if images else prompt,
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
                   and (not locator.get("source_id") or c.source_id == locator["source_id"])
                   for c in chunks):
            return False
        matches = {c.source_id for c in chunks if c.locator_kind == locator.get("kind") and str(c.locator_value) == str(locator.get("value"))}
        if not locator.get("source_id") and len(matches) != 1:
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
        """Drop stems within cosine ≥ max of the learner's recent stems (F29).

        Degrades to unfiltered when the embedder is unreachable (e.g. the local
        server crashed mid-generation): a slightly repetitive quiz beats no quiz.
        """
        if not kept:
            return kept
        recent = self.quizzes.recent_stems(user_id, project_id)
        import numpy as np

        try:
            new_vecs = np.asarray(self.embedder.embed([q["stem"] for q in kept]), dtype=np.float32)
        except Exception as exc:  # noqa: BLE001
            log.warning("Novelty filter skipped (embedder unreachable): %s", exc)
            return kept
        new_vecs /= np.linalg.norm(new_vecs, axis=1, keepdims=True) + 1e-9
        sims = None
        if recent:
            old_vecs = np.asarray(recent, dtype=np.float32)
            old_vecs /= np.linalg.norm(old_vecs, axis=1, keepdims=True) + 1e-9
            sims = new_vecs @ old_vecs.T
        out, seen_idx, seen_stems = [], [], set()
        for i, q in enumerate(kept):
            stem_key = " ".join(q["stem"].split()).casefold()
            if stem_key in seen_stems:
                continue
            if sims is not None and sims[i].max() >= self.settings.quiz_similarity_max:
                log.info("Dropped near-duplicate stem %r", q["stem"][:80])
                continue
            if seen_idx and (new_vecs[i] @ new_vecs[seen_idx].T).max() >= self.settings.quiz_similarity_max:
                log.info("Dropped within-quiz duplicate stem %r", q["stem"][:80])
                continue
            seen_idx.append(i)
            seen_stems.add(stem_key)
            out.append(q)
        return out

    # ---------------------------------------------------------- grading
    def _grade(self, question, answer: int | float | str) -> tuple[bool, object, str, list[str]]:
        """Grade an answer. Returns (correct, expected, feedback, missed_points).

        `missed_points` feeds the post-assessment misconception report: rubric
        misses for short answers, a chose-vs-correct note for MCQ/numerical.
        """
        if question.qtype == "mcq":
            if isinstance(answer, bool) or not isinstance(answer, int):
                raise QuizError("MCQ answers must be an option index (integer).")
            key = _coerce_key(question.answer_key).get("correct_index")
            options = _coerce_options(question.options)
            if not 0 <= answer < len(options):
                raise QuizError(f"Option index out of range (0–{len(options) - 1}).")
            correct = answer == key
            expected = options[key] if key is not None and key < len(options) else None
            missed = [] if correct else [f"Chose '{options[answer]}' — correct answer: '{expected}'"]
            return correct, expected, (
                f"Correct. {question.explanation}" if correct
                else f"Not quite — the answer is {options[key] if key is not None else '?'}. {question.explanation}"
            ).strip(), missed
        if question.qtype == "short_answer":
            if not isinstance(answer, str) or not answer.strip():
                raise QuizError("Short answers must be non-empty text.")
            return self._grade_short(question, answer.strip())
        if question.qtype == "numerical":
            value = _parse_number(answer)
            if value is None:
                raise QuizError("Numerical answers must be a number.")
            key = _coerce_key(question.answer_key)
            target = float(key.get("value", 0.0))
            tol = float(key.get("tolerance", self.settings.quiz_default_tolerance))
            unit = str(key.get("unit", ""))
            correct = abs(value - target) <= tol
            ukey = f" {unit}" if unit else ""
            missed = [] if correct else [f"Answered {value}{ukey} — correct value: {target}{ukey} (±{tol})"]
            return correct, f"{target}{ukey}", (
                f"Correct: {value}{ukey} is within ±{tol} of {target}{ukey}. {question.explanation}"
                if correct else
                f"You answered {value}{ukey}; the value is {target}{ukey} (±{tol}). {question.explanation}"
            ).strip(), missed
        raise QuizError(f"Unknown question type '{question.qtype}'.")

    def _grade_short(self, question, answer: str) -> tuple[bool, object, str, list[str]]:
        key = _coerce_key(question.answer_key)
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
        if not isinstance(data, dict) or type(data.get("correct")) is not bool:
            raise QuizError("The grader returned an invalid result. Your answer was not recorded; please try again.")
        correct = data["correct"]
        sample = str(key.get("sample_answer", ""))
        feedback = str(data.get("feedback", "")).strip()
        missed = [str(m).strip() for m in (data.get("missed") or []) if str(m).strip()][:8]
        return correct, sample, (
            f"{feedback} {question.explanation}".strip() if feedback else question.explanation
        ), missed

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
            if (c.locator_kind, c.locator_value) == (q["locator"]["kind"], q["locator"]["value"]) and (not q["locator"].get("source_id") or c.source_id == q["locator"]["source_id"]):
                return c.source_id
        return chunks[0].source_id if chunks else None

    def _answer_payload(self, quiz, question, topic, correct, given, p_before, p_after,
                          ability_before: float | None = None,
                          ability_after: float | None = None) -> dict:
        source_name = None
        if question.source_id:
            row = self.sources.get(question.source_id)
            source_name = row.filename if row else None
        assessment = self.quizzes.find_assessment_for_quiz(quiz.id, quiz.user_id)
        out = {
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
        if ability_before is not None or ability_after is not None:
            out["irt"] = {"ability_before": ability_before, "ability_after": ability_after}
        return out

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
        points: dict[str, list[str]] = {}
        for q in questions:
            a = answers[q.id]
            tm = self._topic_memory(user_id, quiz.project_id, q.topic_id)
            tid = q.topic_id or ""
            d = deltas.setdefault(tid, {"slug": "", "name": "", "before": [], "after": []})
            if tm:
                d["slug"] = tm["slug"]
                d["name"] = tm["name"]
            d["before"].append(a.p_before)
            d["after"].append(a.p_after)
            detail.append({
                "question_id": q.id, "stem": q.stem, "qtype": q.qtype,
                "given": a.given, "correct": a.correct,
                "citation": citation_label(q.locator_kind, q.locator_value),
            })
            bucket = points.setdefault(tid, [])
            for point in list(getattr(a, "missed", None) or [])[:4]:
                text = str(point).strip()[:280]
                if text and text not in bucket and len(bucket) < 3:
                    bucket.append(text)
        correct = sum(1 for q in questions if answers[q.id].correct)
        mem = self.memory.list_for_project(user_id, quiz.project_id)
        weakest = min(mem, key=lambda r: (float(r.p_mastery), r.name)) if mem else None
        label = f"Quiz — {correct}/{len(questions)} correct"

        def _avg(vals: list[float]) -> float | None:
            return round(sum(vals) / len(vals), 4) if vals else None

        mastery_delta = [
            {"slug": v["slug"] or "(untagged)",
             "name": v["name"] or v["slug"] or "(untagged)",
             "p_before": _avg(v["before"]), "p_after": _avg(v["after"])}
            for v in deltas.values()
        ]
        after_of = {d["slug"]: (d["p_after"] if d["p_after"] is not None else 1.0) for d in mastery_delta}
        misconceptions = [
            {"slug": deltas[tid]["slug"] or "(untagged)",
             "name": deltas[tid]["name"] or deltas[tid]["slug"] or "(untagged)",
             "points": pts}
            for tid, pts in points.items() if pts
        ]
        misconceptions.sort(key=lambda m: after_of.get(m["slug"], 1.0))
        # IRT snapshot: ability now vs an approximate pre-quiz level.
        from .learner.irt import ability_label, prob_correct

        _prof_now = self.profiles.get(user_id)
        ability_after = float(getattr(_prof_now, "irt_ability", 0.0) or 0.0) if _prof_now else 0.0
        avg_difficulty = (
            round(sum(float(getattr(q, "difficulty", 0.5) or 0.5) for q in questions) / len(questions), 3)
            if questions else 0.5
        )
        irt_block = {
            "method": "rasch-1pl",
            "ability_before": None,
            "ability_after": round(ability_after, 4),
            "ability_label": ability_label(ability_after),
            "avg_difficulty": avg_difficulty,
            "p_expected": round(prob_correct(ability_after, avg_difficulty), 4),
        }
        # LLM narrative: personalised report grounded in BKT+IRT evidence.
        narrative: dict = {}
        try:
            delta_txt = "; ".join(
                f"{d['name']}: {d['p_before']}→{d['p_after']}" for d in mastery_delta[:6]
            ) or "no topic deltas"
            misc_txt = "; ".join(
                f"{m['name']}: {', '.join(m['points'][:2])}" for m in misconceptions[:4]
            ) or "no misconceptions recorded"
            narrative = self.llm.complete_json(
                QUIZ_REPORT_SYSTEM,
                f"Score: {correct}/{len(questions)}. BKT mastery: {delta_txt}. "
                f"IRT ability now {ability_after:.2f} ({ability_label(ability_after)}), "
                f"avg difficulty {avg_difficulty}. Misconceptions: {misc_txt}.",
                max_tokens=700,
            )
        except Exception as exc:  # noqa: BLE001 — narrative is nice-to-have
            log.warning("Quiz narrative report skipped: %s", exc)
            narrative = {}
        summary = str(narrative.get("summary") or "").strip()[:1500]
        if not summary:
            summary = (
                f"Scored {correct}/{len(questions)}. "
                + (f"Weakest now: {weakest.name}. " if weakest else "")
                + f"Ability {ability_label(ability_after)} ({ability_after:+.2f})."
            )
        strengths = [str(s)[:120] for s in (narrative.get("strengths") or [])][:4]
        weaknesses = [str(s)[:120] for s in (narrative.get("weaknesses") or [])][:4]
        if not weaknesses:
            weaknesses = [str(m.get("name", "")) for m in misconceptions[:3] if m.get("name")]
        report = {
            "score": {"correct": correct, "total": len(questions)},
            "questions": detail,
            "mastery_delta": mastery_delta,
            "misconceptions": misconceptions,
            "next_up": weakest.slug if weakest else None,
            "next_up_name": weakest.name if weakest else None,
            "irt": irt_block,
            "summary": summary,
            "strengths": strengths,
            "weaknesses": weaknesses,
        }
        row = self.quizzes.create_assessment(quiz.project_id, user_id, label, [quiz.id], report)
        # Personalised report row for the Learning Profile page.
        try:
            from .repo.learner_reports import LearnerReportRepo

            kind = "exam" if len(questions) > 10 else "quiz"
            LearnerReportRepo(self.profiles.db).create(
                user_id, quiz.project_id, kind, ref_id=row.id,
                summary=summary,
                bkt={"mastery_delta": mastery_delta, "method": "bkt-calibrated"},
                irt=irt_block,
                strengths=strengths
                or [str(m.get("name", "")) for m in misconceptions[:0]],
                weaknesses=weaknesses,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Learner report store skipped for quiz %s: %s", quiz.id, exc)
        try:
            self.profiles.observe_study_pattern(user_id, quizzes=1)
        except Exception:  # noqa: BLE001
            log.warning("Study-pattern quiz count failed for %s", user_id, exc_info=True)
        return row.id


def _coerce_options(value: object) -> list:
    """Options as a list, tolerating a JSON-text snapshot (SQLite legacy rows)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return list(value) if isinstance(value, list) else []


def _coerce_key(value: object) -> dict:
    """Answer key as a dict, tolerating a JSON-text snapshot."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _public_question(record, slug: str) -> dict:
    """Client-safe question: keys stay server-side until grading."""
    return {
        "id": record.id,
        "topic_id": record.topic_id,
        "topic_slug": slug,
        "stem": record.stem,
        "qtype": record.qtype,
        "options": _coerce_options(record.options) if record.qtype == "mcq" else [],
        "locator": citation_label(record.locator_kind, record.locator_value),
        "difficulty": record.difficulty,
    }


def _subtopic_line(topic) -> str:
    """Subtopic/concept outline appended to quiz grounding per topic."""
    subs = [s for s in (getattr(topic, "subtopics", None) or []) if isinstance(s, dict)]
    if not subs:
        return ""
    parts = []
    for s in subs[:8]:
        concepts = ", ".join(s.get("concepts", []) or [])
        parts.append(f"- {s.get('name', s.get('slug', ''))}" + (f": {concepts}" if concepts else ""))
    return "Subtopics to cover in questions:\n" + "\n".join(parts) + "\n"


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
        "easiness": float(getattr(row, "easiness", 2.5) or 2.5),
        "repetitions": int(getattr(row, "repetitions", 0) or 0),
        "interval_days": float(getattr(row, "interval_days", 0.0) or 0.0),
        "due_at": row.due_at.isoformat() if getattr(row, "due_at", None) else None,
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
                        "value": str(loc.get("value", "")).strip(),
                        "source_id": str(loc.get("source_id", "")).strip()},
        }
        if qtype == "mcq":
            raw_options = item.get("options")
            key = item.get("correct_index")
            if (not isinstance(raw_options, list) or len(raw_options) != 4
                    or not all(isinstance(o, str) and o.strip() for o in raw_options)
                    or type(key) is not int or not 0 <= key < 4):
                continue
            options = [o.strip()[:500] for o in raw_options]
            if len({o.casefold() for o in options}) != 4:
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
    key = _coerce_key(question.answer_key)
    if question.qtype == "mcq":
        idx = key.get("correct_index")
        options = _coerce_options(question.options)
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
