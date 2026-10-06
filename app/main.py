"""
Study Companion API — composition root.

    uvicorn app.main:app --reload --port 8000

Architecture at a glance (one module per responsibility):

    config.py              .env -> typed Settings (LLM/embeddings/Firecrawl/RAG/BKT)
    llm.py                 OpenAI-SDK clients with dynamic base_url + api_key
    ingestion/parsers.py   Firecrawl /v2/parse (per-page markdown) | pdf-inspector/anydoc fallback
    ingestion/chunker.py   page-aware chunks -> exact `page` metadata
    ingestion/topics.py    knowledge components for the learner model
    ingestion/pipeline.py  parse -> chunk -> embed -> topics -> ChromaDB
    vectorstore.py         ChromaDB (persistent, cosine)
    rag.py                 strict source-grounded answers with [Page N] citations
    learner/bkt.py         Bayesian Knowledge Tracing
    learner/quiz.py        weak-topic MCQ generation + grading
    routers/*.py           HTTP surface: /upload /documents /chat /quiz /learner

Everything stateful is created once in `lifespan()` and shared via app.state.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import Database
from .ingestion import IngestionPipeline
from .learner import QuizService
from .llm import LLMClient, build_embedder
from .rag import RAGService
from .routers import chat, documents, quiz
from .schemas import HealthResponse
from .vectorstore import VectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("study-companion")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build every service exactly once; FastAPI tears them down on shutdown."""
    settings = get_settings()
    settings.ensure_dirs()

    llm = LLMClient(settings)                       # chat completions
    embedder = build_embedder(settings)             # vectors (API or local ONNX)
    store = VectorStore(settings.chroma_dir, settings.collection_name)
    db = Database(settings.sqlite_path)

    app.state.settings = settings
    app.state.llm = llm
    app.state.embedder = embedder
    app.state.store = store
    app.state.db = db
    app.state.pipeline = IngestionPipeline(settings, llm, embedder, store, db)
    app.state.rag = RAGService(settings, llm, embedder, store)
    app.state.quiz = QuizService(settings, llm, embedder, store, db)

    log.info("LLM        : %s @ %s", settings.llm_model, settings.llm_base_url)
    log.info("Embeddings : %s", embedder.name)
    log.info("Parser     : %s%s", settings.parser, "" if settings.firecrawl_api_key else " (no FIRECRAWL_API_KEY -> local pdf-inspector/anydoc)")
    log.info("Data dir   : %s", settings.data_dir.resolve())
    yield


app = FastAPI(
    title="Study Companion API",
    version="1.0.0",
    description="Source-cited RAG tutor + adaptive assessment over your own lecture PDFs.",
    lifespan=lifespan,
)

# The Vite dev server (5173) talks to us directly; tighten in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)   # POST /upload, GET /documents, GET /documents/{id}/file
app.include_router(chat.router)        # POST /chat
app.include_router(quiz.router)        # POST /quiz, POST /quiz/answer, GET /learner/{user}/{doc}


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    s = app.state.settings
    return HealthResponse(
        status="ok",
        llm_base_url=s.llm_base_url,
        llm_model=s.llm_model,
        embeddings=app.state.embedder.name,
        parser=s.parser if s.firecrawl_api_key or s.parser == "local" else "local (no Firecrawl key)",
        documents=len(app.state.db.list_documents()),
    )
