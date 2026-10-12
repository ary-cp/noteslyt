"""Project-level topic repository.

Topics belong to the PROJECT, not to an individual file. That is what lets a
project's "weak topic" list stay coherent when the same subject is covered by a
PDF *and* a lecture video (plan.md section 1). `topic_sources` records which
sources cover a topic and where.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .base import Repo, to_record, to_records
from .models import TopicRecord

_FIELDS = ("id", "project_id", "slug", "name", "description", "created_at")
_COLUMNS = ", ".join(_FIELDS)


class TopicRepo(Repo):
    def upsert(
        self,
        project_id: str,
        slug: str,
        name: str,
        description: str = "",
    ) -> TopicRecord:
        """Create or update a topic by (project_id, slug) — the natural key."""
        row = self._fetch_one(
            f"insert into topics (id, project_id, slug, name, description) "
            f"values (%s, %s, %s, %s, %s) "
            f"on conflict (project_id, slug) do update "
            f"set name = excluded.name, description = excluded.description "
            f"returning {_COLUMNS}",
            (str(uuid.uuid4()), project_id, slug, name, description),
        )
        return to_record(TopicRecord, row)  # type: ignore[return-value]

    def get(self, topic_id: str) -> TopicRecord | None:
        row = self._fetch_one(f"select {_COLUMNS} from topics where id = %s", (topic_id,))
        return to_record(TopicRecord, row)

    def list_for_project(self, project_id: str) -> list[TopicRecord]:
        rows = self._fetch_all(
            f"select {_COLUMNS} from topics where project_id = %s order by name",
            (project_id,),
        )
        return to_records(TopicRecord, rows)  # type: ignore[return-value]

    def list_for_sources(self, project_id: str, source_ids: list[str]) -> list[TopicRecord]:
        """Project topics linked to at least one selected source."""
        if not source_ids:
            return []
        placeholders = ", ".join(["%s"] * len(source_ids))
        rows = self._fetch_all(
            f"select distinct t.{_FIELDS[0]}, "
            + ", ".join(f"t.{name}" for name in _FIELDS[1:])
            + " from topics t join topic_sources ts on ts.topic_id = t.id "
            f"where t.project_id = %s and ts.source_id in ({placeholders}) order by t.name",
            (project_id, *source_ids),
        )
        return to_records(TopicRecord, rows)  # type: ignore[return-value]

    def link_source(
        self, topic_id: str, source_id: str, locators: list[dict[str, Any]] | None = None
    ) -> None:
        """Record that `source_id` covers `topic_id`, and where."""
        self._execute(
            "insert into topic_sources (topic_id, source_id, locators) "
            "values (%s, %s, %s::jsonb) "
            "on conflict (topic_id, source_id) do update set locators = excluded.locators",
            (topic_id, source_id, json.dumps(locators or [])),
        )

    def sources_for(self, topic_id: str) -> list[dict[str, Any]]:
        """Which sources cover a topic — used to show topic coverage in the UI."""
        rows = self._fetch_all(
            "select ts.source_id, ts.locators, s.filename, s.source_type "
            "from topic_sources ts join sources s on s.id = ts.source_id "
            "where ts.topic_id = %s",
            (topic_id,),
        )
        for r in rows:
            if isinstance(r.get("locators"), str):
                try:
                    r["locators"] = json.loads(r["locators"])
                except ValueError:
                    r["locators"] = []
        return rows

    def set_edges(self, project_id: str, edges: list[tuple[str, str]]) -> None:
        """Replace the prerequisite graph for a project. `edges` = (topic_id, prereq_id)."""
        with self.db.transaction() as conn:
            conn.execute("delete from topic_edges where project_id = %s", (project_id,))
            for topic_id, prereq_id in edges:
                conn.execute(
                    "insert into topic_edges (project_id, topic_id, prereq_id) "
                    "values (%s, %s, %s) on conflict do nothing",
                    (project_id, topic_id, prereq_id),
                )

    def count_for_project(self, project_id: str) -> int:
        with self.db.connection() as conn:
            row = conn.execute(
                "select count(*) as n from topics where project_id = %s", (project_id,)
            ).fetchone()
        return int(row["n"])

    def edges(self, project_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            "select topic_id, prereq_id from topic_edges where project_id = %s",
            (project_id,),
        )
