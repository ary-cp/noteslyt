"""
Adaptive assessment endpoints.

POST /quiz                      generate MCQs for the student's weakest topic
POST /quiz/answer               grade one answer, update BKT, return new mastery
GET  /learner/{user}/{document} mastery snapshot for the dashboard
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_quiz
from ..learner import QuizService
from ..schemas import (
    AnswerRequest,
    AnswerResponse,
    LearnerResponse,
    QuizQuestionOut,
    QuizRequest,
    QuizResponse,
    TopicMasteryOut,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["quiz"])


def _mastery(items) -> list[TopicMasteryOut]:
    return [TopicMasteryOut(**t.to_dict()) for t in items]


@router.post("/quiz", response_model=QuizResponse)
@router.post("/quiz/generate", response_model=QuizResponse, include_in_schema=False)
def generate_quiz(req: QuizRequest, quiz: QuizService = Depends(get_quiz)) -> QuizResponse:
    try:
        result = quiz.generate(req.user_id, req.document_id, req.topic_id, req.n_questions)
    except KeyError:
        raise HTTPException(404, "Document not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Quiz generation failed")
        raise HTTPException(502, f"LLM request failed: {exc}") from exc

    return QuizResponse(
        quiz_id=result.id,
        topic=TopicMasteryOut(**result.topic.to_dict()),
        questions=[QuizQuestionOut(**q) for q in result.questions],
        mastery=_mastery(result.mastery),
    )


@router.post("/quiz/answer", response_model=AnswerResponse)
def answer_question(req: AnswerRequest, quiz: QuizService = Depends(get_quiz)) -> AnswerResponse:
    try:
        g = quiz.grade(req.user_id, req.quiz_id, req.question_id, req.selected_index)
    except KeyError:
        raise HTTPException(404, "Quiz or question not found")
    return AnswerResponse(
        correct=g.correct,
        correct_index=g.correct_index,
        explanation=g.explanation,
        page=g.page,
        topic=TopicMasteryOut(**g.topic.to_dict()),
        p_before=g.p_before,
        p_after=g.p_after,
        mastery=_mastery(g.mastery),
    )


@router.get("/learner/{user_id}/{document_id}", response_model=LearnerResponse)
def learner_state(user_id: str, document_id: str, quiz: QuizService = Depends(get_quiz)) -> LearnerResponse:
    try:
        snapshot = quiz.mastery_snapshot(user_id, document_id)
    except KeyError:
        raise HTTPException(404, "Document not found")
    return LearnerResponse(user_id=user_id, document_id=document_id, mastery=_mastery(snapshot))
