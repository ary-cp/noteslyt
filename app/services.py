"""
Build (and rebuild) the AI service objects from the resolved Settings.

Lifespan calls `build_ai_services()` once; PATCH /system/settings calls it
again after applying DB overrides — that is the hot-reload: new models, keys
and tuning take effect without a restart. Everything is reconstructed (not
mutated) because ChatService/QuizService snapshot BKT params and hold client
refs at construction; the wrappers are cheap and stateless.

Local-engine managers (`llama_embed` / `audio_cpp`) are singletons keyed off
their own modules: `init_*` is idempotent for an already-initialized manager
and only ensures the vendored servers exist. Changing local paths/weights via
settings takes effect on the next server start, not mid-process — engine
internals (hosts/ports/batches) are therefore env-only (see settings_overlay).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI

log = logging.getLogger(__name__)


def build_ai_services(app: FastAPI) -> dict[str, Any]:
    """(Re)build llm/embedder/transcriber + pipeline/memory/chat/quiz on app.state."""
    from .chat import ChatService
    from .ingestion import IngestionPipeline
    from .ingestion.parsers_media import set_transcriber
    from .llm import LLMClient, build_embedder
    from .quiz import QuizService
    from .repo import ProjectMemoryRepo
    from .transcription import build_transcriber

    settings = app.state.settings
    db = app.state.db

    if settings.embedding_mode == "local":
        from .llama_embed import init_embedding_server

        init_embedding_server(settings)
    if settings.transcription_mode == "local":
        from .audio_cpp import init_transcription_server

        init_transcription_server(settings)

    llm = LLMClient(settings)
    embedder = build_embedder(settings)
    transcriber = build_transcriber(settings)
    set_transcriber(transcriber)

    pipeline = IngestionPipeline(
        settings, llm, embedder, app.state.store,
        app.state.sources, app.state.topics,
    )
    memory = ProjectMemoryRepo(db, p_init=settings.bkt_p_init)
    chat = ChatService(
        settings, llm, embedder, app.state.store,
        app.state.chats, memory, app.state.topics, app.state.profiles,
        app.state.sources, app.state.storage,
    )
    quiz = QuizService(
        settings, llm, embedder, app.state.store, app.state.quizzes,
        memory, app.state.topics, app.state.sources, app.state.profiles,
    )

    app.state.llm = llm
    app.state.embedder = embedder
    app.state.transcriber = transcriber
    app.state.pipeline = pipeline
    app.state.memory = memory
    app.state.chat = chat
    app.state.quiz = quiz
    log.info("Services  : rebuilt AI clients (llm=%s, embedder=%s)", llm.model, embedder.name)
    return {
        "llm": llm, "embedder": embedder, "transcriber": transcriber,
        "pipeline": pipeline, "memory": memory, "chat": chat, "quiz": quiz,
    }


__all__ = ["build_ai_services"]
