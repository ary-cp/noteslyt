"""
Postgres connection pool and transaction helpers.

One local Postgres instance, reached over a DSN:

    postgresql://postgres:YOUR_PASSWORD@localhost:5432/StudyCompanion

We speak to Postgres directly over psycopg3. That is what makes pgvector
available locally, and it keeps all the SQL in one place — including
`match_chunks`, the function that carries the project-scoping guarantee.

The previous implementation reached Postgres through a PostgREST-style client
plus a hand-duplicated in-memory twin. Those two branches had already drifted,
so one code path with real transactions is simpler and safer (plan.md D2).

`prepare_threshold=None` disables psycopg's prepared statements. We do not need
them, and turning them off keeps the code working behind any transaction-mode
proxy with no behavioural change for this workload.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import Settings

log = logging.getLogger(__name__)


def _configure(conn: psycopg.Connection) -> None:
    """Runs on every new pooled connection.

    Teaches psycopg how to adapt `pgvector.Vector` objects, returns dict rows so
    repositories read columns by name, and turns off prepared statements.
    """
    conn.row_factory = dict_row
    conn.prepare_threshold = None
    register_vector(conn)


class DatabaseError(RuntimeError):
    """Raised when Postgres is unreachable or the schema is not usable."""


REEMBED_HINT = (
    "Vectors from different embedding models are incomparable, so swapping "
    "models means re-embedding the whole corpus (padding/truncating would "
    "silently corrupt retrieval):\n"
    "  1. Set EMBEDDING_DIM to your model's output width "
    "(1536 OpenAI text-embedding-3-small, 768 nomic/EmbeddingGemma, "
    "3072 OpenAI text-embedding-3-large).\n"
    "  2. Run:  uv run python scripts/re_embed.py\n"
    "     (`--check` only reports; the default run alters the column, wipes "
    "the old vectors, and re-ingests every stored file. Stop the API first.)"
)


def describe_embedding_drift(dims: dict[str, int | None]) -> list[str]:
    """Compare every known vector width; empty list means everything agrees.

    `None` means "not present yet" (fresh DB, missing function) and is
    skipped rather than treated as drift.
    """
    known = {name: width for name, width in dims.items() if width is not None}
    if len(set(known.values())) <= 1:
        return []
    detail = ", ".join(f"{name}={width}" for name, width in known.items())
    return [f"Embedding width drift: {detail}."]


class Database:
    """Thin pool wrapper. Repositories in `app/repo/` own all the SQL."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._active_connection = ContextVar(f"db_connection_{id(self)}", default=None)
        url = settings.database_url
        if not url:
            raise DatabaseError(
                "DATABASE_URL is not set. Add it to .env, e.g.\n"
                "  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/StudyCompanion"
            )
        try:
            self._pool = ConnectionPool(
                conninfo=url,
                min_size=settings.db_pool_min,
                max_size=settings.db_pool_max,
                configure=_configure,
                open=False,
            )
            self._pool.open(wait=True, timeout=settings.db_connect_timeout_s)
        except psycopg.Error as exc:
            raise DatabaseError(
                f"Could not connect to {settings.db_target}: {exc}\n"
                "Is Postgres running, and is the password in DATABASE_URL correct?"
            ) from exc
        self.check_ready()

    # ------------------------------------------------------------- lifecycle
    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Borrow a connection; commits on success, rolls back on error."""
        active = self._active_connection.get()
        if active is not None:
            yield active
            return
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """Run a unit of work atomically. Read-only callers should use `connection`."""
        with self.connection() as conn:
            with conn.transaction():
                token = self._active_connection.set(conn)
                try:
                    yield conn
                finally:
                    self._active_connection.reset(token)

    def close(self) -> None:
        self._pool.close()

    # ----------------------------------------------------------- diagnostics
    def check_ready(self) -> None:
        """
        Verify the two things whose absence fails in deeply confusing ways later:
        the pgvector extension, and applied migrations.
        """
        with self.connection() as conn:
            try:
                has_vector = conn.execute(
                    "select 1 from pg_extension where extname = 'vector'"
                ).fetchone()
            except psycopg.Error as exc:
                raise DatabaseError(f"Postgres health query failed: {exc}") from exc
            if not has_vector:
                raise DatabaseError(
                    "The pgvector extension is not enabled on this database.\n"
                    "Run: create extension vector;  (and install the extension files first "
                    "if Postgres reports it is not available)."
                )
            self.pending_migrations = self._pending_migrations(conn)

    def _pending_migrations(self, conn: psycopg.Connection) -> list[str]:
        exists = conn.execute(
            "select 1 from information_schema.tables "
            "where table_schema = 'public' and table_name = 'schema_migrations'"
        ).fetchone()
        if not exists:
            return [p.name for p in sorted(self._settings.migrations_dir.glob("*.sql"))]
        done = {r["version"] for r in conn.execute("select version from schema_migrations")}
        return [p.name for p in sorted(self._settings.migrations_dir.glob("*.sql")) if p.stem[:4] not in done]

    def health(self) -> dict[str, Any]:
        with self.connection() as conn:
            server = conn.execute("select version()").fetchone()["version"].split(",")[0]
            pending = self._pending_migrations(conn)
        return {
            "target": self._settings.db_target,
            "server": server,
            "migrations_pending": pending,
        }

    def count(self, table: str) -> int:
        """Row count for /health. `table` must be a hard-coded literal, never user input."""
        with self.connection() as conn:
            row = conn.execute(f"select count(*) as n from {table}").fetchone()
        return int(row["n"])

    def vector_width(self, table: str, column: str) -> int | None:
        """Live width of a `vector(N)` column, or None before it exists."""
        if (table, column) not in (("chunks", "embedding"), ("questions", "stem_embedding")):
            raise ValueError(f"Not a tracked vector column: {table}.{column}")
        with self.connection() as conn:
            row = conn.execute(
                "select format_type(a.atttypid, a.atttypmod) as typ "
                "from pg_attribute a join pg_class c on c.oid = a.attrelid "
                "where c.relname = %s and a.attname = %s and not a.attisdropped",
                (table, column),
            ).fetchone()
        if not row:
            return None
        match = re.match(r"vector\((\d+)\)", row["typ"] or "")
        return int(match.group(1)) if match else None

    def embedding_dim(self) -> int | None:
        """Live width of `chunks.embedding`, or None before migrations run."""
        return self.vector_width("chunks", "embedding")
