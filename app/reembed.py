"""
Corpus re-embed: resize vector columns to the model's width and re-ingest
every stored file through the normal pipeline.

Single-sourced core shared by two callers:

    scripts/re_embed.py   operator CLI (stop-the-API flow, prints to stdout)
    POST /system/reembed  admin in-app job (server keeps serving reads + the
                          banner while it runs; progress via GET status)

Procedure (destructive to vectors only — users, projects, sources, topics,
chats and memory are untouched):

    1. Probe the model: one /embeddings call, the output width wins.
    2. Drop the HNSW index, delete all chunk rows, null stored stem vectors,
       ALTER both vector columns, recreate the index and `match_chunks`.
    3. Reset ready/failed sources to queued and re-ingest every stored file
       through `IngestionPipeline` (same code as uploads, incl. topics merge).
    4. Record `system_meta` ground truth (model + dim) so drift detection has
       something to compare against afterwards.

Uploads landing mid-job ingest at the NEW width into the resized column, so
they stay consistent (the CLI's "stop the API first" is caution, not a
correctness requirement).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI

    from .config import Settings
    from .db import Database

log = logging.getLogger(__name__)

# Keep in sync with migrations/0001_core.sql — the only other place this
# function is defined. `{dim}` is the single substitution point (fresh
# migrations are additionally templated by migration_runner.render_sql).
MATCH_CHUNKS_DDL = """create or replace function match_chunks (
    query_embedding   vector({dim}),
    match_count       integer,
    filter_project_id uuid,
    filter_source_ids uuid[] default null,
    filter_topic_ids  uuid[] default null
)
returns table (
    id            uuid,
    source_id     uuid,
    topic_id      uuid,
    text          text,
    locator_kind  text,
    locator_value text,
    page          integer,
    start_ts      real,
    end_ts        real,
    heading       text,
    similarity    real
)
language sql stable as $$
    select c.id, c.source_id, c.topic_id, c.text, c.locator_kind, c.locator_value,
           c.page, c.start_ts, c.end_ts, c.heading,
           1 - (c.embedding <=> query_embedding) as similarity
    from chunks c
    where c.project_id = filter_project_id
      and (filter_source_ids is null or c.source_id = any (filter_source_ids))
      and (filter_topic_ids  is null or c.topic_id  = any (filter_topic_ids))
    order by c.embedding <=> query_embedding
    limit match_count;
