"""
Vector store backed by pgvector.

Two deliberate changes from the old ChromaDB/PostgREST implementation:

1. **Project scoping is mandatory.** `query()` takes `project_id` with no default,
   and the SQL function `match_chunks` requires it too. The old signature treated
   `document_id=None` as "search everything", which is a tenancy hole the moment
   users own private projects (plan.md invariant 3).

2. **One code path.** The in-memory twin is gone; there is a single SQL path
   against the local Postgres instance (plan.md D2).

`RetrievedChunk` keeps a `page` convenience attribute for the existing citation
contract while carrying the generalized `locator_*` fields, so a slide, a
timestamp or a spreadsheet row can be cited the same way a page can.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import psycopg
from pgvector import Vector

from .db import Database

log = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    id: str
    source_id: str
    topic_id: str | None
    text: str
    locator_kind: str
    locator_value: str
    heading: str
    distance: float
    start_ts: float | None = None
    end_ts: float | None = None

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance

    @property
    def page(self) -> int | None:
        """Page number when this chunk is a page locator, else None."""
        if self.locator_kind == "page":
            try:
                return int(self.locator_value)
            except (TypeError, ValueError):
                return None
        return None


class VectorStore:
    def __init__(self, db: Database):
        self._db = db

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
        """
        Store chunks for one source. Each metadata dict may carry:
            topic_id, locator_kind, locator_value, page, start_ts, end_ts, heading
        `project_id` is passed explicitly (not trusted from metadata) so it can
        never disagree with the source's project.
        """
        if not (len(ids) == len(texts) == len(embeddings) == len(metadatas)):
            raise ValueError("ids, texts, embeddings and metadatas must be the same length")
        rows = []
        for cid, embedding, text, meta in zip(ids, embeddings, texts, metadatas, strict=True):
            rows.append(
                (
                    cid,
                    project_id,
                    source_id,
                    meta.get("topic_id"),
                    meta.get("locator_kind", "page"),
                    str(meta.get("locator_value", "")),
                    meta.get("page"),
                    meta.get("start_ts"),
                    meta.get("end_ts"),
                    meta.get("heading", "") or "",
                    int(meta.get("chunk_index", 0)),
                    text,
                    _as_vector(embedding),
                )
            )
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    insert into chunks (
                        id, project_id, source_id, topic_id, locator_kind, locator_value,
                        page, start_ts, end_ts, heading, chunk_index, text, embedding
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (id) do update set
                        topic_id = excluded.topic_id,
                        locator_kind = excluded.locator_kind,
                        locator_value = excluded.locator_value,
                        page = excluded.page,
                        start_ts = excluded.start_ts,
                        end_ts = excluded.end_ts,
                        heading = excluded.heading,
                        chunk_index = excluded.chunk_index,
                        text = excluded.text,
                        embedding = excluded.embedding
                    """,
                    rows,
                )
        return len(rows)

    def delete_source(self, source_id: str) -> int:
        with self._db.transaction() as conn:
            return conn.execute("delete from chunks where source_id = %s", (source_id,)).rowcount

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
        embedding: list[float] | Vector | Any,
        *,
        project_id: str,
        k: int = 6,
        source_ids: list[str] | None = None,
        topic_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """
        Nearest chunks within ONE project.

        `project_id` is keyword-only and required. `source_ids` / `topic_ids`
        narrow within the project; empty lists are treated as "no filter" rather
        than "match nothing".
        """
        params: dict[str, Any] = {
            "query_embedding": _as_vector(embedding),
            "match_count": k,
            "filter_project_id": project_id,
            "filter_source_ids": _uuid_list(source_ids),
            "filter_topic_ids": _uuid_list(topic_ids),
        }
        with self._db.connection() as conn:
            rows = conn.execute(
                """
                select id, source_id, topic_id, text, locator_kind, locator_value,
                       heading, start_ts, end_ts, similarity
                from match_chunks(
                    %(query_embedding)s, %(match_count)s, %(filter_project_id)s,
                    %(filter_source_ids)s, %(filter_topic_ids)s
                )
                """,
                params,
            ).fetchall()
        return [
            RetrievedChunk(
                id=str(r["id"]),
                source_id=str(r["source_id"]),
                topic_id=str(r["topic_id"]) if r["topic_id"] else None,
                text=r["text"],
                locator_kind=r["locator_kind"],
                locator_value=r["locator_value"],
                heading=r["heading"] or "",
                distance=_distance(r["similarity"]),
                start_ts=r["start_ts"],
                end_ts=r["end_ts"],
            )
            for r in rows
        ]

    def get_by_topic(
        self, topic_id: str, *, project_id: str, limit: int = 8,
        source_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Chunks tagged with one topic, within one project. No vector search — used to
        ground quiz generation on a specific topic.

        `source_ids` narrows to processed sources (quiz passes the ready ids);
        empty/None means "no filter".
        """
        sql = (
            "select id, source_id, topic_id, text, locator_kind, locator_value,"
            "heading, start_ts, end_ts "
            "from chunks "
            "where project_id = %s and topic_id = %s"
        )
        params: list = [project_id, topic_id]
        if source_ids:
            sql += " and source_id = any(%s)"
            params.append([str(v) for v in source_ids])
        sql += " order by chunk_index limit %s"
        params.append(limit)
        with self._db.connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            RetrievedChunk(
                id=str(r["id"]),
                source_id=str(r["source_id"]),
                topic_id=str(r["topic_id"]),
                text=r["text"],
                locator_kind=r["locator_kind"],
                locator_value=r["locator_value"],
                heading=r["heading"] or "",
                distance=0.0,
                start_ts=r["start_ts"],
                end_ts=r["end_ts"],
            )
            for r in rows
        ]


def _as_vector(value: Any) -> Vector:
    """
    Coerce whatever the caller has into a `Vector`.

    Accepts a plain list, a numpy array, an existing `Vector` (what pgvector
    returns when reading a column back) or a raw `'[...]'` string, so a vector
    read out of the database can be fed straight back into `query`.
    """
    if isinstance(value, Vector):
        return value
    if isinstance(value, str):
        return Vector(value)
    return Vector(list(value))


def _distance(similarity: Any) -> float:
    """Cosine distance, with NaN collapsed to 1.0 (maximally distant).

    A zero query vector — an empty or degenerate embedding — makes pgvector return
    NaN similarity. The RAG gate compares `distance > max_distance`, and every
    comparison with NaN is False, so a NaN would sail past the refusal gate and
    feed meaningless context to the LLM. Treating it as maximally distant makes
    the gate refuse instead.
    """
    value = 1.0 - float(similarity)
    return value if math.isfinite(value) else 1.0


def _uuid_list(values: list[str] | None) -> list[str] | None:
    """Empty list -> None. Postgres `'{}'::uuid[]` would match nothing, which is
    never the intent of passing no filters."""
    if not values:
        return None
    return [str(v) for v in values]


__all__ = ["VectorStore", "RetrievedChunk", "psycopg"]