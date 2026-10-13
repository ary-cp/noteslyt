"""
Assessment endpoints — project-scoped quizzes, grading, learner, reports (Phase F).

Tenancy rule: quiz rows carry their own `user_id` + `project_id`, so the
answer endpoint (which takes no scope parameter) resolves ownership from the
stored row. Foreign ids are 404, never 403.
"""

from __future__ import annotations

import logging
import asyncio
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from ..deps import (
    CurrentUser,
    NoDrift,
    get_projects,
    get_quiz_service,
    get_quizzes,
    get_studio,
    require_project,
    require_quiz,
)
from ..quiz import QuizError, QuizNotFoundError
from ..repo.projects import ProjectRepo
from ..repo.quizzes import QuizRepo
from ..schemas import (
    AnswerOut,
    AnswerSubmit,
    AssessmentReportOut,
    ExamCreateRequest,
    FlashcardsCreateRequest,
    FlashcardsOut,
    LearnerOut,
    QuizCreateRequest,
    QuizOut,
    RecentQuizOut,
    StudioJobCreate,
    StudioJobOut,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["quiz"])


def _job_out(row) -> StudioJobOut:
    return StudioJobOut(**{**row, "id": str(row["id"]), "project_id": str(row["project_id"]),
                           "created_at": row["created_at"].isoformat(),
                           "updated_at": row["updated_at"].isoformat()})


def _generate_studio_job(job_id, kind, payload, user_id, project_id, studio, service):
    studio.update(job_id, "generating")
    try:
        if kind == "quiz":
            result = service.create_quiz(user_id, project_id, topic_id=payload.get("topic_id"),
                n_questions=payload.get("n_questions"), qtypes=payload.get("qtypes"),
                source_ids=payload.get("source_ids"))
            output = QuizOut(**result).model_dump(mode="json")
            title = f"Quiz · {(result.get('topic') or {}).get('name') or 'mixed topics'}"
        elif kind == "exam":
            result = service.create_quiz(user_id, project_id, topic_id=payload.get("topic_id"),
                n_questions=payload.get("n_questions"), qtypes=payload.get("qtypes"), max_questions=30,
                source_ids=payload.get("source_ids"))
            output = QuizOut(**result).model_dump(mode="json")
            title = f"Mock exam · {(result.get('topic') or {}).get('name') or 'mixed topics'}"
        else:
            result = service.create_flashcards(
                user_id, project_id, payload.get("topic_id"), payload["n_cards"],
                source_ids=payload.get("source_ids"),
            )
            output = FlashcardsOut(**result).model_dump(mode="json")
            title = f"Flashcards · {len(result['cards'])} cards"
        studio.update(job_id, "ready", title=title, payload=output)
    except Exception as exc:  # persisted so the learner can see and retry failures
        log.exception("Studio %s generation failed", kind)
        label = {"quiz": "Quiz", "exam": "Mock exam", "flashcards": "Flashcards"}[kind]
        studio.update(job_id, "failed", title=label, error=str(exc) or "Generation failed. Please retry.")


def _iso(value) -> str:
    if value is None:
        return ""
    # See chats router: tag naive SQLite UTC rows so browsers don't skew.
    if getattr(value, "tzinfo", None) is None:
        from datetime import timezone as _tz

        value = value.replace(tzinfo=_tz.utc)
    return value.isoformat()


