"""Project ("book") repository — the tenancy boundary."""

from __future__ import annotations

import uuid
from typing import Any

from .base import Repo, to_record, to_records
from .models import ProjectRecord

_COLUMNS = "id, user_id, title, subject, description, archived, created_at, updated_at"


class ProjectRepo(Repo):
    def create(
        self,
        user_id: str,
        title: str,
        subject: str = "",
        description: str = "",
    ) -> ProjectRecord:
        row_id = str(uuid.uuid4())
        row = self._fetch_one(
            f"insert into projects (id, user_id, title, subject, description) "
            f"values (%s, %s, %s, %s, %s) returning {_COLUMNS}",
            (row_id, user_id, title, subject, description),
        )
        return to_record(ProjectRecord, row)  # type: ignore[return-value]

    def get_owned(self, project_id: str, user_id: str) -> ProjectRecord | None:
        """Fetch a project only if it belongs to `user_id` — the tenancy check.

        Callers that expose a project id to the browser should treat None as 404
        (not 403), so project ids are not enumerable across users.
        """
        row = self._fetch_one(
            f"select {_COLUMNS} from projects where id = %s and user_id = %s",
            (project_id, user_id),
        )
        return to_record(ProjectRecord, row)

    def list_for_user(self, user_id: str) -> list[ProjectRecord]:
        rows = self._fetch_all(
            f"select {_COLUMNS} from projects where user_id = %s order by created_at desc",
            (user_id,),
        )
        return to_records(ProjectRecord, rows)  # type: ignore[return-value]

    def update(self, project_id: str, user_id: str, **fields: Any) -> ProjectRecord | None:
        allowed = {"title", "subject", "description", "archived"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_owned(project_id, user_id)
        assignments = ", ".join(f"{k} = %s" for k in updates)
        params = tuple(updates.values()) + (project_id, user_id)
        row = self._fetch_one(
            f"update projects set {assignments}, updated_at = now() "
            f"where id = %s and user_id = %s returning {_COLUMNS}",
            params,
        )
        return to_record(ProjectRecord, row)

    def delete(self, project_id: str, user_id: str) -> bool:
        """Cascade-deletes sources, chunks, topics, chats and project_memory."""
        return (
            self._execute(
                "delete from projects where id = %s and user_id = %s",
                (project_id, user_id),
            )
            > 0
        )