"""Shared migration validation and application for the CLI and API startup."""

from __future__ import annotations

import re
from pathlib import Path

import psycopg

from .config import Settings

VERSION_RE = re.compile(r"^(\d{4})_[A-Za-z0-9_]+\.sql$")
# Matches `vector(1536)` and friends so we can template fresh migrations to the
# resolved width. Live columns always win over SQL text once they exist.
VECTOR_WIDTH_RE = re.compile(r"vector\(\s*(\d+)\s*\)", re.IGNORECASE)


def render_migration_sql(sql: str, dim: int) -> str:
    """Template every `vector(N)` in a migration file to the resolved width.

    Fresh databases never mismatch: whatever the checked-in SQL says (768),
    what executes is `vector(<resolved>)`. Existing databases skip this path
    entirely — their live columns are compared, never rewritten.
    """
    return VECTOR_WIDTH_RE.sub(f"vector({dim})", sql)


def probe_model_dim(settings: Settings) -> int | None:
    """One /embeddings call; the model's actual output width wins over config.

    Returns None when the endpoint is unreachable (offline boot, local server
    still downloading) so callers can fall back to the live column instead of
    failing here. Callers that cannot fall back raise with the fix.
    """
    try:
        from .llm import build_embedder

        vectors = build_embedder(settings).embed(["embedding width probe"])
    except Exception:
        return None
    widths = {len(v) for v in vectors}
    if not vectors or len(widths) != 1:
        return None
    return widths.pop()


def resolve_embedding_dim(
    settings: Settings,
    conn: psycopg.Connection | None = None,
    files: list[tuple[str, Path]] | None = None,
) -> int:
    """Precedence: explicit EMBEDDING_DIM > live probe > live column.

    Explicit pin (website operator) always wins. Otherwise the model's actual
    output is authoritative; the live column is only a fallback when the
    endpoint is unreachable, and the SQL text is never authoritative (it gets
    templated). Raises SystemExit only when nothing can determine a width
    (fresh DB + unreachable endpoint + no pin).
    """
    if settings.embedding_dim is not None:
        return int(settings.embedding_dim)
    probed = probe_model_dim(settings)
    if probed is not None:
        return probed
    if conn is not None:
        try:
            live = live_vector_widths(conn, getattr(settings, "db_backend", "postgres"))
        except Exception:
            live = {}
        present = {v for v in live.values() if v is not None}
        if len(present) == 1:
            return present.pop()
    raise SystemExit(
        "Could not determine the embedding width: EMBEDDING_DIM is unset and the "
        f"embedding endpoint ({settings.resolved_embedding_base_url}, "
        f"model={settings.embedding_model}) is unreachable.\n"
        "Fix: start the endpoint, or pin EMBEDDING_DIM to your model's width in .env."
    )

