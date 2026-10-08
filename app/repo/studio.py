"""Project scoped persistence for generated Studio artifacts."""
from __future__ import annotations

import uuid

from psycopg.types.json import Json

from .base import Repo


class StudioRepo(Repo):
    def create(self, project_id: str, user_id: str, kind: str, title: str, payload=None, status="queued"):
        return self._fetch_one(
            "insert into studio_jobs (id, project_id, user_id, kind, title, status, payload) "
            "values (%s,%s,%s,%s,%s,%s,%s) returning *",
            (str(uuid.uuid4()), project_id, user_id, kind, title, status, Json(payload) if payload is not None else None),
        )

    def update(self, job_id: str, status: str, *, title: str | None = None, payload=None, error: str = ""):
        return self._fetch_one(
            "update studio_jobs set status=%s, title=coalesce(%s,title), payload=%s, error=%s, updated_at=now() "
            "where id=%s returning *",
            (status, title, Json(payload) if payload is not None else None, error[:2000], job_id),
        )

    def list_for_project(self, project_id: str, user_id: str):
        return self._fetch_all(
            "select j.* from studio_jobs j join projects p on p.id=j.project_id "
            "where j.project_id=%s and j.user_id=%s and p.user_id=%s order by j.created_at desc",
            (project_id, user_id, user_id),
        )

    def get_owned(self, job_id: str, user_id: str):
        return self._fetch_one(
            "select j.* from studio_jobs j join projects p on p.id=j.project_id "
            "where j.id=%s and j.user_id=%s and p.user_id=%s", (job_id, user_id, user_id),
        )

    def delete_expired_failures(self, retention_hours: int = 24) -> int:
        """Remove failed quiz/exam/flashcard jobs after their short retention window."""
        return self._execute(
            "delete from studio_jobs where status='failed' "
            "and kind = any(%s) and updated_at < now() - (%s * interval '1 hour')",
            (["quiz", "exam", "flashcards"], retention_hours),
        )
