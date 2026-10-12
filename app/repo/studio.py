"""Project scoped persistence for generated Studio artifacts."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Json

from .base import Repo, parse_times


def _parse_payload(row: dict | None) -> dict | None:
    if row is None:
        return None
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            row["payload"] = json.loads(payload) if payload else None
        except ValueError:
            row["payload"] = None
    parse_times(row, "created_at", "updated_at")
    return row


class StudioRepo(Repo):
    def create(self, project_id: str, user_id: str, kind: str, title: str, payload=None, status="queued"):
        return _parse_payload(self._fetch_one(
            "insert into studio_jobs (id, project_id, user_id, kind, title, status, payload) "
            "values (%s,%s,%s,%s,%s,%s,%s) returning *",
            (str(uuid.uuid4()), project_id, user_id, kind, title, status, Json(payload) if payload is not None else None),
        ))

    def update(self, job_id: str, status: str, *, title: str | None = None, payload=None, error: str = ""):
        return _parse_payload(self._fetch_one(
            "update studio_jobs set status=%s, title=coalesce(%s,title), payload=%s, error=%s, updated_at=now() "
            "where id=%s returning *",
            (status, title, Json(payload) if payload is not None else None, error[:2000], job_id),
        ))

    def list_for_project(self, project_id: str, user_id: str):
        return [_parse_payload(r) for r in self._fetch_all(
            "select j.* from studio_jobs j join projects p on p.id=j.project_id "
            "where j.project_id=%s and j.user_id=%s and p.user_id=%s order by j.created_at desc",
            (project_id, user_id, user_id),
        )]

    def get_owned(self, job_id: str, user_id: str):
        return _parse_payload(self._fetch_one(
            "select j.* from studio_jobs j join projects p on p.id=j.project_id "
            "where j.id=%s and j.user_id=%s and p.user_id=%s", (job_id, user_id, user_id),
        ))

    def delete_expired_failures(self, retention_hours: int = 24) -> int:
        """Remove failed quiz/exam/flashcard jobs after their short retention window."""
        kinds = ["quiz", "exam", "flashcards"]
        placeholders = ", ".join(["%s"] * len(kinds))
        # Cutoff computed in Python (portable): neither backend needs interval math.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).isoformat(sep=" ")
        return self._execute(
            "delete from studio_jobs where status='failed' "
            f"and kind in ({placeholders}) and updated_at < %s",
            (*kinds, cutoff),
        )
