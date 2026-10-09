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
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.migration_runner import (
    DROP_ORDER,
    _ensure_bookkeeping,
    _migration_files,
    applied_versions,
    apply_pending_migrations,
    check_embedding_dim,
    reset,
)

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




