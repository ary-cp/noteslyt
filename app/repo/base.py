"""
Repository base: row -> record mapping and a small query helper.

All SQL in the app lives in `app/repo/*.py`. Routers and services never build SQL
themselves — they call repositories, which keeps the Postgres surface in one place
and makes the tenancy scoping (project_id / user_id) auditable in one directory.

Portability (experimental SQLite backend): repo SQL stays `%s`-style and is
translated by the SQLite cursor wrapper (`app/sqlite_db.py`: placeholders,
casts, `now()`). `to_record` rehydrates what SQLite cannot type — ISO
timestamps, 0/1 booleans and JSON text — using each dataclass's own field
types plus the `_JSON_FIELDS` registry below, so neither backend needs
per-query branches for plain reads.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, TypeVar

import psycopg

from ..db import Database

T = TypeVar("T")

# Record fields holding JSON text on SQLite (dicts on Postgres via jsonb).
# to_record parses them back; the SQLite param adapter serializes on write.
_JSON_FIELDS: dict[str, set[str]] = {
    "SourceRecord": {"warnings"},
    "ChatMessageRecord": {"citations"},
    "QuestionRecord": {"options", "answer_key", "stem_embedding"},
    "AnswerRecord": {"missed", "matched"},
    "AssessmentRecord": {"quiz_ids", "report"},
}

_sqlite_learner_locks: dict[str, threading.Lock] = {}
_sqlite_learner_guard = threading.Lock()


def _coerce(value: Any) -> Any:
    """Normalize a DB value to the type the dataclass declares.

    psycopg hands back `uuid.UUID` for uuid columns, but every model declares
    `id: str` because those values are re-bound as SQL parameters and returned
    over JSON. Converting here keeps one convention across all repositories
    instead of sprinkling `str()` calls through every method.
    """
    return str(value) if isinstance(value, uuid.UUID) else value


def _coerce_field(cls: type[T], name: str, value: Any) -> Any:  # type: ignore[valid-type]
    """Per-field rehydration for the SQLite backend (no-op for Postgres values)."""
    if value is None:
        return None
    try:
        annotation = next(f.type for f in dataclasses.fields(cls)  # type: ignore[arg-type]
                          if f.name == name)
    except StopIteration:
        return _coerce(value)
    hint = str(annotation)
    if isinstance(value, str):
        if "datetime" in hint:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return _coerce(value)
        if getattr(cls, "__name__", "") in _JSON_FIELDS and name in _JSON_FIELDS[cls.__name__]:  # type: ignore[attr-defined]
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return _coerce(value)
    if "bool" in hint and isinstance(value, int) and not isinstance(value, bool):
        return bool(value)
    return _coerce(value)


def to_record(cls: type[T], row: dict[str, Any] | None) -> T | None:
    """Build a dataclass from a DB row, ignoring columns it doesn't declare."""
    if row is None:
        return None
    fields = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: _coerce_field(cls, k, v) for k, v in row.items() if k in fields})  # type: ignore[arg-type]


def to_records(cls: type[T], rows: list[dict[str, Any]]) -> list[T]:
    return [to_record(cls, r) for r in rows]  # type: ignore[misc]


def parse_times(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Normalize raw-row timestamps to datetime (SQLite returns ISO strings).

    Routers call `.isoformat()` on these dicts, so they must hold datetimes on
    both backends. Mutates and returns the row for inline use.
    """
    from datetime import datetime as _datetime

    for key in keys:
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = _datetime.fromisoformat(value)
            except ValueError:
                pass
    return row


class Repo:
    def __init__(self, db: Database):
        self.db = db

    @contextmanager
    def learner_write(self, user_id: str, project_id: str):
        """Serialize assessment and mastery writes, including initially absent rows."""
        if getattr(self.db, "backend", "postgres") == "sqlite":
            # No advisory locks on SQLite: the single-writer connection plus a
            # per-learner mutex gives the same serialization.
            key = f"learner:{user_id}:{project_id}"
            with _sqlite_learner_guard:
                lock = _sqlite_learner_locks.setdefault(key, threading.Lock())
            with lock, self.db.transaction() as conn:
                yield
            return
        with self.db.transaction() as conn:
            conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (f"learner:{user_id}:{project_id}",))
            yield

    def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.db.connection() as conn:
            return list(conn.execute(sql, params).fetchall())

    def _fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.db.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """Run a write inside a transaction. Returns rows affected."""
        with self.db.transaction() as conn:
            result = conn.execute(sql, params)
            return result.rowcount


def as_uuid_list(values: list[str] | None) -> list[str] | None:
    """Normalize an optional list of ids for a Postgres uuid[] parameter.

    Passing an empty list would make `= any('{}')` match nothing, which is almost
    never what the caller meant — treat empty as "no filter" (None).
    """
    if not values:
        return None
    return [str(v) for v in values]


__all__ = ["Repo", "to_record", "to_records", "parse_times", "as_uuid_list", "psycopg"]
