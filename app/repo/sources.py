"""Source (uploaded file) repository."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Json

from .base import Repo, to_record, to_records
from .models import SourceRecord

_FIELDS = (
    "id", "project_id", "filename", "source_type", "status", "status_detail",
    "parser", "units", "duration_s", "chunk_count", "file_url", "warnings", "created_at",
)
_COLUMNS = ", ".join(_FIELDS)
# Same columns, prefixed for a JOIN against `projects`.
_COLUMNS_PREFIXED = ", ".join(f"s.{name}" for name in _FIELDS)


class SourceRepo(Repo):
    def create(self, project_id: str, filename: str, source_type: str = "other") -> SourceRecord:
        """Insert a `queued` row. Ingestion picks it up from here."""
        row = self._fetch_one(
            f"insert into sources (id, project_id, filename, source_type, status) "
            f"values (%s, %s, %s, %s, 'queued') returning {_COLUMNS}",
            (str(uuid.uuid4()), project_id, filename, source_type),
        )
        return to_record(SourceRecord, row)  # type: ignore[return-value]

    def ready_ids(self, project_id: str) -> list[str]:
        """Ids of this project's fully-ingested sources, oldest first.

        Chat and quiz pass these as `filter_source_ids` so retrieval only ever
        sees processed material — a source that is still queued/parsing (or
        failed) can never answer, even while the learner keeps chatting.
        """
        rows = self._fetch_all(
            "select id from sources where project_id = %s and status = 'ready' "
            "order by created_at",
            (project_id,),
        )
        return [str(r["id"]) for r in rows]

    # ------------------------------------------------------- parsed content
    def ready_chunks(self, project_id: str, source_ids: list[str], per_source: int = 4):
        """Bounded, project-scoped context, including images without topic rows."""
        return self._fetch_all(
            "select * from (select c.id,c.source_id,c.text,c.locator_kind,c.locator_value,"
            "c.heading,c.start_ts,c.end_ts,c.chunk_index, row_number() over "
            "(partition by c.source_id order by c.chunk_index) as rn "
            "from chunks c join sources s on s.id=c.source_id "
            "where c.project_id=%s and s.project_id=%s and s.status='ready' "
            "and c.source_id=any(%s::uuid[])) ranked where rn <= %s "
            "order by source_id, chunk_index",
            (project_id, project_id, source_ids, per_source),
        )

    def list_segments(self, source_id: str, user_id: str) -> list[dict[str, Any]]:
        """Timed chunks for the viewer's transcript rail, ownership-checked.

        Only chunks with a start timestamp (transcribed audio/video
        segments), oldest first. The join on projects proves the caller owns
        the source, so an id from another learner yields no rows.
        """
        return self._fetch_all(
            "select c.locator_kind,c.locator_value,c.heading,"
            "substring(c.text,1,600) as text,c.start_ts,c.end_ts,c.chunk_index "
            "from chunks c join sources s on s.id=c.source_id "
            "join projects p on p.id=s.project_id "
            "where c.source_id=%s and p.user_id=%s and c.start_ts is not null "
            "order by c.start_ts, c.chunk_index limit 1000",
            (source_id, user_id),
        )

    def save_content(self, source_id: str, markdown: str, truncated: bool = False) -> None:
        """Persist the parser's markdown for the viewer ("what the model read").

        Upserted so a re-ingest replaces the text instead of duplicating it.
        """
        self._execute(
            "insert into source_contents (source_id, markdown, truncated, chars, updated_at) "
            "values (%s, %s, %s, %s, now()) "
            "on conflict (source_id) do update set "
            "markdown = excluded.markdown, truncated = excluded.truncated, "
            "chars = excluded.chars, updated_at = now()",
            (source_id, markdown, truncated, len(markdown)),
        )

    def get_content(self, source_id: str) -> dict | None:
        """Parsed markdown for one source, or None when ingestion never stored it."""
        with self.db.connection() as conn:
            return conn.execute(
                "select source_id, markdown, truncated, chars, updated_at "
                "from source_contents where source_id = %s",
                (source_id,),
            ).fetchone()

    def get(self, source_id: str) -> SourceRecord | None:
        """Fetch by id without an ownership check — only for callers that
        already proved ownership of the enclosing quiz/chat/project."""
        row = self._fetch_one(
            f"select {_COLUMNS} from sources where id = %s",
            (source_id,),
        )
        return to_record(SourceRecord, row)

    def get_owned(self, source_id: str, user_id: str) -> SourceRecord | None:
        """Fetch a source only when it belongs to a project owned by `user_id`."""
        row = self._fetch_one(
            f"select {_COLUMNS_PREFIXED} from sources s "
            f"join projects p on p.id = s.project_id "
            f"where s.id = %s and p.user_id = %s",
            (source_id, user_id),
        )
        return to_record(SourceRecord, row)

    def list_for_project(self, project_id: str) -> list[SourceRecord]:
        rows = self._fetch_all(
            f"select {_COLUMNS} from sources where project_id = %s order by created_at desc",
            (project_id,),
        )
        return to_records(SourceRecord, rows)  # type: ignore[return-value]

    def set_status(
        self,
        source_id: str,
        status: str,
        detail: str = "",
        *,
        units: int | None = None,
        chunk_count: int | None = None,
        duration_s: float | None = None,
        parser: str | None = None,
        file_url: str | None = None,
        warnings: list | None = None,
    ) -> None:
        """Advance ingestion progress: queued -> parsing -> embedding -> ready|failed."""
        sets = ["status = %s", "status_detail = %s"]
        params: list[Any] = [status, detail]
        for column, value in (
            ("units", units),
            ("chunk_count", chunk_count),
            ("duration_s", duration_s),
            ("parser", parser),
            ("file_url", file_url),
            ("warnings", warnings),
        ):
            if value is not None:
                sets.append(f"{column} = %s")
                # jsonb columns need the JSON adapter. Passing a bare Python list
                # makes psycopg store an object, which then reads back as `{}`
                # instead of a list and fails response validation.
                params.append(Json(value) if column == "warnings" else value)
        params.append(source_id)
        self._execute(
            f"update sources set {', '.join(sets)} where id = %s",
            tuple(params),
        )

    def mark_failed(self, source_id: str, reason: str) -> None:
        """A readable failure. The reason is surfaced to the learner, not swallowed."""
        self._execute(
            "update sources set status = 'failed', status_detail = %s where id = %s",
            (reason[:500], source_id),
        )

    def mark_ready(
        self,
        source_id: str,
        *,
        units: int,
        chunk_count: int,
        parser: str,
        warnings: list | None = None,
        duration_s: float | None = None,
    ) -> None:
        self.set_status(
            source_id,
            "ready",
            f"{chunk_count} chunks from {units} units",
            units=units,
            chunk_count=chunk_count,
            parser=parser,
            warnings=warnings or [],
            duration_s=duration_s,
        )

    def count_for_project(self, project_id: str) -> int:
        with self.db.connection() as conn:
            row = conn.execute(
                "select count(*) as n from sources where project_id = %s", (project_id,)
            ).fetchone()
        return int(row["n"])

    def counts_by_status(self, project_id: str) -> dict[str, int]:
        """Ingestion progress for a project, e.g. {'ready': 3, 'queued': 1}."""
        rows = self._fetch_all(
            "select status, count(*) as n from sources where project_id = %s group by status",
            (project_id,),
        )
        return {r["status"]: int(r["n"]) for r in rows}

    def claim_pending(self) -> list[SourceRecord]:
        """Queued rows, oldest first. Ingestion drains these."""
        rows = self._fetch_all(
            f"select {_COLUMNS} from sources where status = 'queued' order by created_at"
        )
        return to_records(SourceRecord, rows)  # type: ignore[return-value]

    def delete_owned(self, source_id: str, user_id: str) -> bool:
        return (
            self._execute(
                "delete from sources s using projects p "
                "where s.id = %s and s.project_id = p.id and p.user_id = %s",
                (source_id, user_id),
            )
            > 0
        )

    def delete_for_project(self, source_id: str, project_id: str) -> bool:
        """Delete scoped to the project (the caller already proved ownership)."""
        return (
            self._execute(
                "delete from sources where id = %s and project_id = %s",
                (source_id, project_id),
            )
            > 0
        )

    def list_owned_in_project(self, project_id: str, user_id: str) -> list[SourceRecord]:
        """Sources in one project, but only if the caller owns that project."""
        rows = self._fetch_all(
            f"select {_COLUMNS_PREFIXED} from sources s "
            f"join projects p on p.id = s.project_id "
            f"where s.project_id = %s and p.user_id = %s order by s.created_at desc",
            (project_id, user_id),
        )
        return to_records(SourceRecord, rows)  # type: ignore[return-value]
