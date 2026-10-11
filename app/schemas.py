"""
Pydantic request/response models — the API contract.

`frontend/src/lib/types.ts` mirrors the legacy document/chat/quiz models, but the
frontend is frozen (plan.md D6), so that mirror is knowingly out of date from here
on. New project/source/auth models have no frontend counterpart yet by design.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------- auth
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str


class TokenResponse(BaseModel):
    """`token_type` is "bearer" per RFC 6750; the client sends it as a header."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class OnboardingRequest(BaseModel):
    """Seeds tier-1 global memory (plan.md D5). Every field is optional so the
    learner can skip straight into the app and fill it in later."""

    capability_level: str = "unknown"
    thinking_level: str = "unknown"
    goals: str = ""


class ProfileUpdate(BaseModel):
    """
    Partial profile edit. Fields default to None = "leave unchanged", which is
    what keeps `PATCH /profile {"goals": "..."}` from silently resetting the
    capability level the learner already told us about.
    """

    capability_level: str | None = None
    thinking_level: str | None = None
    goals: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------------ projects
class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    subject: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=2000)


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    subject: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None


class ProjectOut(BaseModel):
    id: str
    title: str
    subject: str = ""
    description: str = ""
    archived: bool = False
    created_at: str
    updated_at: str


class ProjectStats(BaseModel):
    """Aggregate counts shown next to a project in the list."""

    sources: int = 0
    topics: int = 0
    chunks: int = 0
    chats: int = 0


class ProjectListItem(ProjectOut):
    stats: ProjectStats = ProjectStats()


# ------------------------------------------------------------------- sources
class SourceOut(BaseModel):
    id: str
    project_id: str
    filename: str
    source_type: str
    status: str
    status_detail: str = ""
    parser: str = ""
    units: int = 0
    duration_s: float | None = None
    chunk_count: int = 0
    file_url: str | None = None
    warnings: list = []
    created_at: str


class SourceUploadAccepted(BaseModel):
    """Returned by POST /projects/{id}/sources — ingestion runs in the background."""

    accepted: list[SourceOut]
    rejected: list[dict]          # [{filename, reason}]


class ParsedContentOut(BaseModel):
    """The parser's markdown for one source — what the model actually ingested.

    The viewer shows this first ("Parsed" tab) with the original file behind
    an "Original" tab, so the learner can see the model's view of the material.
    """

    source_id: str
    filename: str
    source_type: str
    parser: str = ""
    markdown: str = ""
    truncated: bool = False
    chars: int = 0


class SourceSegmentOut(BaseModel):
    """One timestamped chunk for the viewer's transcript rail.

    Returned oldest-first by `GET /sources/{id}/segments`; the viewer
    highlights the segment containing a cited timestamp and scrolls to it.
    """

    locator_kind: str = ""
    locator_value: str = ""
    heading: str = ""
    text: str = ""
    start_ts: float | None = None
    end_ts: float | None = None
    chunk_index: int = 0


class TopicSummary(BaseModel):
    """A project-level topic plus where it is covered."""

    id: str
    slug: str
    name: str
    description: str = ""
    sources: list[dict] = []       # [{source_id, filename, locators: [...]}]


class TopicGraphNode(BaseModel):
    id: str
    slug: str
    name: str


class TopicGraphEdge(BaseModel):
    topic_slug: str
    prereq_slug: str


class TopicGraphOut(BaseModel):
    """Prerequisite graph over a project's topics (slugs, for the flow map)."""

    nodes: list[TopicGraphNode] = []
    edges: list[TopicGraphEdge] = []


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
    file_url: str | None = None


# ----------------------------------------------------------------------- chat
class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    document_id: str
    question: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = []


class CitationOut(BaseModel):
    """One surviving citation — generalized over locator kinds (Phase D.19)."""

    locator_kind: str = "page"        # page|slide|segment|sheet|row|section|image
    locator_value: str = ""
    page: int | None = None           # set for page locators (back-compat)
    source_id: str | None = None
    heading: str = ""
    snippet: str = ""
    end_ts: float | None = None       # segment end: cited clip plays [start, end]


# ------------------------------------------------------------------ chats
class ChatCreate(BaseModel):
    title: str = Field(default="New chat", max_length=120)


class ChatRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatOut(BaseModel):
    id: str
    project_id: str
    title: str
    summary: str = ""
    message_count: int = 0
    created_at: str
    last_message_at: str


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[CitationOut] = []
    grounded: bool | None = None
    created_at: str


class ChatDetailOut(BaseModel):
    chat: ChatOut
    messages: list[ChatMessageOut]
    total: int


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    response_style: str = Field(default="default", pattern="^(default|precise|explanatory)$")
    source_ids: list[str] | None = Field(default=None, max_length=200)


class SendMessageResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    grounded: bool
    retrieved: list[str] = []
    best_distance: float | None = None
    chat_id: str
    chat_title: str


