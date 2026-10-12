"""
NotesLyt API — composition root.

    uvicorn app.main:app --reload --port 8000

Architecture at a glance (one module per responsibility):

    config.py              .env -> typed Settings (LLM/embeddings/Firecrawl/RAG/BKT/memory)
    settings_overlay.py    layered config: explicit env > admin DB settings > defaults
    services.py            build/rebuild AI clients (boot + settings hot-reload share it)
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
from .llm import NativeUnsupportedError, verify_native_support
from .migrations import apply_pending_migrations
from .repo import ChatRepo, ProjectRepo, QuizRepo, SourceRepo, StudioRepo, TopicRepo, UserRepo
from .repo.profile import ProfileRepo
from .routers import auth, chats, profile, projects, quizzes, sources, system
from .schemas import DatabaseOut, EndpointOut, HealthResponse, StorageOut
from .storage import FileStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("noteslyt")


async def _studio_failure_cleanup(studio: StudioRepo, db) -> None:
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

    # Missing secret? Generate + persist it (sessions and encrypted API keys
    # are rooted here, so it must survive restarts — see ensure_auth_secret_key).
    # Only an unwritable `.env` still boots loudly via the check below.
    from .config import ensure_auth_secret_key

    try:
        if ensure_auth_secret_key(settings):
            log.warning("Auth       : AUTH_SECRET_KEY was missing — generated one and saved it to .env")
    except RuntimeError as exc:
        log.warning("Auth       : %s", exc)

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

    # Fail fast and loudly: an unreachable DB should stop the boot, not
    # surface as a 500 on the first request. Backend follows DATABASE_URL:
    # Postgres+pgvector when set, SQLite+Chroma otherwise (desktop default).
    from .backend import create_database, create_vector_store

    db = create_database(settings)

    # Layered config (Phase 2, experimental): explicit env/.env > admin DB
    # settings > defaults. Applied BEFORE the width checks below so a DB-pinned
    # model/dim flows into the probe and the drift guard.
    from .settings_overlay import apply_db_overrides, locked_keys

    try:
        sources = apply_db_overrides(settings, db)
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc
    n_db = sum(1 for s in sources.values() if s == "db")
    if n_db:
        log.info("Settings   : %d admin override(s) from database, %d locked by env",
                 n_db, len(locked_keys(settings)))

    store = create_vector_store(settings, db)
    log.info("Database   : %s backend (%s)", settings.db_backend, settings.db_target)
    storage = FileStorage(settings.uploads_dir)     # local disk

    app.state.settings = settings
    app.state.db = db
    app.state.store = store
    app.state.storage = storage
    app.state.reembed_job = {"state": "idle"}
    # Repositories — the only place SQL lives (plan.md section 4).
    app.state.users = UserRepo(db)
    app.state.projects = ProjectRepo(db)
    app.state.sources = SourceRepo(db)
    app.state.topics = TopicRepo(db)
    app.state.profiles = ProfileRepo(db)
    app.state.chats = ChatRepo(db)
    app.state.quizzes = QuizRepo(db)
    app.state.studio = StudioRepo(db)
    # AI clients + pipeline/memory/chat/quiz (one builder; PATCH reuses it for
    # hot-reload, so boot and PATCH can never diverge). The local vendored
    # servers are initialized in here — the width probe below needs them on a
    # fresh boot, so resolution deliberately happens after this call.
    from .services import build_ai_services

    build_ai_services(app)

    # Embedding width auto-detect (EMBEDDING_DIM unset): probe wins, then the
    # last known width (live column, else ground truth), so offline reboots
    # keep working. If this is a fresh database and the endpoint is not set up
    # yet, keep the API available so the learner can configure it in the UI.
    if settings.embedding_dim is None:
        from .drift import last_known_width
        from .migration_runner import probe_model_dim

        probed = probe_model_dim(settings)
        if probed is not None:
            settings.embedding_dim = probed
            log.info("Embedding  : auto-detected width %d from %s", probed, settings.embedding_model)
        else:
            known = last_known_width(db)
            if known is not None:
                settings.embedding_dim = known
                log.warning("Embedding  : endpoint unreachable, trusting last-known width %d", known)
            else:
                log.warning(
                    "Embedding  : no reachable endpoint or stored width — "
                    "starting unresolved so the app remains available for "
                    "configuration. Embedding-dependent actions will wait "
                    "until a working endpoint and width are configured."
                )
    # Drift no longer refuses to boot (Phase 3, experimental): a model swap
    # without re-embedding degrades vector routes to 409 with the fix, while
    # the app, settings and history stay usable behind the re-embed banner.
    from .drift import compute_drift

    drift_state = compute_drift(settings, db)
    if drift_state.drift:
        log.warning("Embedding DRIFT (degraded vector routes): %s", drift_state.detail)
    app.state.embedding_drift = drift_state

    # Native audio/video vectors are meaningless when the endpoint cannot
    # encode media bytes (text-only or rejecting cloud endpoints). That used
    # to refuse the whole boot; instead we fall back to transcript mode loudly
    # (log + persisted admin override, drift recomputed) so the app stays up
    # on retrievable text. Genuine transport errors still fail fast.
    if settings.audio_embed_mode == "native" or settings.video_embed_mode == "native":
        try:
            verify_native_support(app.state.embedder)
        except NativeUnsupportedError as exc:
            flipped = [k for k in ("audio_embed_mode", "video_embed_mode")
                       if getattr(settings, k) == "native"]
            for key in flipped:
                setattr(settings, key, "transcript")
            log.error(
                "Embedding endpoint cannot encode media bytes (%s). Falling back "
                "to transcript mode for: %s. Serve a joint model "
                "(e.g. embeddinggemma-2-GGUF + mmproj) from llama.cpp and re-set "
                "native modes to use it.", exc, ", ".join(flipped))
            try:
                from .repo.app_settings import AppSettingsRepo

                repo = AppSettingsRepo(db)
                for key in flipped:
                    if key not in locked_keys(settings):
                        repo.set(key, "transcript")
            except Exception:
                log.exception("Could not persist transcript fallback; "
                              "it applies to this session only")
            drift_state = compute_drift(settings, db)
            if drift_state.drift:
                log.warning("Embedding DRIFT (degraded vector routes): %s", drift_state.detail)
            app.state.embedding_drift = drift_state

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
        if settings.transcription_mode == "local":
            try:
                from .audio_cpp import get_transcription_manager

                get_transcription_manager().stop()
            except Exception:
                log.exception("Failed to stop the local transcription server")
        db.close()


app = FastAPI(
    title="NotesLyt API",
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
    import mimetypes

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # Windows' MIME database commonly misses the .mjs extension. PDF.js loads
    # its worker as a JavaScript module, and browsers reject text/plain here.
    mimetypes.add_type("application/javascript", ".mjs")

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
    try:
        from .drift import compute_drift

        drift_detail = compute_drift(s, app.state.db).detail
    except Exception:
        drift_detail = getattr(app.state, "embedding_drift", None)
        drift_detail = drift_detail.detail if drift_detail else ""
    drifted = bool(drift_detail)
    return HealthResponse(
        status="degraded" if (pending or drifted) else "ok",
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
        embedding_drift=(drift_detail if drifted else ""),
    )


FRONTEND_DIR = _mount_frontend(app)
if not FRONTEND_DIR:
    log.info("Frontend   : no build found (frontend/dist or frontend/build) — API-only; use Vite dev on 5173")
