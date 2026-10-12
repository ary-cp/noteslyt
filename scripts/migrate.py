"""
Ordered SQL migration runner (both backends).

    uv run python scripts/migrate.py            # apply everything pending
    uv run python scripts/migrate.py --status   # list applied / pending, change nothing
    uv run python scripts/migrate.py --reset    # DROP every app table, then re-apply

Postgres migrations live in `migrations/`, SQLite ports in
`migrations/sqlite/`; the backend follows `DATABASE_URL` (unset → SQLite).

Files are applied in filename order inside a single transaction per file, and each
applied version is recorded in `schema_migrations` so re-running is a no-op.

Safety rails:
    * The embedding width is resolved once (explicit EMBEDDING_DIM > probe >
      live column); fresh Postgres SQL is templated to it, live columns are
      compared. SQLite has no fixed columns (Chroma is dim-flexible).
    * Each migration runs in its own transaction, so a failure leaves earlier
      migrations applied and the failing one cleanly rolled back.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.migration_runner import (
    _ensure_bookkeeping,
    _migration_files,
    _split_sqlite_statements,
    applied_versions,
    check_embedding_dim,
    render_migration_sql,
    reset,
    resolve_embedding_dim,
)

def main() -> int:
    parser = argparse.ArgumentParser(description="Apply NotesLyt SQL migrations.")
    parser.add_argument("--status", action="store_true", help="report state and exit")
    parser.add_argument("--reset", action="store_true", help="drop all app tables first")
    args = parser.parse_args()

    settings = get_settings()
    backend = settings.db_backend
    migrations_dir = (settings.sqlite_migrations_dir if backend == "sqlite"
                      else settings.migrations_dir)

    files = _migration_files(migrations_dir)
    print(f"target        : {settings.db_target} [{backend}]")

    if backend == "sqlite":
        return _run_sqlite(settings, files, args)
    return _run_postgres(settings, files, args)


def _run_postgres(settings, files, args) -> int:
    import psycopg  # noqa: E402 — postgres-only driver

    if not settings.database_url:
        raise SystemExit(
            "DATABASE_URL is not set for a Postgres target.\n"
            "Add it to .env, e.g.\n"
            "  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/StudyCompanion"
        )
    with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout_s) as conn:
        conn.execute("create extension if not exists vector")
        if args.reset:
            with conn.transaction():
                _ensure_bookkeeping(conn, "postgres")
                reset(conn, "postgres")

        with conn.transaction():
            _ensure_bookkeeping(conn, "postgres")
        done = applied_versions(conn)

        if args.status:
            for version, path in files:
                mark = "applied" if version in done else "pending"
                print(f"  {mark:8} {path.name}")
            return 0

        resolved = resolve_embedding_dim(settings, conn)
        check_embedding_dim(conn, resolved, files, backend="postgres")

        pending = [(v, p) for v, p in files if v not in done]
        if not pending:
            print(f"migrations    : up to date ({len(done)} applied)")
            return 0

        settings.embedding_dim = resolved
        for version, path in pending:
            sql = render_migration_sql(path.read_text(encoding="utf-8"), resolved)
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "insert into schema_migrations (version, filename) values (%s, %s)",
                    (version, path.name),
                )
            print(f"applied       : {path.name}")

        print(f"migrations    : {len(pending)} applied, {len(done)} already present")
    return 0


def _run_sqlite(settings, files, args) -> int:
    from app.sqlite_db import SqliteDatabase  # noqa: E402 — sqlite-only driver

    db = SqliteDatabase(settings)
    try:
        if args.reset:
            with db.transaction() as conn:
                _ensure_bookkeeping(conn, "sqlite")
                reset(conn, "sqlite")

        with db.transaction() as conn:
            _ensure_bookkeeping(conn, "sqlite")
        done = applied_versions(db._conn)

        if args.status:
            for version, path in files:
                mark = "applied" if version in done else "pending"
                print(f"  {mark:8} {path.name}")
            return 0

        resolved = resolve_embedding_dim(settings, db._conn)
        check_embedding_dim(db._conn, resolved, files, backend="sqlite")

        pending = [(v, p) for v, p in files if v not in done]
        if not pending:
            print(f"migrations    : up to date ({len(done)} applied)")
            return 0

        settings.embedding_dim = resolved
        for version, path in pending:
            statements = _split_sqlite_statements(path.read_text(encoding="utf-8"))
            with db.transaction() as conn:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "insert into schema_migrations (version, filename) values (%s, %s)",
                    (version, path.name),
                )
            print(f"applied       : {path.name}")

        print(f"migrations    : {len(pending)} applied, {len(done)} already present")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())