# ------------------------------------------------------------------ memory
class TopicMemoryOut(BaseModel):
    topic_id: str
    slug: str
    name: str
    description: str = ""
    p_mastery: float
    attempts: int
    correct: int
    coverage_status: str
    label: str
    last_practiced_at: str | None = None


class ProjectMemoryOut(BaseModel):
    project_id: str
    topics: list[TopicMemoryOut]
    weakest: list[TopicMemoryOut]
    coverage: dict[str, int]
    streak: dict[str, int]


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    grounded: bool
    retrieved_pages: list[int] = []
    best_distance: float | None = None


# ----------------------------------------------------------------------- quiz
class TopicMasteryOut(BaseModel):
    topic_id: str
    slug: str = ""
    name: str
    description: str = ""
    p_mastery: float
    attempts: int
    correct: int
    label: str
    coverage_status: str = "not_started"


class QuizCreateRequest(BaseModel):
    topic_id: str | None = None          # omit -> weakest topic (or diagnostic spread)
    n_questions: int | None = Field(default=None, ge=1, le=10)
    qtypes: list[str] | None = None     # subset of mcq|short_answer|numerical
    source_ids: list[str] | None = Field(default=None, max_length=200)


class ExamCreateRequest(BaseModel):
    topic_id: str | None = None
    n_questions: int = Field(default=10, ge=1, le=30)
    qtypes: list[str] = Field(default_factory=lambda: ["mcq", "short_answer"])
    source_ids: list[str] | None = Field(default=None, max_length=200)


class FlashcardsCreateRequest(BaseModel):
    topic_id: str | None = None
    n_cards: int = Field(default=10, ge=1, le=20)
    source_ids: list[str] | None = Field(default=None, max_length=200)


class FlashcardOut(BaseModel):
    id: str
    front: str
    back: str
    topic: str
    locator_kind: str
    locator_value: str
    source_id: str | None = None


class FlashcardsOut(BaseModel):
    flashcards_id: str
    cards: list[FlashcardOut]
    requested: int | None = None


class StudioJobCreate(BaseModel):
    kind: str
    title: str
    payload: dict


class StudioJobOut(BaseModel):
    id: str
    project_id: str
    kind: str
    title: str
    status: str
    payload: dict | None = None
    error: str = ""
    created_at: str
    updated_at: str


class QuizQuestionOut(BaseModel):
    """Client-safe question — keys stay server-side until grading."""

    id: str
    topic_id: str | None = None
    topic_slug: str = ""
    stem: str
    qtype: str
    options: list[str] = []
    locator: str = ""
    difficulty: float = 0.5


class QuizVerificationOut(BaseModel):
    requested: int
    generated: int
    verified: int
    regenerated: int
    dropped: int


class QuizOut(BaseModel):
    quiz_id: str
    topic: TopicMasteryOut | None = None
    questions: list[QuizQuestionOut]
    verification: QuizVerificationOut


class AnswerSubmit(BaseModel):
    question_id: str
    answer: int | float | str           # MCQ index | short text | number


class AnswerCitationOut(BaseModel):
    label: str
    source: str | None = None


class AnswerOut(BaseModel):
    correct: bool
    given: int | float | str
    expected: int | float | str | None = None
    explanation: str = ""
    feedback: str = ""
    citation: AnswerCitationOut
    p_before: float
    p_after: float
    topic: TopicMasteryOut | None = None
    assessment_id: str | None = None


class RecentQuizOut(BaseModel):
    quiz_id: str
    topic_id: str | None = None
    topic_slug: str | None = None
    n_questions: int = 0
    answered: int = 0
    correct: int = 0
    created_at: str


class LearnerOut(BaseModel):
    project_id: str
    topics: list[TopicMemoryOut]
    weakest: list[TopicMemoryOut]
    coverage: dict[str, int]
    streak: dict[str, int]
    recent_quizzes: list[RecentQuizOut]


class AssessmentReportOut(BaseModel):
    id: str
    project_id: str
    label: str
    quiz_ids: list[str]
    report: dict
    created_at: str


# --------------------------------------------------------------------- health
class EndpointOut(BaseModel):
    """Resolved provider for one capability — keys are never included."""

    base_url: str
    model: str


class DatabaseOut(BaseModel):
    """Which Postgres we are on, and whether the schema is current."""

    target: str
    server: str
    migrations_pending: list[str] = []


class StorageOut(BaseModel):
    """Where uploaded files live. Local disk only — this app is not deployed."""

    backend: str = "local"
    path: str


class HealthResponse(BaseModel):
    status: str                       # "ok", or "degraded" when migrations are pending
    # Every capability reports the endpoint it actually resolved to, so a
    # mixed setup (Groq chat + OpenAI embeddings + Groq Whisper) is verifiable
    # without reading logs. `llm_*` kept flat for backwards compatibility.
    llm_base_url: str
    llm_model: str
    embeddings: str
    transcription: EndpointOut
    embedding_endpoint: EndpointOut
    database: DatabaseOut
    parser: str
    storage: StorageOut
    projects: int
    frontend: str = ""                # served bundle dir, or "" when API-only
