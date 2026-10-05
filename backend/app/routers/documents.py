"""
/upload and /documents — the ingestion surface.

POST /upload            multipart PDF -> parse -> chunk -> embed -> index
GET  /documents         registry (newest first)
GET  /documents/{id}    one record incl. extracted topics
GET  /documents/{id}/file   the original PDF, streamed to the viewer pane
DELETE /documents/{id}  removes vectors, mastery, quizzes and the file
"""

from __future__ import annotations

import logging
import shutil
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import Settings
from ..db import Database, DocumentRecord
from ..deps import get_db, get_pipeline, get_settings, get_store
from ..ingestion import IngestionError, IngestionPipeline
from ..schemas import DocumentOut
from ..vectorstore import VectorStore

log = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])

PDF_MAGIC = b"%PDF"


def _to_out(doc: DocumentRecord) -> DocumentOut:
    return DocumentOut(**doc.__dict__)


@router.post("/upload", response_model=DocumentOut, status_code=201)
def upload_pdf(
    file: Annotated[UploadFile, File(description="A lecture PDF / exported slide deck")],
    settings: Settings = Depends(get_settings),
    pipeline: IngestionPipeline = Depends(get_pipeline),
) -> DocumentOut:
    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(415, "Only PDF files are supported. Export slides to PDF first.")

    document_id = uuid.uuid4().hex
    target = settings.uploads_dir / f"{document_id}.pdf"

    # Stream to disk (never buffer a 50 MB deck in RAM), then sanity-check it.
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    size_mb = target.stat().st_size / (1024 * 1024)
    with target.open("rb") as fh:
        is_pdf = fh.read(4) == PDF_MAGIC
    if not is_pdf or size_mb > settings.max_upload_mb:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "Invalid PDF" if not is_pdf else f"PDF exceeds {settings.max_upload_mb} MB")

    try:
        record = pipeline.ingest(target, filename, document_id=document_id)
    except IngestionError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface provider errors verbatim to the UI
        target.unlink(missing_ok=True)
        log.exception("Ingestion failed")
        raise HTTPException(502, f"Ingestion failed: {exc}") from exc
    return _to_out(record)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Database = Depends(get_db)) -> list[DocumentOut]:
    return [_to_out(d) for d in db.list_documents()]


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Database = Depends(get_db)) -> DocumentOut:
    doc = db.get_document(document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return _to_out(doc)


@router.get("/documents/{document_id}/file")
def get_document_file(document_id: str, settings: Settings = Depends(get_settings), db: Database = Depends(get_db)):
    doc = db.get_document(document_id)
    path = settings.uploads_dir / f"{document_id}.pdf"
    if doc is None or not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="application/pdf", filename=doc.filename, content_disposition_type="inline")


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    store: VectorStore = Depends(get_store),
) -> None:
    if db.get_document(document_id) is None:
        raise HTTPException(404, "Document not found")
    store.delete_document(document_id)
    db.delete_document(document_id)
    (settings.uploads_dir / f"{document_id}.pdf").unlink(missing_ok=True)
