"""
SQLite backend: zero-install default when `DATABASE_URL` is unset.

Same method surface as `app/db.py:Database` (`connection`, `transaction`,
`close`, `health`, `count`, `vector_width`, `embedding_dim`), so repositories
and services take either without branching. Two deliberate translations make
that possible:

1. **Placeholders.** Repo SQL is written `%s`-style (psycopg). The cursor
   wrapper rewrites `%s`/`%(name)s` to `?` and adapts values (bool/datetime/
   dict/list/psycopg-Json all have SQLite-safe forms). Postgres-only syntax
   that cannot be mechanically translated (`= any(...)`, jsonb operators,
   `FILTER`, `FOR UPDATE`) was rewritten portably at the call site instead —
   grep for `backend ==` to find those branches.
2. **Casts + clocks.** `expr::int`-style casts are stripped (SQLite infers),
   `now()` becomes `datetime('now')`. Timestamps come back as ISO strings and
   are rehydrated by `app/repo/base.py:to_record`.

Vectors do NOT live here at all — see `app/vectorstore_chroma.py`. The
`chunks` table keeps text + metadata (for topic grounding/counts/cascades);
similarity lives in Chroma. Single-writer discipline: one shared connection
behind an RLock (WAL mode for concurrent readers), nested transactions via a
ContextVar exactly like the Postgres pool wrapper.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .config import Settings

log = logging.getLogger(__name__)

# expr::type — SQLite infers; the cast is only load-bearing on Postgres.
_CAST_RE = re.compile(
    r"::\s*(?:jsonb|uuid|text|int(?:eger)?|bigint|real|float|double\s+precision|boolean|date)\b",
    re.IGNORECASE,
)
# now() outside string literals (our SQL never embeds the word in literals).
_NOW_RE = re.compile(r"(?<![\w'])now\(\)", re.IGNORECASE)
# Trailing row lock: single-writer SQLite serializes in transaction() already.
_FOR_UPDATE_RE = re.compile(r"\s+for\s+update\s*;?\s*$", re.IGNORECASE)
_PYFORMAT_RE = re.compile(r"%\((\w+)\)s")


def _adapt_param(value: Any) -> Any:
    """One bound value to its SQLite form (mirrors psycopg adaptation)."""
    if value is None or isinstance(value, (str, int, float, bytes)):
        if isinstance(value, bool):
            return int(value)
        return value
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    # psycopg Json wrapper (repos keep using it for the pg path).
    obj = getattr(value, "obj", None)
    if obj is not None and type(value).__name__ == "Json":
        return json.dumps(obj)
    # pgvector Vector wrapper (stem embeddings; stored as JSON text here).
    to_list = getattr(value, "to_list", None)
    if callable(to_list) and type(value).__name__ == "Vector":
        return json.dumps([float(x) for x in to_list()])
    if isinstance(value, tuple):
        return tuple(_adapt_param(v) for v in value)
    return value


def _translate(sql: str, params: Any) -> tuple[str, Any]:
    """Rewrite one statement's placeholders + pg-isms for sqlite3."""
    if isinstance(params, dict):
        names = _PYFORMAT_RE.findall(sql)
        sql = _PYFORMAT_RE.sub("?", sql)
        params = tuple(params[n] for n in names)
    else:
        sql = sql.replace("%s", "?")
    sql = _CAST_RE.sub("", sql)
    sql = _NOW_RE.sub("datetime('now')", sql)
    sql = _FOR_UPDATE_RE.sub("", sql)
    if isinstance(params, (list, tuple)):
        params = tuple(_adapt_param(v) for v in params)
    elif params is not None:
        params = _adapt_param(params)
    return sql, params if params is not None else ()


