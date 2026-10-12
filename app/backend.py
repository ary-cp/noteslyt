"""
Backend factory: Postgres+pgvector (website) vs SQLite+Chroma (desktop default).

`Settings.db_backend` decides (`DATABASE_URL` set → postgres, else sqlite).
Everything downstream takes the created objects — repositories accept either
database (same method surface), services accept either store (same interface).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .config import Settings


def create_database(settings: Settings) -> Any:
    """Relational store for the configured backend (migrations applied by caller)."""
    if settings.db_backend == "sqlite":
        from .sqlite_db import SqliteDatabase

        return SqliteDatabase(settings)
    from .db import Database

    return Database(settings)


def create_vector_store(settings: Settings, db: Any) -> Any:
    """Similarity store: pgvector when Postgres, ChromaDB when SQLite."""
    if settings.db_backend == "sqlite":
        from .vectorstore_chroma import ChromaVectorStore

        return ChromaVectorStore(db, settings)
    from .vectorstore import VectorStore

    return VectorStore(db)


__all__ = ["create_database", "create_vector_store"]
