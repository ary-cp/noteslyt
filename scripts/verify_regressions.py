"""Focused regression checks, with isolated disposable rows in local Postgres.

Run with uv run python scripts/verify_regressions.py. No provider calls.
Only the randomized user created by this run is deleted, including on failure.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.citations import audit_citations, format_context
from app.config import get_settings
from app.db import Database
from app.llm import LLMClient
from app.quiz import QuizError, QuizService, _GeneratedFlashcardSet, _shape_questions, _shuffled_options
from app.repo.chats import ChatRepo
from app.repo.memory import ProjectMemoryRepo
from app.repo.profile import ProfileRepo
from app.repo.projects import ProjectRepo
from app.repo.quizzes import QuizRepo
from app.repo.sources import SourceRepo
from app.repo.topics import TopicRepo
from app.repo.users import UserRepo
from app.storage import FileStorage
from app.vectorstore import VectorStore


def check(label, condition):
    assert condition, label
    print("PASS:", label)


def main():
    chunks = [NS(source_id=s, locator_kind="page", locator_value="1", heading="",
                 text=t, start_ts=None) for s, t in [("a", "Apples"), ("b", "Bananas")]]
    check("same page in different files gets separate references", "[2] [Page 1]" in format_context(chunks, numbered=True))
    result = audit_citations("Bananas [2]. Apples [1].", chunks)
    check("citation preserves the correct source", [c.source_id for c in result.citations] == ["b", "a"])
    check("ambiguous legacy labels cannot pick the wrong file", not audit_citations("Claim [Page 1].", chunks).citations)
    check("unknown reference is removed", not audit_citations("Claim [99].", chunks).citations)

    candidate = {"type": "mcq", "topic_slug": "forces", "stem": "A 2 kg mass accelerates at 3 m/s². Find its force.",
                 "options": ["3 N", "6 N", "9 N", "12 N"], "correct_index": 1,
                 "locator": {"kind": "page", "value": "1", "source_id": "b"}}
    for bad_key in [True, 1.5, "1", -1, 4]:
        check(f"reject malformed MCQ answer key {bad_key!r}", not _shape_questions([{**candidate, "correct_index": bad_key}], {"forces"}, ["mcq"]))
    check("reject surplus MCQ options", not _shape_questions([{**candidate, "options": [*candidate["options"], "15 N"]}], {"forces"}, ["mcq"]))
    check("reject duplicate MCQ options", not _shape_questions([{**candidate, "options": ["A", "a", "B", "C"]}], {"forces"}, ["mcq"]))
    shaped = _shape_questions([candidate], {"forces"}, ["mcq"])[0]
    check("preserve source-specific quiz citation", shaped["locator"]["source_id"] == "b")
    for _ in range(20):
        opts, key = _shuffled_options(candidate["options"], 1)
        assert opts[key] == "6 N"
    check("MCQ shuffle preserves correct answer", True)

    # Cold-start quizzes still need within-batch exact and semantic deduplication.
    novelty = object.__new__(QuizService)
    novelty.settings = NS(quiz_similarity_max=0.95)
    novelty.quizzes = NS(recent_stems=lambda *args: [])
    vectors = {"force": [1.0, 0.0], "paraphrase": [0.999, 0.001], "energy": [0.0, 1.0]}
    novelty.embedder = NS(embed=lambda stems: [vectors.get(stem.strip().lower(), [0.0, 0.0]) for stem in stems])
    batch = [{"stem": s} for s in ["force", "force", "paraphrase", "energy", "zero", " ZERO "]]
    check("first quiz removes exact and semantic duplicates", [q["stem"] for q in novelty._novelty_filter("u", "p", batch)] == ["force", "energy", "zero"])
    novelty.quizzes = NS(recent_stems=lambda *args: [[1.0, 0.0]])
    check("existing history still filters repeated questions", [q["stem"] for q in novelty._novelty_filter("u", "p", batch)] == ["energy", "zero"])
    check("empty quiz needs no embeddings", novelty._novelty_filter("u", "p", []) == [])

    grader = object.__new__(QuizService)
    short_q = NS(stem="What is 2+2?", answer_key={"key_points": ["4"], "sample_answer": "4"}, explanation="Add two and two.")
    for invalid in ["false", "true", 0, 1, None, [], {}]:
        grader.llm = NS(complete_json=lambda *args, **kwargs: {"correct": invalid})
        try:
            grader._grade_short(short_q, "5")
            raise AssertionError(f"Invalid grading result accepted: {invalid!r}")
        except QuizError:
            pass
    check("grader rejects non-boolean verdicts", True)
    for verdict in [False, True]:
        grader.llm = NS(complete_json=lambda *args, **kwargs: {"correct": verdict, "feedback": "Specific feedback"})
        check(f"grader preserves boolean {verdict}", grader._grade_short(short_q, "5")[0] is verdict)

    with tempfile.TemporaryDirectory() as tmp:
        storage = FileStorage(Path(tmp))
        class Bounded(io.BytesIO):
            def read(self, size=-1):
                assert 0 < size <= 1024 * 1024, "unbounded upload read"
                return super().read(size)
        try:
            storage.save_stream("p", "s", "x.txt", Bounded(b"too large"), 3)
            raise AssertionError("oversize upload accepted")
        except ValueError:
            pass
        check("oversize upload leaves no file or partial", not list(Path(tmp).rglob("*.*")))
        saved = storage.save_stream("p", "s", "x.txt", Bounded(b"abc"), 3)
        check("exact upload limit accepted", saved.path.read_bytes() == b"abc")

    # A malformed schema response falls back to validated JSON.
    outputs = iter(["{invalid", '{"cards":[]}'])
    llm = object.__new__(LLMClient)
    llm.model = "mock"
    llm._client = NS(chat=NS(completions=NS(create=lambda **kw: NS(
        choices=[NS(message=NS(content=next(outputs), refusal=None))]))))
    check("malformed structured output retries", llm.complete_structured(
        "Make cards", "Context", response_model=_GeneratedFlashcardSet).cards == [])

    settings = get_settings()
    db = Database(settings)
    uid = None
    try:
        user = UserRepo(db).create(f"regression-{uuid.uuid4().hex}@test.co", "test-regression-password", "Regression")
        uid = user.id
        project = ProjectRepo(db).create(uid, "Regression checks")
        chats = ChatRepo(db)
        chat = chats.create(project.id, uid)
        messages = [chats.add_message(chat.id, "user", f"turn {i}") for i in range(15)]
        check("memory uses latest ten messages in chronological order",
              [m.content for m in chats.recent_messages(chat.id)] == [f"turn {i}" for i in range(5, 15)])
        older = chats.unsummarized_messages(chat.id, None, 10)
        check("summary excludes recent window", [m.id for m in older] == [m.id for m in messages[:5]])
        check("summary advances past checkpoint", [m.id for m in chats.unsummarized_messages(chat.id, messages[2].id, 10)] == [m.id for m in messages[3:5]])

        sources, topics = SourceRepo(db), TopicRepo(db)
        memory, quizzes = ProjectMemoryRepo(db), QuizRepo(db)
        service = QuizService(settings, None, None, VectorStore(db), quizzes, memory, topics, sources, ProfileRepo(db))
        topic = topics.upsert(project.id, "atomic", "Atomic grading")
        def question():
            quiz = quizzes.create_quiz(project.id, uid, topic.id)
            q = quizzes.add_question(quiz.id, 0, topic_id=topic.id, source_id=None,
                qtype="mcq", stem="Choose A", options=["A", "B", "C", "D"],
                answer_key={"correct_index": 0}, locator_value="1")
            return quiz, q
        quiz, q = question()
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda _: service.answer(uid, quiz.id, q.id, 0), range(4)))
        check("concurrent retries record exactly one answer", len(quizzes.answers_for_quiz(quiz.id, uid)) == 1)
        check("concurrent retries update mastery once", memory.get(uid, project.id, topic.id).attempts == 1)
        check("retries return the original result", all(a["p_after"] == answers[0]["p_after"] for a in answers))
        replay = service.answer(uid, quiz.id, q.id, 1)
        check("changed replay cannot contradict original grading", replay["correct"] and replay["feedback"].startswith("Correct"))

        failed_quiz, failed_q = question()
        with patch.object(quizzes, "record_answer", side_effect=RuntimeError("injected persistence failure")):
            try:
                service.answer(uid, failed_quiz.id, failed_q.id, 0)
                raise AssertionError("injected failure did not propagate")
            except RuntimeError:
                pass
        check("failed answer rolls back mastery", memory.get(uid, project.id, topic.id).attempts == 1)
        check("failed answer leaves no answer row", not quizzes.answers_for_quiz(failed_quiz.id, uid))
        check("resume restores saved answers without grading", service.saved_answers(uid, quiz.id)[q.id]["given"] == "0")
        check("resume never reveals unanswered question keys", service.saved_answers(uid, failed_quiz.id) == {})

        other_questions = [question(), question()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda pair: service.answer(uid, pair[0].id, pair[1].id, 0), other_questions))
        check("different concurrent quizzes do not lose mastery updates", memory.get(uid, project.id, topic.id).attempts == 3)

        progress = ProfileRepo(db).quiz_progress(uid)
        check("profile reflects historical first answers exactly once", progress == {"answered": 3, "correct": 3, "topics_practiced": 1, "quizzes_practiced": 3})
        check("quiz citation rejects ambiguous page labels", not service._verify_question({**shaped, "locator": {"kind": "page", "value": "1"}}, chunks))
        check("quiz citation accepts exact source", service._verify_question(shaped, chunks))

        feedback_topic = topics.upsert(project.id, "feedback", "Feedback persistence")
        short_quiz = quizzes.create_quiz(project.id, uid, feedback_topic.id)
        short_record = quizzes.add_question(short_quiz.id, 0, topic_id=feedback_topic.id, source_id=None,
            qtype="short_answer", stem="What is 2+2?", answer_key={"key_points": ["4"], "sample_answer": "4"},
            explanation="Add two and two.", locator_value="1")
        service.llm = NS(complete_json=lambda *args, **kwargs: {"correct": "false"})
        try:
            service.answer(uid, short_quiz.id, short_record.id, "5")
            raise AssertionError("Malformed grade was saved")
        except QuizError:
            pass
        check("malformed grade never records an answer", not quizzes.answers_for_quiz(short_quiz.id, uid))
        before = memory.get(uid, project.id, feedback_topic.id)
        check("malformed grade never updates mastery", before is None or before.attempts == 0)
        service.llm = NS(complete_json=lambda *args, **kwargs: {"correct": False, "feedback": "You added one extra; count four objects."})
        first = service.answer(uid, short_quiz.id, short_record.id, "5")
        service.llm = NS(complete_json=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Replay must not regrade")))
        replay = service.answer(uid, short_quiz.id, short_record.id, "4")
        resumed = service.saved_answers(uid, short_quiz.id)[short_record.id]
        check("personalized feedback survives repository round trip", quizzes.find_answer(short_quiz.id, short_record.id, uid).feedback == first["feedback"])
        check("retry and resume preserve original feedback", first["feedback"] == replay["feedback"] == resumed["feedback"] and "one extra" in resumed["feedback"])
        check("feedback replay leaves score and mastery unchanged", not resumed["correct"] and memory.get(uid, project.id, feedback_topic.id).attempts == 1)
        legacy_quiz, legacy_question = question()
        quizzes.record_answer(legacy_quiz.id, legacy_question.id, uid, "0", True, 0.3, 0.5)
        check("legacy answers without feedback can still resume", service.saved_answers(uid, legacy_quiz.id)[legacy_question.id]["feedback"] == "")

        # An image with no linked topics is a valid flashcard source. The fake
        # provider verifies original image bytes arrive, then omits one card.
        source = sources.create(project.id, "diagram.png", "image")
        sources.set_status(source.id, "ready", "", units=1, chunk_count=1)
        vector = [1.0] + [0.0] * (settings.embedding_dim - 1)
        service.store.upsert(project_id=project.id, source_id=source.id,
            ids=[str(uuid.uuid4())], embeddings=[vector], texts=["Image embedded directly"],
            metadatas=[{"locator_kind": "image", "locator_value": source.id}])
        foreign = ProjectRepo(db).create(uid, "Other project")
        check("source context cannot cross projects", sources.ready_chunks(foreign.id, [source.id]) == [])
        with tempfile.TemporaryDirectory() as tmp:
            service.settings = settings.model_copy(update={"data_dir": Path(tmp)})
            FileStorage(service.settings.uploads_dir).save(project.id, source.id, source.filename, b"test-image-bytes")
            calls = []
            def generate(system, user, **kwargs):
                calls.append(user)
                assert any(part.get("type") == "image_url" for part in user)
                # Repeating a card must not produce duplicated study material.
                return _GeneratedFlashcardSet.model_validate({"cards": [
                    {"front": "What is shown?", "back": "A diagram.", "topic_slug": source.id,
                     "locator": {"kind": "image", "value": source.id, "source_id": source.id}},
                    {"front": "Wrong source", "back": "Invalid", "topic_slug": source.id,
                     "locator": {"kind": "image", "value": source.id, "source_id": str(uuid.uuid4())}},
                ]})
            service.llm = NS(complete_structured=generate)
            result = service.create_flashcards(uid, project.id, None, 2, [source.id])
            check("image-only flashcards work without topic extraction", result["cards"][0]["source_id"] == source.id)
            check("partial verified cards survive bounded retries", len(result["cards"]) == 1 and result["requested"] == 2 and len(calls) == 3)
            def topics_from_image(system, user, **kwargs):
                assert isinstance(user, list) and any(part.get("type") == "image_url" for part in user)
                return {"topics": [{"id": "newton-laws", "name": "Newton laws of motion", "description": "Apply force and acceleration.", "locators": [{"kind": "image", "value": source.id}]}]}
            service.llm = NS(complete_json=topics_from_image)
            service._ensure_image_topics(project.id, [source.id])
            saved = topics.list_for_sources(project.id, [source.id])
            check("image topics are saved and linked to the original source", len(saved) == 1 and saved[0].slug == "newton-laws")
            service._ensure_image_topics(project.id, [source.id])
            check("image topics are reused without duplication", len(topics.list_for_sources(project.id, [source.id])) == 1)
            image_chunks = [NS(**{**row, "source_id": str(row["source_id"])}) for row in sources.ready_chunks(project.id, [source.id])]
            def quiz_from_image(system, user, **kwargs):
                assert isinstance(user, list) and any(part.get("type") == "image_url" for part in user)
                return {"questions": [{**candidate, "topic_slug": saved[0].slug,
                        "locator": {"kind": "image", "value": source.id, "source_id": source.id}}]}
            service.llm = NS(complete_json=quiz_from_image)
            generated = service._generate_round([(saved[0], 1)], {saved[0].slug: (saved[0], image_chunks)}, ["mcq"], 1, [])
            check("image quiz reads original pixels and preserves citation", len(generated) == 1 and service._verify_question(generated[0], image_chunks))

    finally:
        if uid:
            with db.transaction() as conn:
                conn.execute("delete from users where id=%s", (uid,))
        db.close()
    print("REGRESSIONS VERIFIED")


if __name__ == "__main__":
    main()
