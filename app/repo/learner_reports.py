"""Personalised learning reports: one row per quiz/exam/chat (Phase: personalization).

Written best-effort in the background after each interaction — never blocks a
turn or an answer. The Learning Profile page reads these for history; the
aggregated mastery itself stays in ``project_memory`` (BKT) + ``user_profiles``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .base import Repo, to_record, to_records
from .models import LearnerReportRecord


def _dump(value: Any) -> str:
    try:
        return json.dumps(value or ({} if isinstance(value, dict) else value))
    except (TypeError, ValueError):
        return "{}"


class LearnerReportRepo(Repo):
    def create(
        self,
        user_id: str,
        project_id: str,
        kind: str,
        ref_id: str = "",
        summary: str = "",
        bkt: dict | None = None,
        irt: dict | None = None,
        strengths: list | None = None,
        weaknesses: list | None = None,
    ) -> LearnerReportRecord:
        from psycopg.types.json import Json

        kind = str(kind or "quiz").strip().lower() or "quiz"
        if kind not in ("quiz", "exam", "chat"):
            kind = "quiz"
        row = self._fetch_one(
            "insert into learner_reports (id, user_id, project_id, kind, ref_id, summary, "
            "bkt, irt, strengths, weaknesses) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "returning id, user_id, project_id, kind, ref_id, summary, bkt, irt, "
            "strengths, weaknesses, created_at",
            (
                str(uuid.uuid4()), user_id, project_id, kind, str(ref_id or ""),
                str(summary or "")[:4000],
                Json(bkt or {}), Json(irt or {}),
                Json(list(strengths or [])[:12]), Json(list(weaknesses or [])[:12]),
            ),
        )
        return to_record(LearnerReportRecord, row)  # type: ignore[return-value]

    def list_for_user(self, user_id: str, limit: int = 20) -> list[LearnerReportRecord]:
        rows = self._fetch_all(
            "select id, user_id, project_id, kind, ref_id, summary, bkt, irt, "
            "strengths, weaknesses, created_at from learner_reports "
            "where user_id = %s order by created_at desc limit %s",
            (user_id, max(1, min(int(limit or 20), 100))),
        )
        return to_records(LearnerReportRecord, rows)  # type: ignore[return-value]

    def list_for_project(
        self, user_id: str, project_id: str, limit: int = 20
    ) -> list[LearnerReportRecord]:
        rows = self._fetch_all(
            "select id, user_id, project_id, kind, ref_id, summary, bkt, irt, "
            "strengths, weaknesses, created_at from learner_reports "
            "where user_id = %s and project_id = %s order by created_at desc limit %s",
            (user_id, project_id, max(1, min(int(limit or 20), 100))),
        )
        return to_records(LearnerReportRecord, rows)  # type: ignore[return-value]
