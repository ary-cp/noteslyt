"""
Domain records.

Plain dataclasses for rows we read back out of Postgres. They mirror table columns
loosely on purpose — the mapping from row to record happens in `app/repo/base.py`
and tolerates extra columns, so adding a table column does not break the app (the
old `MasteryRecord(**row)` pattern raised TypeError on any new column).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class UserRecord:
    id: str
    email: str
    display_name: str
    created_at: datetime | None = None


@dataclass
class ProjectRecord:
    id: str
    user_id: str
    title: str
    subject: str = ""
    description: str = ""
    archived: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class SourceRecord:
    id: str
    project_id: str
    filename: str
    source_type: str = "other"
    status: str = "queued"
    status_detail: str = ""
    parser: str = ""
    units: int = 0
    duration_s: float | None = None
    chunk_count: int = 0
    file_url: str | None = None
    warnings: list = field(default_factory=list)
    created_at: datetime | None = None


@dataclass
class TopicRecord:
    id: str
    project_id: str
    slug: str
    name: str
    description: str = ""
    created_at: datetime | None = None


@dataclass
class ChatRecord:
    id: str
    project_id: str
    user_id: str
    title: str = "New chat"
    summary: str = ""
    summarized_through: str | None = None
    created_at: datetime | None = None
    last_message_at: datetime | None = None


@dataclass
class ChatMessageRecord:
    id: str
    chat_id: str
    role: str = "user"
    content: str = ""
    citations: list = field(default_factory=list)
    grounded: bool | None = None
    created_at: datetime | None = None


@dataclass
class TopicMemoryRecord:
    """One project topic plus this learner's mastery of it (Phase E.26)."""

    user_id: str = ""
    project_id: str = ""
    topic_id: str = ""
    slug: str = ""
    name: str = ""
    description: str = ""
    p_mastery: float = 0.30
    attempts: int = 0
    correct: int = 0
    coverage_status: str = "not_started"
    last_practiced_at: datetime | None = None


@dataclass
class QuizRecord:
    id: str
    project_id: str
    user_id: str
    topic_id: str | None = None
    created_at: datetime | None = None


@dataclass
class QuestionRecord:
    id: str
    quiz_id: str
    topic_id: str | None = None
    source_id: str | None = None
    seq: int = 0
    qtype: str = "mcq"          # mcq|short_answer|numerical
    stem: str = ""
    options: list = field(default_factory=list)
    answer_key: dict = field(default_factory=dict)
    explanation: str = ""
    locator_kind: str = "page"
    locator_value: str = ""
    difficulty: float = 0.5
    stem_embedding: list | None = None


@dataclass
class AnswerRecord:
    id: str
    quiz_id: str
    question_id: str
    user_id: str
    given: str = ""
    correct: bool = False
    p_before: float = 0.30
    p_after: float = 0.30
    feedback: str = ""
    missed: list = field(default_factory=list)
    matched: list = field(default_factory=list)
    created_at: datetime | None = None


@dataclass
class AssessmentRecord:
    id: str
    project_id: str
    user_id: str
    label: str = ""
    quiz_ids: list = field(default_factory=list)
    report: dict = field(default_factory=dict)
    created_at: datetime | None = None