"""
Pydantic request/response models — the API contract shared with the frontend
(`frontend/src/lib/types.ts` mirrors these one-to-one).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ documents
class TopicOut(BaseModel):
    id: str
    name: str
    description: str = ""
    pages: list[int] = []


class DocumentOut(BaseModel):
    id: str
    filename: str
    total_pages: int
    parser: str
    chunk_count: int
    topics: list[TopicOut]
    created_at: str
    warnings: list[str] = []


# ----------------------------------------------------------------------- chat
class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    document_id: str
    question: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = []


class CitationOut(BaseModel):
    page: int
    heading: str = ""
    snippet: str = ""


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    grounded: bool
    retrieved_pages: list[int] = []
    best_distance: float | None = None


# ----------------------------------------------------------------------- quiz
class TopicMasteryOut(BaseModel):
    topic_id: str
    name: str
    description: str = ""
    p_mastery: float
    attempts: int
    correct: int
    label: str


class QuizRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    document_id: str
    topic_id: str | None = None          # omit -> weakest topic is chosen
    n_questions: int | None = Field(default=None, ge=1, le=10)


class QuizQuestionOut(BaseModel):
    id: str
    question: str
    options: list[str]
    page: int


class QuizResponse(BaseModel):
    quiz_id: str
    topic: TopicMasteryOut
    questions: list[QuizQuestionOut]
    mastery: list[TopicMasteryOut]


class AnswerRequest(BaseModel):
    user_id: str
    quiz_id: str
    question_id: str
    selected_index: int = Field(ge=0, le=3)


class AnswerResponse(BaseModel):
    correct: bool
    correct_index: int
    explanation: str
    page: int
    topic: TopicMasteryOut
    p_before: float
    p_after: float
    mastery: list[TopicMasteryOut]


class LearnerResponse(BaseModel):
    user_id: str
    document_id: str
    mastery: list[TopicMasteryOut]


# --------------------------------------------------------------------- health
class HealthResponse(BaseModel):
    status: str
    llm_base_url: str
    llm_model: str
    embeddings: str
    parser: str
    documents: int