@router.post("/projects/{project_id}/quiz", response_model=StudioJobOut, status_code=status.HTTP_202_ACCEPTED)
def create_quiz(
    project_id: uuid.UUID,
    payload: QuizCreateRequest,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    background: BackgroundTasks,
    studio=Depends(get_studio),
    service=Depends(get_quiz_service),
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> StudioJobOut:
    """Queue quiz generation and return its project-scoped Studio job."""
    require_project(projects, str(project_id), user.id)
    row = studio.create(str(project_id), user.id, "quiz", "Quiz · Generating")
    background.add_task(_generate_studio_job, row["id"], "quiz", payload.model_dump(), user.id, str(project_id), studio, service)
    return _job_out(row)


@router.post("/projects/{project_id}/exam", response_model=StudioJobOut, status_code=status.HTTP_202_ACCEPTED)
def create_exam(
    project_id: uuid.UUID,
    payload: ExamCreateRequest,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    background: BackgroundTasks,
    studio=Depends(get_studio),
    service=Depends(get_quiz_service),
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> StudioJobOut:
    """Queue mock exam generation."""
    require_project(projects, str(project_id), user.id)
    row = studio.create(str(project_id), user.id, "exam", "Mock exam · Generating")
    background.add_task(_generate_studio_job, row["id"], "exam", payload.model_dump(), user.id, str(project_id), studio, service)
    return _job_out(row)


@router.post("/projects/{project_id}/flashcards", response_model=StudioJobOut, status_code=status.HTTP_202_ACCEPTED)
def create_flashcards(
    project_id: uuid.UUID,
    payload: FlashcardsCreateRequest,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    background: BackgroundTasks,
    studio=Depends(get_studio),
    service=Depends(get_quiz_service),
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> StudioJobOut:
    """Queue flashcard generation."""
    require_project(projects, str(project_id), user.id)
    row = studio.create(str(project_id), user.id, "flashcards", "Flashcards · Generating")
    background.add_task(_generate_studio_job, row["id"], "flashcards", payload.model_dump(), user.id, str(project_id), studio, service)
    return _job_out(row)


@router.get("/projects/{project_id}/studio-jobs", response_model=list[StudioJobOut])
async def list_studio_jobs(project_id: uuid.UUID, user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)], studio=Depends(get_studio),
    wait_seconds: Annotated[int, Query(ge=0, le=30)] = 0):
    require_project(projects, str(project_id), user.id)
    rows = studio.list_for_project(str(project_id), user.id)
    deadline = time.monotonic() + wait_seconds
    while wait_seconds and time.monotonic() < deadline and any(
        row["status"] in {"queued", "generating"} for row in rows
    ):
        await asyncio.sleep(2)
        rows = studio.list_for_project(str(project_id), user.id)
    return [_job_out(row) for row in rows]


@router.post("/projects/{project_id}/studio-jobs", response_model=StudioJobOut, status_code=status.HTTP_201_CREATED)
def save_studio_artifact(project_id: uuid.UUID, payload: StudioJobCreate, user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)], studio=Depends(get_studio)):
    require_project(projects, str(project_id), user.id)
    if payload.kind not in {"summary", "guide", "sheet", "mindmap", "slides", "schedule"}:
        raise HTTPException(status_code=422, detail="Unsupported Studio artifact type.")
    return _job_out(studio.create(str(project_id), user.id, payload.kind, payload.title, payload.payload, "ready"))


@router.get("/quizzes/{quiz_id}/answers", response_model=dict[str, AnswerOut])
def saved_answers(
    quiz_id: uuid.UUID,
    user: CurrentUser,
    quizzes: Annotated[QuizRepo, Depends(get_quizzes)],
    service=Depends(get_quiz_service),
) -> dict:
    """Resume a learner's first attempts without grading again or revealing unanswered keys."""
    require_quiz(quizzes, str(quiz_id), user.id)
    return service.saved_answers(user.id, str(quiz_id))


@router.post("/quizzes/{quiz_id}/answer", response_model=AnswerOut)
def answer_question(
    quiz_id: uuid.UUID,
    payload: AnswerSubmit,
    user: CurrentUser,
    quizzes: Annotated[QuizRepo, Depends(get_quizzes)],
    service=Depends(get_quiz_service),
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> AnswerOut:
    """Grade one answer (index | text | number) with cited feedback + BKT update."""
    require_quiz(quizzes, str(quiz_id), user.id)
    try:
        return AnswerOut(**service.answer(
            user.id, str(quiz_id), payload.question_id, payload.answer,
        ))
    except QuizNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Question not found.") from exc
    except QuizError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/learner", response_model=LearnerOut)
def get_learner(
    project_id: uuid.UUID,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    service=Depends(get_quiz_service),
) -> LearnerOut:
    """Mastery + coverage + streak + recent quiz history for one project."""
    require_project(projects, str(project_id), user.id)
    data = service.learner(user.id, str(project_id))
    return LearnerOut(
        project_id=data["project_id"],
        topics=data["topics"],
        weakest=data["weakest"],
        coverage=data["coverage"],
        streak=data["streak"],
        recent_quizzes=[
            RecentQuizOut(
                quiz_id=r["id"], topic_id=r["topic_id"], topic_slug=r["topic_slug"],
                n_questions=r["n_questions"], answered=r["answered"], correct=r["correct"],
                created_at=r["created_at"].isoformat() if r["created_at"] else "",
            )
            for r in data["recent_quizzes"]
        ],
    )


@router.get("/assessments/{assessment_id}/report", response_model=AssessmentReportOut)
def get_report(
    assessment_id: uuid.UUID,
    user: CurrentUser,
    service=Depends(get_quiz_service),
) -> AssessmentReportOut:
    """The frozen post-assessment report created when a quiz was completed."""
    try:
        return AssessmentReportOut(**service.report(user.id, str(assessment_id)))
    except QuizNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Assessment not found.") from exc
