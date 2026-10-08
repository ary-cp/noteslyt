"""
Repository base: row -> record mapping and a small query helper.

All SQL in the app lives in `app/repo/*.py`. Routers and services never build SQL
themselves — they call repositories, which keeps the Postgres surface in one place
and makes the tenancy scoping (project_id / user_id) auditable in one directory.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, TypeVar

import psycopg

from ..db import Database

T = TypeVar("T")


def _coerce(value: Any) -> Any:
    """Normalize a DB value to the type the dataclass declares.

    psycopg hands back `uuid.UUID` for uuid columns, but every model declares
    `id: str` because those values are re-bound as SQL parameters and returned
    over JSON. Converting here keeps one convention across all repositories
    instead of sprinkling `str()` calls through every method.
    """
    return str(value) if isinstance(value, uuid.UUID) else value


def to_record(cls: type[T], row: dict[str, Any] | None) -> T | None:
    """Build a dataclass from a DB row, ignoring columns it doesn't declare."""
    if row is None:
        return None
    fields = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: _coerce(v) for k, v in row.items() if k in fields})  # type: ignore[arg-type]


def to_records(cls: type[T], rows: list[dict[str, Any]]) -> list[T]:
    return [to_record(cls, r) for r in rows]  # type: ignore[misc]


class Repo:
    def __init__(self, db: Database):
        self.db = db

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


__all__ = ["Repo", "to_record", "to_records", "as_uuid_list", "psycopg"]