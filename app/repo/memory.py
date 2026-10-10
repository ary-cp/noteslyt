"""Tier-2 project memory: BKT mastery per (user, project, topic) — Phase E.26.

Mastery was re-keyed from `(user_id, document_id, topic_id)` to
`(user_id, project_id, topic_id)` so it spans every source in the project.
Two writers:

  * `apply_result` — assessment evidence (Phase F quizzes): full BKT update,
    attempts/correct counters move.
  * `apply_nudge`  — chat evidence (Phase E): a bounded ±5%-of-remaining
    step that never touches the counters. A nudged row still flips
    `not_started` → `in_progress`, which is how "no chat evidence" is
    detected without a schema change.

`coverage_status` is derived, never stored independently:
  mastered    — p_mastery ≥ 0.8 and attempts ≥ 3
  in_progress — any attempt, or any chat nudge (p ≠ prior)
  not_started — otherwise
"""

from __future__ import annotations

from typing import Any

from ..learner.bkt import BKTParams, label, update
from .base import Repo, to_record, to_records
from .models import TopicMemoryRecord

MASTERED_P = 0.8
MASTERED_ATTEMPTS = 3


def derive_coverage(p_mastery: float, attempts: int, p_init: float) -> str:
    if p_mastery >= MASTERED_P and attempts >= MASTERED_ATTEMPTS:
        return "mastered"
    if attempts > 0 or abs(p_mastery - p_init) > 1e-9:
        return "in_progress"
    return "not_started"


class ProjectMemoryRepo(Repo):
    def __init__(self, db, p_init: float = 0.30):
        super().__init__(db)
        self.p_init = p_init

    # ------------------------------------------------------------- reads
    def list_for_project(self, user_id: str, project_id: str) -> list[TopicMemoryRecord]:
        """Every project topic with this learner's mastery (defaults when unseen)."""
        rows = self._fetch_all(
            "select %s as user_id, t.project_id, t.id as topic_id, t.slug, t.name, "
            "t.description, coalesce(m.p_mastery, %s) as p_mastery, "
            "coalesce(m.attempts, 0) as attempts, coalesce(m.correct, 0) as correct, "
            "coalesce(m.coverage_status, 'not_started') as coverage_status, "
            "m.last_practiced_at "
            "from topics t left join project_memory m "
            "on m.topic_id = t.id and m.user_id = %s and m.project_id = %s "
            "where t.project_id = %s order by t.name",
            (user_id, self.p_init, user_id, project_id, project_id),
        )
        return to_records(TopicMemoryRecord, rows)  # type: ignore[return-value]

    def get(self, user_id: str, project_id: str, topic_id: str) -> TopicMemoryRecord | None:
        row = self._fetch_one(
            "select user_id, project_id, topic_id, p_mastery, attempts, correct, "
            "coverage_status, last_practiced_at from project_memory "
            "where user_id = %s and project_id = %s and topic_id = %s",
            (user_id, project_id, topic_id),
        )
        return to_record(TopicMemoryRecord, row)

    # ------------------------------------------------------------ writes
    def apply_result(
        self, user_id: str, project_id: str, topic_id: str, correct: bool, params: BKTParams
    ) -> dict[str, Any]:
        """Full BKT update from a graded assessment attempt (Phase F)."""
        with self.learner_write(user_id, project_id):
            row = self.get(user_id, project_id, topic_id)
            p = float(row.p_mastery) if row else self.p_init
            attempts = int(row.attempts) + 1 if row else 1
            hits = int(row.correct) + (1 if correct else 0) if row else (1 if correct else 0)
            new_p = update(p, correct, params)
            status = derive_coverage(new_p, attempts, self.p_init)
            return self._upsert(user_id, project_id, topic_id, new_p, attempts, hits, status)

    def apply_nudge(self, user_id: str, project_id: str, topic_id: str, correct: bool) -> dict[str, Any]:
        """Bounded chat-evidence step: ±5% of the remaining distance, counters untouched."""
        with self.learner_write(user_id, project_id):
            row = self.get(user_id, project_id, topic_id)
            p = float(row.p_mastery) if row else self.p_init
            attempts = int(row.attempts) if row else 0
            hits = int(row.correct) if row else 0
            new_p = round(min(max(p + (0.05 * (1 - p) if correct else -0.05 * p), 0.02), 0.98), 4)
            status = derive_coverage(new_p, attempts, self.p_init)
            return self._upsert(user_id, project_id, topic_id, new_p, attempts, hits, status)

    def _upsert(
        self, user_id: str, project_id: str, topic_id: str,
        p: float, attempts: int, correct: int, status: str,
    ) -> dict[str, Any]:
        row = self._fetch_one(
            "insert into project_memory "
            "(user_id, project_id, topic_id, p_mastery, attempts, correct, coverage_status, last_practiced_at, updated_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, now(), now()) "
            "on conflict (user_id, project_id, topic_id) do update set "
            "p_mastery = excluded.p_mastery, attempts = excluded.attempts, "
            "correct = excluded.correct, coverage_status = excluded.coverage_status, "
            "last_practiced_at = now(), updated_at = now() "
            "returning user_id, project_id, topic_id, p_mastery, attempts, correct, coverage_status, last_practiced_at",
            (user_id, project_id, topic_id, p, attempts, correct, status),
        )
        assert row is not None
        return {k: (str(v) if k.endswith("_id") else v) for k, v in row.items()}

    # ------------------------------------------------------------ streak
    def streak(self, user_id: str, project_id: str) -> dict[str, int]:
        """Study streak from the learner's own message days in this project."""
        rows = self._fetch_all(
            "select distinct (m.created_at at time zone 'UTC')::date as day "
            "from chat_messages m join chats c on c.id = m.chat_id "
            "where c.project_id = %s and c.user_id = %s and m.role = 'user' "
            "and m.created_at >= now() - interval '14 days' order by day desc",
            (project_id, user_id),
        )
        days = [str(r["day"]) for r in rows]
        current = _consecutive(days)
        return {"current_days": current, "active_days_14": len(days)}

    def mastery_label(self, p_mastery: float) -> str:
        return label(p_mastery)


def _consecutive(day_strings: list[str]) -> int:
    """Run of consecutive days ending today (or yesterday — streaks survive a rest day at query time)."""
    from datetime import date, timedelta

    if not day_strings:
        return 0
    days = {date.fromisoformat(d) for d in day_strings}
    today = date.today()
    cursor = today if today in days else today - timedelta(days=1)
    if cursor not in days:
        return 0
    run = 0
    while cursor in days:
        run += 1
        cursor -= timedelta(days=1)
    return run
