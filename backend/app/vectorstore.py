"""
ChromaDB wrapper — a single persistent collection, cosine space.

We pass embeddings in explicitly (computed by `llm.Embedder`), so Chroma is a
pure vector index here and never calls out to any model itself. One collection
holds every document; `document_id` / `topic_id` metadata filters scope queries.

Metadata schema per chunk:
    document_id : str   which upload
    page        : int   1-based physical page  (<- citation source of truth)
    chunk_index : int   order within the document
    heading     : str   nearest section title ("" if none)
    topic_id    : str   knowledge component assigned at ingestion
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import chromadb

log = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    id: str
    text: str
    page: int
    heading: str
    topic_id: str
    distance: float        # cosine distance (0 = identical, 2 = opposite)

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance


class VectorStore:
    def __init__(self, path: Path, collection_name: str = "study_materials"):
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,   # we always supply vectors ourselves
        )

    # ----------------------------------------------------------------- write
    def upsert(self, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict]) -> None:
        # Chroma caps batch size (~5k); 256 keeps memory flat on small machines.
        step = 256
        for i in range(0, len(ids), step):
            self._collection.upsert(
                ids=ids[i : i + step],
                embeddings=embeddings[i : i + step],  # type: ignore[arg-type]
                documents=documents[i : i + step],
                metadatas=metadatas[i : i + step],    # type: ignore[arg-type]
            )

    def delete_document(self, document_id: str) -> None:
        self._collection.delete(where={"document_id": document_id})

    # ------------------------------------------------------------------ read
    def query(self, embedding: list[float], k: int, document_id: str | None = None) -> list[RetrievedChunk]:
        where = {"document_id": document_id} if document_id else None
        result = self._collection.query(
            query_embeddings=[embedding],  # type: ignore[arg-type]
            n_results=k,
            where=where,  # type: ignore[arg-type]
            include=["documents", "metadatas", "distances"],
        )
        return self._to_chunks(
            result["ids"][0],
            result["documents"][0],      # type: ignore[index]
            result["metadatas"][0],      # type: ignore[index]
            result["distances"][0],      # type: ignore[index]
        )

    def get_by_topic(self, document_id: str, topic_id: str, limit: int = 8) -> list[RetrievedChunk]:
        """Metadata-only fetch (no vector) — used to ground quiz generation."""
        result = self._collection.get(
            where={"$and": [{"document_id": document_id}, {"topic_id": topic_id}]},
            limit=limit,
            include=["documents", "metadatas"],
        )
        chunks = self._to_chunks(result["ids"], result["documents"], result["metadatas"], None)  # type: ignore[arg-type]
        return sorted(chunks, key=lambda c: (c.page, c.id))

    def count(self, document_id: str | None = None) -> int:
        if document_id is None:
            return self._collection.count()
        return len(self._collection.get(where={"document_id": document_id}, include=[])["ids"])

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _to_chunks(ids, documents, metadatas, distances) -> list[RetrievedChunk]:
        out: list[RetrievedChunk] = []
        for i, cid in enumerate(ids):
            meta = metadatas[i] or {}
            out.append(
                RetrievedChunk(
                    id=cid,
                    text=documents[i] or "",
                    page=int(meta.get("page", 0)),
                    heading=str(meta.get("heading", "") or ""),
                    topic_id=str(meta.get("topic_id", "") or ""),
                    distance=float(distances[i]) if distances is not None else 0.0,
                )
            )
        return out
