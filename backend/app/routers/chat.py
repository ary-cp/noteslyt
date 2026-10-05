"""
POST /chat — strict, source-grounded tutoring.

Request : {document_id, question, history?: [{role, content}]}
Response: {answer, citations: [{page, heading, snippet}], grounded, retrieved_pages}

`answer` is Markdown whose `[Page N]` tokens the frontend renders as clickable
chips; `citations` is the de-duplicated list of those pages with a snippet for
hover previews. `grounded=false` means the refusal sentence was returned.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..db import Database
from ..deps import get_db, get_rag
from ..rag import RAGService
from ..schemas import ChatRequest, ChatResponse, CitationOut

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, rag: RAGService = Depends(get_rag), db: Database = Depends(get_db)) -> ChatResponse:
    if db.get_document(req.document_id) is None:
        raise HTTPException(404, "Document not found — upload a PDF first.")
    try:
        result = rag.answer(req.question, req.document_id, [t.model_dump() for t in req.history])
    except Exception as exc:  # noqa: BLE001 — LLM/embedding server unreachable etc.
        log.exception("Chat failed")
        raise HTTPException(502, f"LLM request failed: {exc}") from exc

    return ChatResponse(
        answer=result.answer,
        citations=[CitationOut(page=c.page, heading=c.heading, snippet=c.snippet) for c in result.citations],
        grounded=result.grounded,
        retrieved_pages=result.retrieved_pages,
        best_distance=result.best_distance,
    )
