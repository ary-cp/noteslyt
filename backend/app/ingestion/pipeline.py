"""
Ingestion pipeline:  PDF  ->  pages  ->  chunks  ->  vectors  ->  ChromaDB

    parse_pdf()        Firecrawl /v2/parse (per-page markdown) or local PyMuPDF
    chunk_pages()      page-aware chunks, each tagged with its exact page
    embedder.embed()   one batched call for all chunks
    extract_topics()   one LLM call -> knowledge components for the learner model
    assign_topics()    nearest-centroid tagging, reusing the chunk vectors
    store.upsert()     metadata: document_id, page, chunk_index, heading, topic_id

The pipeline is synchronous on purpose: a 40-slide deck finishes in seconds,
and a single blocking `/upload` is far simpler to demo than job polling.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from ..config import Settings
from ..db import Database, DocumentRecord
from ..llm import Embedder, LLMClient
from ..vectorstore import VectorStore
from .chunker import chunk_pages
from .parsers import ParserError, parse_pdf
from .topics import assign_topics, extract_topics

log = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    pass


class IngestionPipeline:
    def __init__(self, settings: Settings, llm: LLMClient, embedder: Embedder, store: VectorStore, db: Database):
        self.settings = settings
        self.llm = llm
        self.embedder = embedder
        self.store = store
        self.db = db

    def ingest(self, pdf_path: Path, original_filename: str, document_id: str | None = None) -> DocumentRecord:
        document_id = document_id or uuid.uuid4().hex
        t0 = time.perf_counter()

        # 1. Parse -------------------------------------------------------------
        try:
            parsed = parse_pdf(pdf_path, self.settings)
        except ParserError as exc:
            raise IngestionError(str(exc)) from exc
        if parsed.is_empty:
            raise IngestionError("No extractable text found in this PDF (scanned images only?).")

        # 2. Chunk -------------------------------------------------------------
        chunks = chunk_pages(parsed.pages, self.settings.chunk_size, self.settings.chunk_overlap)
        if not chunks:
            raise IngestionError("Document produced no chunks.")

        # 3. Embed -------------------------------------------------------------
        embeddings = self.embedder.embed([c.text for c in chunks])

        # 4. Topics (knowledge components) -------------------------------------
        topics = extract_topics(self.llm, parsed.pages, self.settings.max_topics)
        topic_ids = assign_topics(embeddings, [c.page for c in chunks], topics, self.embedder)

        # 5. Upsert ------------------------------------------------------------
        self.store.upsert(
            ids=[f"{document_id}:{c.chunk_index}" for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[
                {
                    "document_id": document_id,
                    "page": c.page,
                    "chunk_index": c.chunk_index,
                    "heading": c.heading or "",
                    "topic_id": tid,
                }
                for c, tid in zip(chunks, topic_ids)
            ],
        )

        record = DocumentRecord(
            id=document_id,
            filename=original_filename,
            total_pages=parsed.total_pages,
            parser=parsed.parser,
            chunk_count=len(chunks),
            topics=[t.to_dict() for t in topics],
            warnings=parsed.warnings,
        )
        self.db.save_document(record)

        log.info(
            "Ingested %s: %d pages, %d chunks, %d topics via %s in %.1fs",
            original_filename, parsed.total_pages, len(chunks), len(topics), parsed.parser, time.perf_counter() - t0,
        )
        return record
