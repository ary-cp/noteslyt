"""
FastAPI dependency accessors.

Every service is built exactly once in `main.lifespan()` and parked on
`app.state`; routers pull them through these helpers so they stay unit-testable
(swap `app.state.rag` for a fake and the router never knows).
"""

from __future__ import annotations

from fastapi import Request

from .config import Settings
from .db import Database
from .ingestion import IngestionPipeline
from .learner import QuizService
from .rag import RAGService
from .vectorstore import VectorStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_store(request: Request) -> VectorStore:
    return request.app.state.store


def get_pipeline(request: Request) -> IngestionPipeline:
    return request.app.state.pipeline


def get_rag(request: Request) -> RAGService:
    return request.app.state.rag


def get_quiz(request: Request) -> QuizService:
    return request.app.state.quiz
