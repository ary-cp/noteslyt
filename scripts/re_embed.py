"""
Re-embed the corpus after changing embedding models.

    uv run python scripts/re_embed.py --check   # report model/config/DB widths, change nothing
    uv run python scripts/re_embed.py           # resize + wipe vectors + re-ingest everything
    uv run python scripts/re_embed.py --force   # re-ingest even when widths agree
                                                # (same-width model swap)

Why this exists: vectors from different models are incomparable, so swapping
e.g. OpenAI `text-embedding-3-small` (1536-d) for nomic/EmbeddingGemma (768-d)
without re-embedding fails every ingest at the Postgres insert
("expected 1536 dimensions, not 768") — and padding/truncating across would
silently corrupt retrieval. The boot guard (`app/main.py`) refuses to start
in drift and points here.

Procedure (destructive to vectors only — users, projects, sources, topics,
chats and memory are untouched):

    1. Probe the model: one /embeddings call, the output width wins.
    2. Require EMBEDDING_DIM to equal it (the script never guesses).
    3. Drop the HNSW index, delete all chunk rows, null stored stem vectors,
       ALTER both vector columns, recreate the index and `match_chunks`.
    4. Reset ready/failed sources to queued and re-ingest every stored file
       through the normal pipeline (same code as uploads, incl. topics merge).

Stop the API server first: it holds no locks the DDL needs, but an upload
landing mid-run would ingest at the new width into a half-migrated table.
LLM + transcription endpoints must be reachable (topics + media re-ingest).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings  # noqa: E402
from app.db import Database, describe_embedding_drift  # noqa: E402
from app.ingestion import IngestionPipeline  # noqa: E402
from app.ingestion.parsers_media import set_transcriber  # noqa: E402
from app.llm import LLMClient, build_embedder  # noqa: E402
from app.repo import ProjectRepo, SourceRepo, TopicRepo, UserRepo  # noqa: E402
from app.repo.profile import ProfileRepo  # noqa: E402
from app.storage import FileStorage  # noqa: E402
from app.transcription import build_transcriber  # noqa: E402
from app.vectorstore import VectorStore  # noqa: E402

# Keep in sync with migrations/0001_core.sql — the only other place this
# function is defined. `{dim}` is the single substitution point.
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


def probe_model_dim(settings: Settings) -> int:
    """One /embeddings call — the model's actual output width wins over config."""
    try:
        vectors = build_embedder(settings).embed(["re-embed width probe"])
    except Exception as exc:
        raise SystemExit(
            f"Could not reach the embedding endpoint "
            f"({settings.resolved_embedding_base_url}, model={settings.embedding_model}): {exc}\n"
            f"Fix the EMBEDDING_* settings first — the probe is how we know the width."
        ) from exc
    widths = {len(v) for v in vectors}
    if not vectors or len(widths) != 1:
        raise SystemExit(f"Embedder returned inconsistent widths: {sorted(widths)}")
    return widths.pop()


def collect_dims(settings: Settings, db: Database, model_dim: int | None) -> dict[str, int | None]:
    return {
        "model": model_dim,
        "EMBEDDING_DIM": settings.embedding_dim,
        "chunks.embedding": db.embedding_dim(),
        "questions.stem_embedding": db.vector_width("questions", "stem_embedding"),
    }


def report(dims: dict[str, int | None]) -> list[str]:
    for name, width in dims.items():
        print(f"  {name:18} {width if width is not None else '(absent)'}")
    return describe_embedding_drift(dims)


def resize(db: Database, dim: int) -> None:
    """Wipe vectors and resize every vector column, index and the retrieval function."""
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
    print(f"resized         : deleted {deleted} chunk rows, nulled {nulled} stem vectors, "
          f"columns now vector({dim})")


def reset_sources(db: Database) -> int:
    """Ready/failed rows go back to queued so the pipeline picks them up."""
    with db.transaction() as conn:
        n = conn.execute(
            "update sources set status = 'queued', "
            "status_detail = 're-embed queued after embedding-model change', "
            "units = 0, chunk_count = 0, parser = '', warnings = '[]' "
            "where status in ('ready', 'failed')"
        ).rowcount
    print(f"re-queued       : {n} source(s)")
    return n


def reingest(settings: Settings, db: Database) -> tuple[int, int]:
    """Run every queued source through the normal ingestion pipeline."""
    llm = LLMClient(settings)
    embedder = build_embedder(settings)
    store = VectorStore(db)
    sources = SourceRepo(db)
    topics = TopicRepo(db)
    set_transcriber(build_transcriber(settings))
    storage = FileStorage(settings.uploads_dir)
    # Repos below are unused directly but mirror the lifespan wiring so a
    # future pipeline dependency cannot silently diverge.
    _users, _projects, _profiles = UserRepo(db), ProjectRepo(db), ProfileRepo(db)
    pipeline = IngestionPipeline(settings, llm, embedder, store, sources, topics)

    ok, failed = 0, 0
    for source in sources.claim_pending():
        path = storage.open_for_read(source.project_id, source.id, source.filename)
        if path is None:
            sources.mark_failed(source.id, "Stored file disappeared before re-embed.")
            print(f"  [FAIL] {source.filename} — file missing on disk")
            failed += 1
            continue
        result = pipeline.ingest_source(source, path)
        if result.ok:
            print(f"  [ok] {source.filename} — {result.chunks} chunks via {result.parser}")
            ok += 1
        else:
            print(f"  [FAIL] {source.filename} — {result.detail}")
            failed += 1
    return ok, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-embed the corpus after a model change.")
    parser.add_argument("--check", action="store_true", help="report widths, change nothing")
    parser.add_argument("--force", action="store_true", help="re-ingest even when widths agree")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")
    print(f"target          : {settings.db_target}")

    model_dim = probe_model_dim(settings)
    if settings.embedding_dim != model_dim:
        raise SystemExit(
            f"EMBEDDING_DIM={settings.embedding_dim} but the model emits {model_dim}-d vectors.\n"
            f"Set EMBEDDING_DIM={model_dim} in .env first — the script will not guess, "
            f"because every other width check keys off it."
        )

    db = Database(settings)
    try:
        dims = collect_dims(settings, db, model_dim)
        print("widths:")
        drift = report(dims)
        if args.check:
            return 1 if drift else 0

        if drift:
            print("drift           : " + " ".join(drift))
        elif not args.force:
            print("in sync         : every width agrees, nothing to do (use --force to re-ingest anyway)")
            return 0
        else:
            print("force           : widths agree, re-ingesting anyway")

        resize(db, model_dim)
        reset_sources(db)
        ok, failed = reingest(settings, db)

        print("widths after:")
        post = report(collect_dims(settings, db, model_dim))
        print(f"done            : {ok} re-embedded, {failed} failed")
        if post or failed:
            print("NOT CLEAN — see failures above")
            return 1
        print("RE-EMBED COMPLETE")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