class _Cursor:
    """sqlite3 cursor speaking psycopg-ish: dict rows, rowcount, `with` support."""

    def __init__(self, raw: sqlite3.Cursor):
        self._raw = raw

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        self._raw.close()

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount

    def _rows(self) -> list[str]:
        return [d[0] for d in (self._raw.description or [])]

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        sql, params = _translate(sql, params)
        self._raw.execute(sql, params)
        return self

    def executemany(self, sql: str, seq: Any) -> _Cursor:
        sql, _ = _translate(sql, ())
        adapted = [tuple(_adapt_param(v) for v in row) for row in seq]
        self._raw.executemany(sql, adapted)
        return self

    def fetchone(self) -> dict[str, Any] | None:
        row = self._raw.fetchone()
        if row is None:
            return None
        return dict(zip(self._rows(), row))

    def fetchall(self) -> list[dict[str, Any]]:
        cols = self._rows()
        return [dict(zip(cols, row)) for row in self._raw.fetchall()]


class _Connection:
    def __init__(self, raw: sqlite3.Connection):
        self._raw = raw

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        return _Cursor(self._raw.cursor()).execute(sql, params)

    def cursor(self) -> _Cursor:
        return _Cursor(self._raw.cursor())


class SqliteDatabase:
    """Thin single-connection wrapper. Repositories own all the SQL."""

    backend = "sqlite"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._active = ContextVar(f"sqlite_connection_{id(self)}", default=None)
        self._depth = ContextVar(f"sqlite_txn_depth_{id(self)}", default=0)
        self._lock = threading.RLock()
        path = settings.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0,
                                  isolation_level=None)
        except sqlite3.Error as exc:
            raise RuntimeError(f"Could not open SQLite database at {path}: {exc}") from exc
        raw.execute("pragma journal_mode=wal")
        raw.execute("pragma foreign_keys=ON")
        raw.execute("pragma busy_timeout=30000")
        self._raw = raw
        self._conn = _Connection(raw)
        self.pending_migrations = self._pending_migrations()

    # ------------------------------------------------------------- lifecycle
    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        """Borrow the shared connection; commits on success, rolls back on error."""
        active = self._active.get()
        if active is not None:
            yield active
            return
        with self._lock:
            token = self._active.set(self._conn)
            try:
                yield self._conn
            finally:
                self._active.reset(token)

    @contextmanager
    def transaction(self) -> Iterator[_Connection]:
        """Run a unit of work atomically. Read-only callers use `connection`."""
        with self.connection() as conn:
            depth = self._depth.get()
            if depth > 0:
                token = self._depth.set(depth + 1)
                try:
                    yield conn
                finally:
                    self._depth.reset(token)
                return
            token = self._depth.set(1)
            try:
                self._raw.execute("begin immediate")
                yield conn
                self._raw.execute("commit")
            except Exception:
                try:
                    self._raw.execute("rollback")
                except Exception:
                    pass
                raise
            finally:
                self._depth.reset(token)

    def close(self) -> None:
        with self._lock:
            self._raw.close()

    # ----------------------------------------------------------- diagnostics
    def _pending_migrations(self) -> list[str]:
        from .migration_runner import _migration_files

        files = _migration_files(self._settings.sqlite_migrations_dir)
        with self.connection() as conn:
            exists = conn.execute(
                "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
            ).fetchone()
            if not exists:
                return [p.name for _, p in files]
            done = {r["version"] for r in
                    conn.execute("select version from schema_migrations").fetchall()}
        return [p.name for v, p in files if v not in done]

    def health(self) -> dict[str, Any]:
        return {
            "target": self._settings.db_target,
            "server": f"SQLite {sqlite3.sqlite_version}",
            "migrations_pending": self._pending_migrations(),
        }

    def count(self, table: str) -> int:
        """Row count for /health. `table` must be a hard-coded literal, never user input."""
        with self.connection() as conn:
            row = conn.execute(f"select count(*) as n from {table}").fetchone()
        return int(row["n"])

    def vector_width(self, table: str, column: str) -> int | None:
        """No fixed vector columns on SQLite (Chroma is dim-flexible) — None."""
        if (table, column) not in (("chunks", "embedding"), ("questions", "stem_embedding")):
            raise ValueError(f"Not a tracked vector column: {table}.{column}")
        return None

    def embedding_dim(self) -> int | None:
        return None


__all__ = ["SqliteDatabase"]
