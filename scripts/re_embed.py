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
silently corrupt retrieval. The server degrades vector routes to 409 in drift
and points here (or POST /system/reembed for the in-app job).

Procedure (destructive to vectors only — users, projects, sources, topics,
chats and memory are untouched):

    1. Probe the model: one /embeddings call, the output width wins.
    2. Require EMBEDDING_DIM to equal it (the script never guesses; unset
       means auto-detect and the probe wins).
    3. Drop the HNSW index, delete all chunk rows, null stored stem vectors,
       ALTER both vector columns, recreate the index and `match_chunks`.
    4. Reset ready/failed sources to queued and re-ingest every stored file
       through the normal pipeline (same code as uploads, incl. topics merge).
       Stored parses/transcripts are reused (no re-transcription bills);
       pass --fresh-parse to redo them from the files (STT/parser upgrades).

Stop the API server first: uploads landing mid-run ingest at the new width
(consistent), but an upload racing the DDL window can still fail its batch.
LLM + transcription endpoints must be reachable (topics + media re-ingest).

The resize/re-ingest core lives in app/reembed.py (shared with the in-app
job); this CLI is prints + arg parsing around it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings  # noqa: E402
from app.db import describe_embedding_drift  # noqa: E402
from app.llm import build_embedder  # noqa: E402
from app.reembed import (  # noqa: E402
    reingest_all,
    reset_sources_for_reembed,
    resize_vectors,
)


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
    """CLI wrapper: shared resize core + prints (see app/reembed.py)."""
    counts = resize_vectors(db, dim)
    print(f"resized         : deleted {counts['deleted']} chunk rows, nulled {counts['nulled']} "
          f"stem vectors, columns now vector({dim})")


def reset_sources(db: Database) -> int:
    """Ready/failed rows go back to queued so the pipeline picks them up."""
    n = reset_sources_for_reembed(db)
    print(f"re-queued       : {n} source(s)")
    return n


def reingest(settings: Settings, db: Database, *, reuse_parsed: bool = True) -> tuple[int, int]:
    """Run every queued source through the normal ingestion pipeline."""
    def on_source(filename: str, ok: bool, detail: str) -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {filename} — {detail}")

    result = reingest_all(settings, db, reuse_parsed=reuse_parsed, on_source=on_source)
    return result["ok"], result["failed"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-embed the corpus after a model change.")
    parser.add_argument("--check", action="store_true", help="report widths, change nothing")
    parser.add_argument("--force", action="store_true", help="re-ingest even when widths agree")
    parser.add_argument("--fresh-parse", action="store_true",
                        help="re-parse/transcribe from the files instead of reusing stored parses")
    args = parser.parse_args()

    settings = get_settings()
    print(f"target          : {settings.db_target}")

    model_dim = probe_model_dim(settings)
    if settings.embedding_dim is None:
        settings.embedding_dim = model_dim
        print(f"auto-dim        : EMBEDDING_DIM unset, using probed width {model_dim}")
    elif settings.embedding_dim != model_dim:
        raise SystemExit(
            f"EMBEDDING_DIM={settings.embedding_dim} but the model emits {model_dim}-d vectors.\n"
            f"Set EMBEDDING_DIM={model_dim} in .env (or unset for auto-detect) — the script "
            f"will not guess, because every other width check keys off it."
        )

    from app.backend import create_database, create_vector_store  # noqa: E402

    db = create_database(settings)
    store = create_vector_store(settings, db)
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

        if getattr(db, "backend", "postgres") == "sqlite":
            counts = store.reset_vectors(model_dim)
            print(f"resized         : cleared Chroma collection + {counts['deleted']} chunk rows")
        else:
            resize(db, model_dim)
        reset_sources(db)
        ok, failed = reingest(settings, db, reuse_parsed=not args.fresh_parse)

        from app.drift import record_ingest_ground_truth  # noqa: E402

        record_ingest_ground_truth(db, settings.embedding_model, model_dim)
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
