"""
Ordered SQL migration runner.

    uv run python scripts/migrate.py            # apply everything pending
    uv run python scripts/migrate.py --status   # list applied / pending, change nothing
    uv run python scripts/migrate.py --reset    # DROP every app table, then re-apply

Why not Alembic: this repo hand-writes plain Postgres SQL (see `migrations/`).
A migration framework would add a dependency and a code-generation step without
changing what we execute.

Files are applied in filename order inside a single transaction per file, and each
applied version is recorded in `schema_migrations` so re-running is a no-op.

Safety rails:
    * Refuses to run if `vector(...)` width in the SQL disagrees with EMBEDDING_DIM —
      the mismatch otherwise surfaces much later as a confusing insert error.
    * Each migration runs in its own transaction, so a failure leaves earlier
      migrations applied and the failing one cleanly rolled back.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

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
    print("reset         : dropping app tables")
    conn.execute("drop function if exists match_chunks(vector, integer, uuid, uuid[], uuid[])")
    for table in DROP_ORDER:
        conn.execute(f"drop table if exists {table} cascade")
    conn.execute("delete from schema_migrations")
    print(f"reset         : dropped {len(DROP_ORDER)} tables")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Study Companion SQL migrations.")
    parser.add_argument("--status", action="store_true", help="report state and exit")
    parser.add_argument("--reset", action="store_true", help="drop all app tables first")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit(
            "DATABASE_URL is not set.\n"
            "Add it to .env, e.g.\n"
            "  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/StudyCompanion"
        )

    files = _migration_files(settings.migrations_dir)
    print(f"target        : {settings.db_target}")

    with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout_s) as conn:
        conn.execute("create extension if not exists vector")
        if args.reset:
            with conn.transaction():
                _ensure_bookkeeping(conn)
                reset(conn)

        with conn.transaction():
            _ensure_bookkeeping(conn)
        done = applied_versions(conn)

        if args.status:
            for version, path in files:
                mark = "applied" if version in done else "pending"
                print(f"  {mark:8} {path.name}")
            return 0

        check_embedding_dim(conn, settings.embedding_dim, files)

        pending = [(v, p) for v, p in files if v not in done]
        if not pending:
            print(f"migrations    : up to date ({len(done)} applied)")
            return 0

        for version, path in pending:
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "insert into schema_migrations (version, filename) values (%s, %s)",
                    (version, path.name),
                )
            print(f"applied       : {path.name}")

        print(f"migrations    : {len(pending)} applied, {len(done)} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())