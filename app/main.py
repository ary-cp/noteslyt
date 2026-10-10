"""
Study Companion API — composition root.

    uvicorn app.main:app --reload --port 8000

Architecture at a glance (one module per responsibility):

    config.py              .env -> typed Settings (LLM/embeddings/Firecrawl/RAG/BKT/memory)
    llm.py                 OpenAI-SDK clients with dynamic base_url + api_key
    ingestion/parsers.py   Firecrawl /v2/parse (per-page markdown) | pdf-inspector/anydoc fallback
    ingestion/chunker.py   locator-aware chunks (never cross a locator => exact citations)
    ingestion/topics.py    project-level topics, merged by slug
    ingestion/pipeline.py  parse -> chunk -> embed -> topics -> pgvector
    vectorstore.py         pgvector, project-scoped (match_chunks requires project_id)
    citations.py           generalized labels + strip-if-not-retrieved audit
    chat.py                project-scoped chat over the three memory tiers
    learner/bkt.py         Bayesian Knowledge Tracing
    routers/*.py           HTTP surface: /auth /profile /projects /sources /chats

Everything stateful is created once in `lifespan()` and shared via app.state.
"""

from __future__ import annotations

import logging
import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .chat import ChatService
from .db import REEMBED_HINT, Database, describe_embedding_drift
from .ingestion import IngestionPipeline
from .llm import LLMClient, build_embedder, verify_native_support
from .migrations import apply_pending_migrations
from .quiz import QuizService
from .repo import ChatRepo, ProjectMemoryRepo, ProjectRepo, QuizRepo, SourceRepo, StudioRepo, TopicRepo, UserRepo
from .repo.profile import ProfileRepo
from .routers import auth, chats, profile, projects, quizzes, sources, system
from .schemas import DatabaseOut, EndpointOut, HealthResponse, StorageOut
from .storage import FileStorage
from .transcription import build_transcriber
from .vectorstore import VectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("study-companion")


