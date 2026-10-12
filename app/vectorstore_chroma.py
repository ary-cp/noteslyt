"""
Vector store for the SQLite backend: ChromaDB (persistent, local dir).

Same interface as `app/vectorstore.py:VectorStore` (`upsert`, `delete_source`,
`delete_project`, `count`, `query`, `get_by_topic`) plus `reset_vectors`, so
ingestion, chat, quiz and the re-embed job take either store without
branching. Two deliberate splits:

* **Relational rows stay in SQLite.** The `chunks` table (minus the `embedding`
  column) keeps text + metadata for topic grounding (`get_by_topic` callers
  read SQL-free? no — they read this SQLite table via the store), counts and
  cascades. Similarity lives in Chroma (`documents` + caller-supplied
  embeddings, cosine space).
* **Project scoping is mandatory here too.** Every Chroma metadata carries
  `project_id` and every query filters on it — the same tenancy guarantee as
  `match_chunks`, audited by `scripts/verify_sqlite.py`.

Width discipline: Chroma collections reject mixed dims, and the collection
metadata records the ingest width. A model swap surfaces as a readable insert
error (plus the `system_meta` drift flow), never silent corruption. Uploads
landing mid-re-embed ingest at the new width into the recreated collection.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .config import Settings
from .vectorstore import RetrievedChunk

log = logging.getLogger(__name__)

COLLECTION = "chunks"


def _distance(value: Any) -> float:
    """Cosine distance, NaN collapsed to 1.0 (mirrors vectorstore._distance)."""
    try:
        distance = float(value)
    except (TypeError, ValueError):
        return 1.0
    return distance if math.isfinite(distance) else 1.0


class ChromaVectorStore:
    backend = "sqlite"

    def __init__(self, db: Any, settings: Settings):
        self._db = db
        self._settings = settings
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError(
                "ChromaDB is required for the SQLite backend. Run `uv sync` "
                "(chromadb is a pinned dependency)."
            ) from exc
        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        self._collection = self._client.get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine"}
        )

    # ------------------------------------------------------------- writing
    def upsert(
        self,
        *,
        project_id: str,
        source_id: str,
        ids: list[str],
        embeddings: list[list[float]],
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> int:
        """Store chunks for one source (SQLite rows + Chroma vectors).

        SQLite first, Chroma second; a Chroma failure compensates by removing
        the just-written SQLite rows so the two homes never disagree silently.
        """
        if not (len(ids) == len(texts) == len(embeddings) == len(metadatas)):
            raise ValueError("ids, texts, embeddings and metadatas must be the same length")
        if not ids:
            return 0
        self._check_width(embeddings)
        rows = [
            (
                cid, project_id, source_id, meta.get("topic_id"),
                meta.get("locator_kind", "page"), str(meta.get("locator_value", "")),
                meta.get("page"), meta.get("start_ts"), meta.get("end_ts"),
                meta.get("heading", "") or "", int(meta.get("chunk_index", 0)), text,
            )
            for cid, text, meta in zip(ids, texts, metadatas, strict=True)
        ]
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    insert into chunks (
                        id, project_id, source_id, topic_id, locator_kind, locator_value,
                        page, start_ts, end_ts, heading, chunk_index, text
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict (id) do update set
                        topic_id = excluded.topic_id,
                        locator_kind = excluded.locator_kind,
                        locator_value = excluded.locator_value,
                        page = excluded.page,
                        start_ts = excluded.start_ts,
                        end_ts = excluded.end_ts,
                        heading = excluded.heading,
                        chunk_index = excluded.chunk_index,
                        text = excluded.text
                    """,
                    rows,
                )
        chroma_metas = [
            {
                "project_id": project_id,
                "source_id": source_id,
                **{k: v for k, v in {
                    "topic_id": meta.get("topic_id"),
                    "locator_kind": meta.get("locator_kind", "page"),
                    "locator_value": str(meta.get("locator_value", "")),
                    "page": meta.get("page"),
                    "start_ts": meta.get("start_ts"),
                    "end_ts": meta.get("end_ts"),
                    "heading": meta.get("heading", "") or "",
                    "chunk_index": int(meta.get("chunk_index", 0)),
                }.items() if v is not None},
            }
            for meta in metadatas
        ]
        try:
            self._collection.upsert(
                ids=list(ids),
                embeddings=[list(map(float, v)) for v in embeddings],
                documents=list(texts),
                metadatas=chroma_metas,
            )
        except Exception:
            log.exception("Chroma upsert failed; rolling back %d SQLite chunk rows", len(ids))
            with self._db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.executemany("delete from chunks where id = ?",
                                    [(cid,) for cid in ids])
            raise
        self._remember_width(embeddings)
        return len(rows)

    def delete_source(self, source_id: str) -> int:
        with self._db.transaction() as conn:
            n = conn.execute("delete from chunks where source_id = %s", (source_id,)).rowcount
        try:
            self._collection.delete(where={"source_id": source_id})
        except Exception:
            log.exception("Chroma delete_source failed for %s", source_id)
            raise
        return n if n is not None and n >= 0 else 0

    def delete_project(self, project_id: str) -> None:
        """Remove a project's vectors (SQLite rows cascade from projects)."""
        try:
            self._collection.delete(where={"project_id": project_id})
        except Exception:
            log.exception("Chroma delete_project failed for %s", project_id)
            raise

    def reset_vectors(self, dim: int) -> dict[str, int]:
        """Wipe vectors for a re-embed: recreate the collection + clear rows."""
        with self._db.transaction() as conn:
            deleted = conn.execute("delete from chunks").rowcount
            conn.execute("update questions set stem_embedding = null")
        try:
            self._client.delete_collection(COLLECTION)
        except Exception:
            log.warning("Chroma collection %s did not exist at reset", COLLECTION)
        self._collection = self._client.get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine", "embedding_dim": str(dim)}
        )
        return {"deleted": deleted if deleted and deleted >= 0 else 0, "nulled": 0}

    def count(self, project_id: str | None = None) -> int:
        sql = "select count(*) as n from chunks"
        params: tuple = ()
        if project_id is not None:
            sql += " where project_id = %s"
            params = (project_id,)
        with self._db.connection() as conn:
            return int(conn.execute(sql, params).fetchone()["n"])

    # ------------------------------------------------------------ retrieval
    def query(
        self,
        embedding: list[float],
        *,
        project_id: str,
        k: int = 6,
        source_ids: list[str] | None = None,
        topic_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Nearest chunks within ONE project (project filter is mandatory)."""
        if k <= 0 or self._collection.count() == 0:
            return []
        clauses: list[dict[str, Any]] = [{"project_id": project_id}]
        if source_ids:
            clauses.append({"source_id": {"$in": [str(v) for v in source_ids]}})
        if topic_ids:
            clauses.append({"topic_id": {"$in": [str(v) for v in topic_ids]}})
        where: dict[str, Any] = clauses[0] if len(clauses) == 1 else {"$and": clauses}
        result = self._collection.query(
            query_embeddings=[[float(x) for x in embedding]],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        out: list[RetrievedChunk] = []
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        for cid, text, meta, dist in zip(ids, docs, metas, dists):
            meta = meta or {}
            out.append(RetrievedChunk(
                id=str(cid),
                source_id=str(meta.get("source_id", "")),
                topic_id=str(meta["topic_id"]) if meta.get("topic_id") else None,
                text=text or "",
                locator_kind=str(meta.get("locator_kind", "page")),
                locator_value=str(meta.get("locator_value", "")),
                heading=str(meta.get("heading", "") or ""),
                distance=_distance(dist),
                start_ts=_opt_float(meta.get("start_ts")),
                end_ts=_opt_float(meta.get("end_ts")),
            ))
        return out

    def get_by_topic(
        self, topic_id: str, *, project_id: str, limit: int = 8,
        source_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Chunks tagged with one topic, within one project (no vector search)."""
        clauses: list[dict[str, Any]] = [
            {"project_id": project_id}, {"topic_id": str(topic_id)}]
        if source_ids:
            clauses.append({"source_id": {"$in": [str(v) for v in source_ids]}})
        result = self._collection.get(
            where={"$and": clauses}, limit=limit,
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        rows = sorted(
            zip(ids, docs, metas),
            key=lambda t: ((t[2] or {}).get("chunk_index", 0)),
        )[:limit]
        return [
            RetrievedChunk(
                id=str(cid),
                source_id=str((meta or {}).get("source_id", "")),
                topic_id=str(topic_id),
                text=text or "",
                locator_kind=str((meta or {}).get("locator_kind", "page")),
                locator_value=str((meta or {}).get("locator_value", "")),
                heading=str((meta or {}).get("heading", "") or ""),
                distance=0.0,
                start_ts=_opt_float((meta or {}).get("start_ts")),
                end_ts=_opt_float((meta or {}).get("end_ts")),
            )
            for cid, text, meta in rows
        ]

    # ------------------------------------------------------------------ dim
    def _remember_width(self, embeddings: list[list[float]]) -> None:
        try:
            if not self._collection.metadata.get("embedding_dim"):
                # Only the new key: resubmitting hnsw:space is rejected as a
                # "distance change" even when the value is identical.
                self._collection.modify(metadata={"embedding_dim": str(len(embeddings[0]))})
        except Exception:
            log.warning("Could not record Chroma embedding width", exc_info=True)

    def _check_width(self, embeddings: list[list[float]]) -> None:
        """Fail loudly on a model swap instead of mixing vector spaces."""
        try:
            known = (self._collection.metadata or {}).get("embedding_dim")
        except Exception:
            return
        if known is None:
            return
        widths = {len(v) for v in embeddings}
        if widths != {int(known)}:
            raise ValueError(
                f"Embedding width {sorted(widths)} does not match this collection's "
                f"width ({known}). The embedding model was swapped without "
                "re-embedding: run POST /system/reembed (or scripts/re_embed.py)."
            )


def _opt_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["ChromaVectorStore"]
