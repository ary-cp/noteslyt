"""
SQLite persistence (stdlib `sqlite3`, zero extra dependencies).

Three small tables:
    documents : registry of uploaded PDFs + their extracted topics
    mastery   : the Learner Model — one BKT belief per (user, document, topic)
    quizzes   : generated question sets, kept server-side so answer keys are
                never shipped to the browser before the student answers

All public methods are thread-safe (FastAPI runs sync endpoints in a pool).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DocumentRecord:
    id: str
    filename: str
    total_pages: int
    parser: str
    chunk_count: int
    topics: list[dict]                      # [{id, name, description, pages}]
    created_at: str = field(default_factory=_now)
    warnings: list[str] = field(default_factory=list)


@dataclass
class MasteryRecord:
    user_id: str
    document_id: str
    topic_id: str
    p_mastery: float
    attempts: int = 0
    correct: int = 0
    updated_at: str = field(default_factory=_now)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id          TEXT PRIMARY KEY,
                    filename    TEXT NOT NULL,
                    total_pages INTEGER NOT NULL,
                    parser      TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    topics_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    created_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mastery (
                    user_id     TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    topic_id    TEXT NOT NULL,
                    p_mastery   REAL NOT NULL,
                    attempts    INTEGER NOT NULL DEFAULT 0,
                    correct     INTEGER NOT NULL DEFAULT 0,
                    updated_at  TEXT NOT NULL,
                    PRIMARY KEY (user_id, document_id, topic_id)
                );
                CREATE TABLE IF NOT EXISTS quizzes (
                    id             TEXT PRIMARY KEY,
                    user_id        TEXT NOT NULL,
                    document_id    TEXT NOT NULL,
                    topic_id       TEXT NOT NULL,
                    questions_json TEXT NOT NULL,
                    created_at     TEXT NOT NULL
                );
                """
            )

    # ------------------------------------------------------------ documents
    def save_document(self, doc: DocumentRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?)",
                (doc.id, doc.filename, doc.total_pages, doc.parser, doc.chunk_count,
                 json.dumps(doc.topics), json.dumps(doc.warnings), doc.created_at),
            )

    def get_document(self, document_id: str) -> DocumentRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return self._row_to_doc(row) if row else None

    def list_documents(self) -> list[DocumentRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [self._row_to_doc(r) for r in rows]

    def delete_document(self, document_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            self._conn.execute("DELETE FROM mastery WHERE document_id = ?", (document_id,))
            self._conn.execute("DELETE FROM quizzes WHERE document_id = ?", (document_id,))

    @staticmethod
    def _row_to_doc(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(
            id=row["id"], filename=row["filename"], total_pages=row["total_pages"], parser=row["parser"],
            chunk_count=row["chunk_count"], topics=json.loads(row["topics_json"]),
            warnings=json.loads(row["warnings_json"]), created_at=row["created_at"],
        )

    # -------------------------------------------------------------- mastery
    def get_mastery(self, user_id: str, document_id: str) -> dict[str, MasteryRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM mastery WHERE user_id = ? AND document_id = ?", (user_id, document_id)
            ).fetchall()
        return {r["topic_id"]: MasteryRecord(**dict(r)) for r in rows}

    def upsert_mastery(self, rec: MasteryRecord) -> None:
        rec.updated_at = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO mastery VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, document_id, topic_id) DO UPDATE SET
                   p_mastery=excluded.p_mastery, attempts=excluded.attempts,
                   correct=excluded.correct, updated_at=excluded.updated_at""",
                (rec.user_id, rec.document_id, rec.topic_id, rec.p_mastery, rec.attempts, rec.correct, rec.updated_at),
            )

    # -------------------------------------------------------------- quizzes
    def save_quiz(self, quiz_id: str, user_id: str, document_id: str, topic_id: str, questions: list[dict]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO quizzes VALUES (?,?,?,?,?,?)",
                (quiz_id, user_id, document_id, topic_id, json.dumps(questions), _now()),
            )

    def get_quiz(self, quiz_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"], "user_id": row["user_id"], "document_id": row["document_id"],
            "topic_id": row["topic_id"], "questions": json.loads(row["questions_json"]),
        }