async def _studio_failure_cleanup(studio: StudioRepo, db: Database) -> None:
    """Prune failed assessment-generation rows hourly; ready artifacts are retained."""
    while True:
        try:
            health = await asyncio.to_thread(db.health)
            if "0005" in {name[:4] for name in health["migrations_pending"]}:
                log.debug("Skipping Studio cleanup while migration 0005 is pending")
            else:
                deleted = await asyncio.to_thread(studio.delete_expired_failures, 24)
                if deleted:
                    log.info("Removed %d expired failed Studio generation(s)", deleted)
        except Exception:
            log.exception("Failed to prune expired Studio generations")
        await asyncio.sleep(60 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build every service exactly once; FastAPI tears them down on shutdown."""
    settings = get_settings()
    settings.ensure_dirs()

    # Auth problems stop the boot: a server that accepts no logins but looks
    # healthy is worse than one that refuses to start.
    auth_errors = settings.auth_startup_errors()
    if auth_errors:
        raise RuntimeError(
            "Authentication is not configured:\n"
            + "\n".join(f"  - {e}" for e in auth_errors)
        )

    migrated = await asyncio.to_thread(apply_pending_migrations, settings)
    if migrated:
        log.info("Database   : applied %d pending migration(s): %s", len(migrated), ", ".join(migrated))

    # Fail fast and loudly: an unreachable DB or a missing pgvector extension
    # should stop the boot, not surface as a 500 on the first request.
    db = Database(settings)

    # Same for embedding widths: a model swap (e.g. 1536-d OpenAI -> 768-d
    # nomic/EmbeddingGemma) without re-embedding fails every ingest at the
    # Postgres insert. Refuse to boot with the fix instead.
    drift = describe_embedding_drift(
        {
            "EMBEDDING_DIM": settings.embedding_dim,
            "chunks.embedding": db.embedding_dim(),
            "questions.stem_embedding": db.vector_width("questions", "stem_embedding"),
        }
    )
    if drift:
        raise RuntimeError("\n".join(drift) + "\n" + REEMBED_HINT)
    store = VectorStore(db)
    llm = LLMClient(settings)                       # chat completions
    if settings.embedding_mode == "local":
        # Vendored llama-server backend: downloads the engine + weights on
        # first boot (loudly logged). The server itself starts lazily on the
        # first embedding call and unloads after idle (wake-on-API).
        from .llama_embed import init_embedding_server

        init_embedding_server(settings)
    embedder = build_embedder(settings)             # vectors (OpenAI-compatible)
    transcriber = build_transcriber(settings)       # speech-to-text
    storage = FileStorage(settings.uploads_dir)     # local disk

    # Native audio/video vectors are meaningless when the endpoint tokenizes
    # the data URI as text. Refuse to boot instead of corrupting retrieval —
    # same philosophy as the embedding-width drift check above.
    if settings.audio_embed_mode == "native" or settings.video_embed_mode == "native":
        verify_native_support(embedder)

    app.state.settings = settings
    app.state.db = db
    app.state.store = store
    app.state.llm = llm
    app.state.embedder = embedder
    app.state.transcriber = transcriber
    app.state.storage = storage
    # Repositories — the only place SQL lives (plan.md section 4).
    app.state.users = UserRepo(db)
    app.state.projects = ProjectRepo(db)
    app.state.sources = SourceRepo(db)
    app.state.topics = TopicRepo(db)
    app.state.profiles = ProfileRepo(db)
    app.state.pipeline = IngestionPipeline(
        settings, llm, embedder, store, app.state.sources, app.state.topics
    )
    # Media parsers only receive (path, settings), so the transcriber built
    # above is handed to them here (Phase D.18). Scripts that ingest without
    # a lifespan get a readable "not configured" failure instead.
    from .ingestion.parsers_media import set_transcriber

    set_transcriber(transcriber)
    # Phase E: server-side chat over the three memory tiers.
    app.state.chats = ChatRepo(db)
    app.state.memory = ProjectMemoryRepo(db, p_init=settings.bkt_p_init)
    app.state.chat = ChatService(
        settings, llm, embedder, store,
        app.state.chats, app.state.memory, app.state.topics, app.state.profiles,
        app.state.sources, storage,
    )
    app.state.quizzes = QuizRepo(db)
    app.state.studio = StudioRepo(db)
    app.state.quiz = QuizService(
        settings, llm, embedder, store, app.state.quizzes,
        app.state.memory, app.state.topics, app.state.sources, app.state.profiles,
    )

    for capability in ("llm", "embedding", "transcription"):
        ep = settings.endpoint(capability)  # type: ignore[arg-type]
        log.info("%-13s: %s @ %s", capability, ep["model"], ep["base_url"])
    for warning in settings.provider_warnings():
        log.warning("Provider config: %s", warning)

    health = db.health()
    log.info("Database   : %s (%s)", health["target"], health["server"])
    if health["migrations_pending"]:
        log.warning("Database   : %d unapplied migration(s): %s",
                    len(health["migrations_pending"]), ", ".join(health["migrations_pending"]))
    log.info("Parser     : %s%s", settings.parser, "" if settings.firecrawl_api_key else " (no FIRECRAWL_API_KEY -> local pdf-inspector/anydoc)")
    log.info("Storage    : local disk (%s)", settings.uploads_dir.resolve())

    studio_cleanup = asyncio.create_task(_studio_failure_cleanup(app.state.studio, db))
    try:
        yield
    finally:
        studio_cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await studio_cleanup
        if settings.embedding_mode == "local":
            try:
                from .llama_embed import get_embedding_manager

                get_embedding_manager().stop()
            except Exception:
                log.exception("Failed to stop the local embedding server")
        db.close()


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

app.include_router(auth.router)        # /auth/signup, /auth/login, /auth/me, /auth/onboarding
app.include_router(profile.router)     # GET|PATCH /profile, /profile/levels
app.include_router(projects.router)    # /projects CRUD + /projects/{id}/memory
app.include_router(sources.router)     # upload / list / topics / status / delete / file / content
app.include_router(chats.router)       # /projects/{id}/chats, /chats/{id}, /chats/{id}/messages
app.include_router(quizzes.router)     # /projects/{id}/quiz, /quizzes/{id}/answer, /learner, /reports
app.include_router(system.router)       # /system/llama operator endpoints


def _mount_frontend(application: FastAPI) -> str:
    """Serve the built frontend on this same port when it exists.

    Mounted AFTER every API route, so `/health`, `/projects`, … always reach
    the API first. Only one SPA path collides with the API (`GET /projects`
    lists projects) — the client therefore uses `/` as its dashboard route; `/`, `/login`, `/signup`,
    `/project/...` and `/console` have no API counterpart and serve index.html
    directly, including on hard refresh.
    """
    directory = get_settings().resolved_frontend_dir
    if directory is None:
        return ""
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # Keep API routes registered before frontend routes. Mounting StaticFiles
    # at '/' returns its own 404 for nested React routes, so mount assets only
    # and let the final catch-all return index.html for browser navigation.
    application.mount(
        "/assets", StaticFiles(directory=str(directory / "assets")), name="frontend-assets"
    )

    @application.get("/", include_in_schema=False)
    async def frontend_index():
        return FileResponse(directory / "index.html")

    @application.get("/{frontend_path:path}", include_in_schema=False)
    async def frontend_fallback(frontend_path: str):
        api_prefixes = (
            "auth/", "profile", "projects", "sources/", "chats/", "quizzes/",
            "learner/", "assessments/", "system/", "health", "openapi.json", "docs", "redoc",
        )
        if frontend_path.startswith(api_prefixes):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not Found")
        asset = directory / frontend_path
        if asset.is_file() and directory in asset.resolve().parents:
            return FileResponse(asset)
        return FileResponse(directory / "index.html")

    log.info("Frontend   : serving %s on this port (same origin as the API)", directory)
    return str(directory)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    s: Settings = app.state.settings
    embedding = s.endpoint("embedding")
    transcription = s.endpoint("transcription")
    db_health = app.state.db.health()
    pending = db_health["migrations_pending"]
    return HealthResponse(
        status="degraded" if pending else "ok",
        llm_base_url=s.llm_base_url,
        llm_model=s.llm_model,
        embeddings=app.state.embedder.name,
        transcription=EndpointOut(base_url=transcription["base_url"], model=transcription["model"]),
        embedding_endpoint=EndpointOut(base_url=embedding["base_url"], model=embedding["model"]),
        database=DatabaseOut(
            target=db_health["target"],
            server=db_health["server"],
            migrations_pending=pending,
        ),
        parser=s.parser if s.firecrawl_api_key or s.parser == "local" else "local (no Firecrawl key)",
        storage=StorageOut(
            backend="local",
            path=str(s.uploads_dir.resolve()),
        ),
        projects=app.state.db.count("projects"),
        frontend=FRONTEND_DIR,
    )


FRONTEND_DIR = _mount_frontend(app)
if not FRONTEND_DIR:
    log.info("Frontend   : no build found (frontend/dist or frontend/build) — API-only; use Vite dev on 5173")
