"""Shared migration validation and application for the CLI and API startup."""

from __future__ import annotations

import re
from pathlib import Path

import psycopg

from .config import Settings

VERSION_RE = re.compile(r"^(\d{4})_[A-Za-z0-9_]+\.sql$")
# Matches `vector(1536)` and friends so we can compare against EMBEDDING_DIM.
VECTOR_WIDTH_RE = re.compile(r"vector\(\s*(\d+)\s*\)", re.IGNORECASE)

DROP_ORDER = [
    "answers", "questions", "assessments", "quizzes",
    "chat_messages", "chats",
    "project_memory", "user_profiles",
    "source_contents",
    "chunks", "topic_sources", "topic_edges", "topics", "sources", "projects", "users",
]


def _migration_files(directory: Path) -> list[tuple[str, Path]]:
    if not directory.is_dir():
        raise SystemExit(f"Migrations directory not found: {directory}")
    files = []
    for path in sorted(directory.glob("*.sql")):
        match = VERSION_RE.match(path.name)
        if not match:
            raise SystemExit(
                f"{path.name} does not match NNNN_name.sql — migrations are ordered by filename"
            )
        files.append((match.group(1), path))
    if not files:
        raise SystemExit(f"No migrations found in {directory}")
    return files


def _ensure_bookkeeping(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        create table if not exists schema_migrations (
            version    text primary key,
            filename   text not null,
            applied_at timestamptz not null default now()
        )
        """
    )


def live_embedding_width(conn: psycopg.Connection) -> int | None:
    """Width of the live `chunks.embedding` column, or None before it exists."""
    row = conn.execute(
        "select format_type(a.atttypid, a.atttypmod) "
        "from pg_attribute a join pg_class c on c.oid = a.attrelid "
        "where c.relname = 'chunks' and a.attname = 'embedding' and not a.attisdropped"
    ).fetchone()
    if not row:
        return None
    match = re.match(r"vector\((\d+)\)", row[0] or "")
    return int(match.group(1)) if match else None


def live_vector_widths(conn: psycopg.Connection) -> dict[str, int | None]:
    """Every tracked vector column: chunks.embedding + questions.stem_embedding."""
    out: dict[str, int | None] = {}
    for table, column in (("chunks", "embedding"), ("questions", "stem_embedding")):
        row = conn.execute(
            "select format_type(a.atttypid, a.atttypmod) "
            "from pg_attribute a join pg_class c on c.oid = a.attrelid "
            "where c.relname = %s and a.attname = %s and not a.attisdropped",
            (table, column),
        ).fetchone()
        if row is None:
            out[f"{table}.{column}"] = None
            continue
        match = re.match(r"vector\((\d+)\)", row[0] or "")
        out[f"{table}.{column}"] = int(match.group(1)) if match else None
    return out


def check_embedding_dim(conn: psycopg.Connection, dim: int, files: list[tuple[str, Path]]) -> None:
    """
    EMBEDDING_DIM must agree with the vectors Postgres actually holds.

    When `chunks` exists (a previous `re_embed.py` may have resized it away
    from what the migration SQL says), the live column wins. On a fresh
    database the SQL text is all there is, so it is checked instead.
    """
    live = live_vector_widths(conn)
    present = {k: v for k, v in live.items() if v is not None}
    if present:
        bad = {k: v for k, v in present.items() if v != dim}
        if bad:
            detail = ", ".join(f"{k} is vector({v})" for k, v in bad.items())
            raise SystemExit(
                f"Embedding dimension mismatch: {detail} but "
                f"EMBEDDING_DIM={dim}.\n"
                f"The embedding model was swapped without re-embedding. Fix:\n"
                f"  1. Set EMBEDDING_DIM to your model's output width.\n"
                f"  2. Run:  uv run python scripts/re_embed.py   (use --check to inspect first)"
            )
        print(f"embedding dim : {dim} (matches {', '.join(present)})")
        return
    found: set[int] = set()
    for _, path in files:
        found.update(int(w) for w in VECTOR_WIDTH_RE.findall(path.read_text(encoding="utf-8")))
    if not found:
        return
    if found != {dim}:
        raise SystemExit(
            f"Embedding dimension mismatch: migrations declare vector({sorted(found)}) but "
            f"EMBEDDING_DIM={dim}.\n"
            f"Fix one of them before migrating — otherwise embedding inserts fail later "
            f"with an opaque dimensionality error."
        )
    print(f"embedding dim : {dim} (matches vector({dim}) in migration SQL)")


def applied_versions(conn: psycopg.Connection) -> set[str]:
    return {r[0] for r in conn.execute("select version from schema_migrations").fetchall()}


def reset(conn: psycopg.Connection) -> None:
    """CLI-only destructive reset; startup migration code never calls this."""
    print("reset         : dropping app tables")
    conn.execute("drop function if exists match_chunks(vector, integer, uuid, uuid[], uuid[])")
    for table in DROP_ORDER:
        conn.execute(f"drop table if exists {table} cascade")
    conn.execute("delete from schema_migrations")
    print(f"reset         : dropped {len(DROP_ORDER)} tables")


def apply_pending_migrations(settings: Settings) -> list[str]:
    """Apply all pending migrations with per-file transactions; never reset."""
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set; cannot apply startup migrations")

    files = _migration_files(settings.migrations_dir)
    applied_now: list[str] = []
    with psycopg.connect(
        settings.database_url,
        connect_timeout=settings.db_connect_timeout_s,
    ) as conn:
        conn.execute("create extension if not exists vector")
        with conn.transaction():
            _ensure_bookkeeping(conn)
        done = applied_versions(conn)
        pending = [(version, path) for version, path in files if version not in done]
        if not pending:
            return applied_now

        check_embedding_dim(conn, settings.embedding_dim, files)
        for version, path in pending:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "insert into schema_migrations (version, filename) values (%s, %s)",
                    (version, path.name),
                )
            applied_now.append(path.name)
    return applied_now

