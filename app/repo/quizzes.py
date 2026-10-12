"""Assessment repository: quizzes, questions, answers, assessments — Phase F.

`quizzes` carries `project_id` + `user_id`, so the answer endpoint (which takes
no scope parameter) inherits its security boundary entirely from this row.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pgvector import Vector
from psycopg.types.json import Json

from .base import Repo, parse_times, to_record, to_records
from .models import AnswerRecord, AssessmentRecord, QuestionRecord, QuizRecord

_QUIZ_FIELDS = ("id", "project_id", "user_id", "topic_id", "created_at")

_QUESTION_FIELDS = (
    "id", "quiz_id", "topic_id", "source_id", "seq", "qtype", "stem",
    "options", "answer_key", "explanation", "locator_kind", "locator_value",
    "difficulty",
)


class QuizRepo(Repo):
    # ------------------------------------------------------------ quizzes
    def create_quiz(self, project_id: str, user_id: str, topic_id: str | None = None) -> QuizRecord:
        row = self._fetch_one(
            f"insert into quizzes (id, project_id, user_id, topic_id) values (%s, %s, %s, %s) "
            f"returning {', '.join(_QUIZ_FIELDS)}",
            (str(uuid.uuid4()), project_id, user_id, topic_id),
        )
        return to_record(QuizRecord, row)  # type: ignore[return-value]

    def get_quiz_owned(self, quiz_id: str, user_id: str) -> QuizRecord | None:
        """404 unless the quiz is the caller's (project join keeps it scoped)."""
        row = self._fetch_one(
            f"select {', '.join('q.' + f for f in _QUIZ_FIELDS)} from quizzes q "
            f"join projects p on p.id = q.project_id "
            f"where q.id = %s and q.user_id = %s and p.user_id = %s",
            (quiz_id, user_id, user_id),
        )
        return to_record(QuizRecord, row)

    # ---------------------------------------------------------- questions
    def add_question(
        self,
        quiz_id: str,
        seq: int,
        *,
        topic_id: str | None,
        source_id: str | None,
        qtype: str,
        stem: str,
        options: list | None = None,
        answer_key: dict | None = None,
        explanation: str = "",
        locator_kind: str = "page",
        locator_value: str = "",
        difficulty: float = 0.5,
        stem_embedding: list[float] | None = None,
    ) -> QuestionRecord:
        row = self._fetch_one(
            f"insert into questions (id, quiz_id, topic_id, source_id, seq, qtype, stem, "
            f"options, answer_key, explanation, locator_kind, locator_value, difficulty, stem_embedding) "
            f"values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"returning {', '.join(_QUESTION_FIELDS)}",
            (
                str(uuid.uuid4()), quiz_id, topic_id, source_id, seq, qtype, stem,
                Json(options or []), Json(answer_key or {}), explanation,
                locator_kind, locator_value, difficulty,
                Vector(stem_embedding) if stem_embedding is not None else None,
            ),
        )
        return to_record(QuestionRecord, row)  # type: ignore[return-value]

    def list_questions(self, quiz_id: str) -> list[QuestionRecord]:
        rows = self._fetch_all(
            f"select {', '.join(_QUESTION_FIELDS)} from questions "
            f"where quiz_id = %s order by seq",
            (quiz_id,),
        )
        return to_records(QuestionRecord, rows)  # type: ignore[return-value]

    def get_question(self, question_id: str) -> QuestionRecord | None:
        row = self._fetch_one(
            f"select {', '.join(_QUESTION_FIELDS)} from questions where id = %s",
            (question_id,),
        )
        return to_record(QuestionRecord, row)

    def recent_stems(self, user_id: str, project_id: str, limit: int = 50) -> list[list[float]]:
        """Newest-first stem vectors for novelty detection (F29)."""
        rows = self._fetch_all(
            "select q.stem_embedding from questions q join quizzes z on z.id = q.quiz_id "
            "where z.user_id = %s and z.project_id = %s and q.stem_embedding is not null "
            "order by z.created_at desc limit %s",
            (user_id, project_id, limit),
        )
        out: list[list[float]] = []
        for r in rows:
            vec = r["stem_embedding"]
            if isinstance(vec, str):
                # SQLite backend: JSON text.
                out.append([float(x) for x in json.loads(vec)])
                continue
            # pgvector returns its own Vector wrapper — normalize to floats.
            to_list = getattr(vec, "to_list", None)
            vals = to_list() if callable(to_list) else list(vec)
            out.append([float(x) for x in vals])
        return out

    # ------------------------------------------------------------ answers
    def find_answer(self, quiz_id: str, question_id: str, user_id: str) -> AnswerRecord | None:
        row = self._fetch_one(
            "select id, quiz_id, question_id, user_id, given, correct, p_before, p_after, feedback, missed, matched, created_at "
            "from answers where quiz_id = %s and question_id = %s and user_id = %s",
            (quiz_id, question_id, user_id),
        )
        return to_record(AnswerRecord, row)

    def record_answer(
        self, quiz_id: str, question_id: str, user_id: str,
        given: str, correct: bool, p_before: float, p_after: float, feedback: str = "",
        missed: list | None = None, matched: list | None = None,
    ) -> AnswerRecord:
        row = self._fetch_one(
            "insert into answers (id, quiz_id, question_id, user_id, given, correct, p_before, p_after, feedback, missed, matched) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "returning id, quiz_id, question_id, user_id, given, correct, p_before, p_after, feedback, missed, matched, created_at",
            (str(uuid.uuid4()), quiz_id, question_id, user_id, given, correct, p_before, p_after, feedback,
             Json(missed or []), Json(matched or [])),
        )
        return to_record(AnswerRecord, row)  # type: ignore[return-value]

    def answer_tally(self, quiz_id: str, user_id: str) -> dict[str, int]:
        with self.db.connection() as conn:
            row = conn.execute(
                "select count(*) as n, coalesce(sum(case when correct then 1 else 0 end), 0) as c "
                "from answers where quiz_id = %s and user_id = %s",
                (quiz_id, user_id),
            ).fetchone()
        return {"answered": int(row["n"]), "correct": int(row["c"])}

    def answers_for_quiz(self, quiz_id: str, user_id: str) -> list[AnswerRecord]:
        rows = self._fetch_all(
            "select id, quiz_id, question_id, user_id, given, correct, p_before, p_after, feedback, missed, matched, created_at "
            "from answers where quiz_id = %s and user_id = %s order by created_at",
            (quiz_id, user_id),
        )
        return to_records(AnswerRecord, rows)  # type: ignore[return-value]

    # ------------------------------------------------------- assessments
    def find_assessment_for_quiz(self, quiz_id: str, user_id: str) -> AssessmentRecord | None:
        # Containment is evaluated in Python (portable): assessment sets per
        # user are tiny, and neither backend needs a JSON operator.
        rows = self._fetch_all(
            "select id, project_id, user_id, label, quiz_ids, report, created_at from assessments "
            "where user_id = %s",
            (user_id,),
        )
        for r in rows:
            ids = r["quiz_ids"]
            if isinstance(ids, str):
                try:
                    ids = json.loads(ids)
                except ValueError:
                    continue
            if quiz_id in (ids or []):
                return to_record(AssessmentRecord, r)
        return None

    def create_assessment(
        self, project_id: str, user_id: str, label: str,
        quiz_ids: list[str], report: dict[str, Any],
    ) -> AssessmentRecord:
        row = self._fetch_one(
            "insert into assessments (id, project_id, user_id, label, quiz_ids, report) "
            "values (%s, %s, %s, %s, %s, %s) "
            "returning id, project_id, user_id, label, quiz_ids, report, created_at",
            (str(uuid.uuid4()), project_id, user_id, label, Json(quiz_ids), Json(report)),
        )
        return to_record(AssessmentRecord, row)  # type: ignore[return-value]

    def get_assessment_owned(self, assessment_id: str, user_id: str) -> AssessmentRecord | None:
        row = self._fetch_one(
            "select a.id, a.project_id, a.user_id, a.label, a.quiz_ids, a.report, a.created_at "
            "from assessments a join projects p on p.id = a.project_id "
            "where a.id = %s and a.user_id = %s and p.user_id = %s",
            (assessment_id, user_id, user_id),
        )
        return to_record(AssessmentRecord, row)

    # ------------------------------------------------------------ learner
    def recent_quizzes(self, user_id: str, project_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Quiz history with per-quiz answer tallies for `GET /learner`."""
        rows = self._fetch_all(
            "select z.id, z.topic_id, t.slug as topic_slug, z.created_at, "
            "(select count(*) from questions q where q.quiz_id = z.id)::int as n_questions, "
            "(select count(*) from answers a where a.quiz_id = z.id and a.user_id = %s)::int as answered, "
            "(select coalesce(sum(case when a.correct then 1 else 0 end), 0) from answers a "
            " where a.quiz_id = z.id and a.user_id = %s)::int as correct "
            "from quizzes z left join topics t on t.id = z.topic_id "
            "where z.user_id = %s and z.project_id = %s "
            "order by z.created_at desc limit %s",
            (user_id, user_id, user_id, project_id, limit),
        )
        out = []
        for r in rows:
            parse_times(r, "created_at")
            out.append({**r, "id": str(r["id"]),
                        "topic_id": str(r["topic_id"]) if r["topic_id"] else None})
        return out