DROP_ORDER = [
    "answers", "questions", "assessments", "quizzes",
    "chat_messages", "chats",
    "project_memory", "user_profiles",
    "source_contents",
    "chunks", "topic_sources", "topic_edges", "topics", "sources", "projects", "users",
    "app_settings", "system_meta",
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


def _ensure_bookkeeping(conn, backend: str = "postgres") -> None:
    if backend == "sqlite":
        conn.execute(
            """
            create table if not exists schema_migrations (
                version    text primary key,
                filename   text not null,
                applied_at text not null default (datetime('now'))
            )
            """
        )
        return
    conn.execute(
        """
        create table if not exists schema_migrations (
            version    text primary key,
            filename   text not null,
            applied_at timestamptz not null default now()
        )
        """
    )


def live_embedding_width(conn, backend: str = "postgres") -> int | None:
    """Width of the live `chunks.embedding` column, or None before it exists."""
    if backend == "sqlite":
        return None  # Chroma is dim-flexible; ground truth lives in system_meta
    row = conn.execute(
        "select format_type(a.atttypid, a.atttypmod) "
        "from pg_attribute a join pg_class c on c.oid = a.attrelid "
        "where c.relname = 'chunks' and a.attname = 'embedding' and not a.attisdropped"
    ).fetchone()
    if not row:
        return None
    match = re.match(r"vector\((\d+)\)", row[0] or "")
    return int(match.group(1)) if match else None


def live_vector_widths(conn, backend: str = "postgres") -> dict[str, int | None]:
    """Every tracked vector column: chunks.embedding + questions.stem_embedding."""
    if backend == "sqlite":
        # No fixed vector columns; width drift is tracked via system_meta
        # (see app/drift.py). Returning Nones skips the live-column check.
        return {"chunks.embedding": None, "questions.stem_embedding": None}
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


def check_embedding_dim(
    conn,
    dim: int,
    files: list[tuple[str, Path]],
    *,
    validate_migration_sql: bool = True,
    backend: str = "postgres",
) -> int:
    """
    The resolved width must agree with the vectors Postgres actually holds.

    `dim` is already resolved (explicit pin > probe > live column — see
    `resolve_embedding_dim`). Live columns win when present; fresh databases
    skip comparison because pending SQL is templated to `dim` at apply time
    (`render_migration_sql`), so checked-in `vector(768)` never mismatches.
    On SQLite there are no fixed columns (Chroma is dim-flexible; width drift
    is tracked via `system_meta` in app/drift.py), so this is a no-op that
    returns `dim` for call chaining.
    """
    live = live_vector_widths(conn, backend)
    present = {k: v for k, v in live.items() if v is not None}
    if present:
        bad = {k: v for k, v in present.items() if v != dim}
        if bad:
            detail = ", ".join(f"{k} is vector({v})" for k, v in bad.items())
            raise SystemExit(
                f"Embedding dimension mismatch: {detail} but "
                f"resolved width={dim}.\n"
                f"The embedding model was swapped without re-embedding. Fix:\n"
                f"  1. Set EMBEDDING_DIM to your model's output width (or unset for auto-detect).\n"
                f"  2. Run:  uv run python scripts/re_embed.py   (use --check to inspect first)"
            )
        print(f"embedding dim : {dim} (matches {', '.join(present)})")
        return dim
    if backend == "sqlite":
        print(f"embedding dim : {dim} (dim-flexible Chroma; no column to match)")
        return dim
    print(f"embedding dim : {dim} (templating fresh migrations to vector({dim}))")
    return dim


def applied_versions(conn) -> set[str]:
    rows = conn.execute("select version from schema_migrations").fetchall()
    return {r["version"] if isinstance(r, dict) else r[0] for r in rows}


def reset(conn, backend: str = "postgres") -> None:
    """CLI-only destructive reset; startup migration code never calls this."""
    print("reset         : dropping app tables")
    if backend == "postgres":
        conn.execute("drop function if exists match_chunks(vector, integer, uuid, uuid[], uuid[])")
    for table in DROP_ORDER:
        conn.execute(f"drop table if exists {table} cascade" if backend == "postgres"
                     else f"drop table if exists {table}")
    conn.execute("delete from schema_migrations")
    print(f"reset         : dropped {len(DROP_ORDER)} tables")


def apply_pending_migrations(settings: Settings) -> list[str]:
    """Apply all pending migrations with per-file transactions; never reset."""
    backend = getattr(settings, "db_backend", "postgres")
    if backend == "sqlite":
        return _apply_sqlite(settings)
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
            _ensure_bookkeeping(conn, backend)
        done = applied_versions(conn)
        pending = [(version, path) for version, path in files if version not in done]
        if not pending:
            return applied_now

        # Resolve once: explicit pin > probe > live column. Fresh SQL is then
        # templated to that width, so checked-in vector(768) never mismatches.
        resolved = resolve_embedding_dim(settings, conn)
        check_embedding_dim(
            conn,
            resolved,
            pending,
            validate_migration_sql=not done,
            backend=backend,
        )
        # Pin the singleton for the rest of this process (boot drift check,
        # ingestion width guards) so downstream code keeps reading one field.
        settings.embedding_dim = resolved
        for version, path in pending:
            sql = render_migration_sql(path.read_text(encoding="utf-8"), resolved)
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "insert into schema_migrations (version, filename) values (%s, %s)",
                    (version, path.name),
                )
            applied_now.append(path.name)
    return applied_now


def _split_sqlite_statements(sql: str) -> list[str]:
    """Split one migration file into statements.

    Migration files are controlled DDL (no triggers/functions, no semicolons
    inside strings or comments), so comment-stripping + semicolon split is
    exact here — and keeps each file atomic inside our transaction, unlike
    `executescript` (which commits around itself).
    """
    lines = [ln for ln in sql.splitlines()
             if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _apply_sqlite(settings: Settings) -> list[str]:
    """SQLite twin of the Postgres path above (no extension, no vector DDL).

    Width resolution is deliberately NOT done here: SQLite DDL needs no width,
    and the probe needs the embedding endpoint — which on a fresh desktop boot
    only exists after the vendored server initializes in the lifespan. The
    lifespan pins `settings.embedding_dim` after building services; the CLI
    (`scripts/migrate.py`) resolves on its own.
    """
    from .sqlite_db import SqliteDatabase

    files = _migration_files(settings.sqlite_migrations_dir)
    applied_now: list[str] = []
    db = SqliteDatabase(settings)
    try:
        with db.transaction() as conn:
            _ensure_bookkeeping(conn, "sqlite")
        done = applied_versions(db._conn)
        pending = [(version, path) for version, path in files if version not in done]
        if not pending:
            return applied_now
        for version, path in pending:
            statements = _split_sqlite_statements(path.read_text(encoding="utf-8"))
            with db.transaction() as conn:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "insert into schema_migrations (version, filename) values (%s, %s)",
                    (version, path.name),
                )
            applied_now.append(path.name)
    finally:
        db.close()
    return applied_now