$$"""

MAX_JOB_ERRORS = 25
_job_lock = threading.Lock()


# ------------------------------------------------------------------ primitives
def probe_model_dim_or_raise(settings: Settings) -> int:
    """Model width wins; RuntimeError (not SystemExit) so the job can report it."""
    from .migration_runner import probe_model_dim

    probed = probe_model_dim(settings)
    if probed is None:
        if settings.embedding_dim is not None:
            log.warning("Reembed    : endpoint unreachable, trusting pinned EMBEDDING_DIM=%d",
                        settings.embedding_dim)
            return int(settings.embedding_dim)
        raise RuntimeError(
            "Could not reach the embedding endpoint "
            f"({settings.resolved_embedding_base_url}, model={settings.embedding_model}). "
            "Start it (or pin EMBEDDING_DIM) before re-embedding — the probe is how "
            "we know the width."
        )
    return probed


def resize_vectors(db: Database, dim: int) -> dict[str, int]:
    """Wipe vectors and resize every vector column, index and retrieval fn.

    Postgres-only (fixed `vector(N)` columns). The SQLite backend has no DDL
    here — call `store.reset_vectors(dim)` instead; `run_reembed_job` and the
    CLI branch on the backend.
    """
    if getattr(db, "backend", "postgres") != "postgres":
        raise RuntimeError("resize_vectors is Postgres-only; use the vector store's reset_vectors.")
    with db.transaction() as conn:
        conn.execute("drop index if exists chunks_embedding_idx")
        deleted = conn.execute("delete from chunks").rowcount
        conn.execute(f"alter table chunks alter column embedding type vector({dim})")
        # Stem vectors are recomputed on next generation; stale cross-model
        # rows would poison novelty cosine, so they go too.
        nulled = conn.execute("update questions set stem_embedding = null").rowcount
        conn.execute(f"alter table questions alter column stem_embedding type vector({dim})")
        conn.execute(
            "create index chunks_embedding_idx on chunks using hnsw (embedding vector_cosine_ops)"
        )
        conn.execute(MATCH_CHUNKS_DDL.format(dim=dim))
    return {"deleted": deleted, "nulled": nulled}


def reset_sources_for_reembed(db: Database) -> int:
    """Ready/failed rows go back to queued so the pipeline picks them up."""
    with db.transaction() as conn:
        n = conn.execute(
            "update sources set status = 'queued', "
            "status_detail = 're-embed queued after embedding-model change', "
            "units = 0, chunk_count = 0, parser = '', warnings = '[]' "
            "where status in ('ready', 'failed')"
        ).rowcount
    return n


def reingest_all(
    settings: Settings,
    db: Database,
    *,
    store: Any = None,
    sources: Any = None,
    topics: Any = None,
    pipeline: Any = None,
    storage: Any = None,
    reuse_parsed: bool = True,
    on_source: Callable[[str, bool, str], None] | None = None,
) -> dict[str, Any]:
    """Run every queued source through the normal ingestion pipeline.

    Pass the server's collaborators (pipeline + storage + repos) for the job;
    omit them and standalone clients are built (CLI). `reuse_parsed` rebuilds
    units from the stored parse (no re-transcription); `fresh_parse` redo's
    everything from the file. Returns
    {"ok", "failed", "errors": [{"filename", "detail"}]}.
    """
    from .ingestion import IngestionPipeline
    from .llm import LLMClient, build_embedder
    from .repo import ProjectRepo, SourceRepo, TopicRepo, UserRepo
    from .repo.profile import ProfileRepo
    from .storage import FileStorage
    from .transcription import build_transcriber
    from .vectorstore import VectorStore

    if pipeline is None:
        from .backend import create_vector_store
        from .ingestion.parsers_media import set_transcriber

        llm = LLMClient(settings)
        embedder = build_embedder(settings)
        store = create_vector_store(settings, db)
        sources = SourceRepo(db)
        topics = TopicRepo(db)
        set_transcriber(build_transcriber(settings))
        storage = FileStorage(settings.uploads_dir)
        # Repos below are unused directly but mirror the lifespan wiring so a
        # future pipeline dependency cannot silently diverge.
        _users, _projects, _profiles = UserRepo(db), ProjectRepo(db), ProfileRepo(db)
        pipeline = IngestionPipeline(settings, llm, embedder, store, sources, topics)
    else:
        if sources is None or storage is None:
            raise ValueError("Pass sources + storage alongside a prebuilt pipeline.")

    ok, failed = 0, 0
    errors: list[dict[str, str]] = []
    for source in sources.claim_pending():
        path = storage.open_for_read(source.project_id, source.id, source.filename)
        if path is None:
            sources.mark_failed(source.id, "Stored file disappeared before re-embed.")
            failed += 1
            errors.append({"filename": source.filename, "detail": "file missing on disk"})
            if on_source:
                on_source(source.filename, False, "file missing on disk")
            continue
        result = pipeline.ingest_source(source, path, reuse_parsed=reuse_parsed)
        if result.ok:
            ok += 1
        else:
            failed += 1
            errors.append({"filename": source.filename, "detail": result.detail})
        if on_source:
            on_source(source.filename, result.ok, result.detail or f"{result.chunks} chunks")
    return {"ok": ok, "failed": failed, "errors": errors[:MAX_JOB_ERRORS]}


# ------------------------------------------------------------------ job state
def fresh_job() -> dict[str, Any]:
    return {
        "state": "idle", "total": 0, "processed": 0, "ok": 0, "failed": 0,
        "started_at": "", "finished_at": "", "message": "", "errors": [],
    }


def job_snapshot(app: FastAPI) -> dict[str, Any]:
    job = getattr(app.state, "reembed_job", None) or fresh_job()
    return dict(job)


def start_reembed_job(app: FastAPI, *, force: bool = False) -> dict[str, Any]:
    """Validate + arm the background job. Raises RuntimeError with the reason
    when it must not start (running / nothing to do) — callers map to 409."""
    from .drift import compute_drift

    with _job_lock:
        job = getattr(app.state, "reembed_job", None) or fresh_job()
        if job.get("state") == "running":
            raise RuntimeError("A re-embed job is already running.")
        drifted = compute_drift(app.state.settings, app.state.db).drift
        if not drifted and not force:
            raise RuntimeError(
                "Embeddings are in sync — nothing to re-embed. "
                "Use force=true for a same-width model swap."
            )
        app.state.reembed_job = {
            **fresh_job(), "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "message": "starting",
        }
        return job_snapshot(app)


def run_reembed_job(app: FastAPI, force: bool = False, fresh_parse: bool = False) -> None:
    """Background entry: full re-embed, then refresh ground truth + drift."""
    from .drift import compute_drift, record_ingest_ground_truth

    settings = app.state.settings
    db = app.state.db
    job = app.state.reembed_job
    def bump(**fields: Any) -> None:
        with _job_lock:
            job.update(fields)

    try:
        bump(message="probing embedding model")
        model_dim = probe_model_dim_or_raise(settings)
        if settings.embedding_dim is None:
            settings.embedding_dim = model_dim
            log.info("Reembed    : auto-detected width %d", model_dim)
        elif settings.embedding_dim != model_dim:
            raise RuntimeError(
                f"EMBEDDING_DIM={settings.embedding_dim} but the model emits "
                f"{model_dim}-d vectors. Fix the pin (or unset for auto-detect) "
                "and retry — the job will not guess."
            )
        bump(message=f"resizing vectors to {model_dim}-d")
        if getattr(db, "backend", "postgres") == "sqlite":
            counts = app.state.store.reset_vectors(model_dim)
            log.info("Reembed    : cleared Chroma collection + %d chunk rows", counts["deleted"])
        else:
            counts = resize_vectors(db, model_dim)
            log.info("Reembed    : deleted %d chunks, nulled %d stems; columns now vector(%d)",
                     counts["deleted"], counts["nulled"], model_dim)
        n = reset_sources_for_reembed(db)
        bump(total=n, message=f"re-ingesting {n} source(s)")

        def on_source(filename: str, ok: bool, detail: str) -> None:
            with _job_lock:
                job["processed"] += 1
                if ok:
                    job["ok"] += 1
                else:
                    job["failed"] += 1
                    if len(job["errors"]) < MAX_JOB_ERRORS:
                        job["errors"].append({"filename": filename, "detail": detail})
                job["message"] = f"{job['processed']}/{job['total']}: {filename}"

        result = reingest_all(
            settings, db,
            store=app.state.store, sources=app.state.sources,
            topics=app.state.topics, pipeline=app.state.pipeline,
            storage=app.state.storage, reuse_parsed=not fresh_parse,
            on_source=on_source,
        )
        record_ingest_ground_truth(db, settings.embedding_model, model_dim)
        bump(message=f"done: {result['ok']} re-embedded, {result['failed']} failed",
             state="done" if not result["failed"] else "done_with_failures",
             finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001 — the job must always land in a state
        log.exception("Re-embed job failed")
        bump(state="failed", message=str(exc) or "re-embed failed",
             finished_at=datetime.now(timezone.utc).isoformat())
    finally:
        try:
            app.state.embedding_drift = compute_drift(settings, db)
        except Exception:
            log.exception("Could not refresh drift state after re-embed")


__all__ = [
    "MATCH_CHUNKS_DDL", "MAX_JOB_ERRORS",
    "probe_model_dim_or_raise", "resize_vectors", "reset_sources_for_reembed",
    "reingest_all", "fresh_job", "job_snapshot",
    "start_reembed_job", "run_reembed_job",
]
