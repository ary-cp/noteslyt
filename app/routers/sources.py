"""
Source endpoints — uploads into a project.

`POST /projects/{id}/sources` returns **202 Accepted** with a `queued` row per
file and does the parsing/embedding in the background. A 90-minute lecture takes
minutes, so a synchronous upload would time out; the client polls
`GET /sources/{id}` until `status` is `ready` or `failed`.

Phase C ships the PDF path. Phase D registers docx/pptx/xlsx/md/csv/txt,
audio/video transcription and direct multimodal image embeddings in
`app/ingestion/registry.py`; an unregistered type is rejected at upload
with a readable reason rather than failing later during ingestion.
"""

from __future__ import annotations

import logging
from typing import Annotated

import psycopg
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from ..config import Settings
from ..deps import (
    CurrentUser,
    get_pipeline,
    get_projects,
    get_settings,
    get_sources,
    get_storage,
    get_topics,
    require_project,
)
from ..ingestion import IngestionPipeline, supported_types
from ..ingestion.registry import parse_source
from ..repo.projects import ProjectRepo
from ..repo.sources import SourceRepo
from ..repo.topics import TopicRepo
from ..schemas import ParsedContentOut, SourceOut, SourceUploadAccepted, TopicSummary
from ..storage import (
    FileStorage,
    content_type_for,
    extension_of,
    safe_display_name,
    source_type_of,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["sources"])

# Enough headroom for many files in one request without letting a caller
# stream an unbounded body.
MAX_FILES_PER_REQUEST = 20


def _to_out(source) -> SourceOut:
    return SourceOut(
        id=source.id,
        project_id=source.project_id,
        filename=source.filename,
        source_type=source.source_type,
        status=source.status,
        status_detail=source.status_detail,
        parser=source.parser,
        units=source.units,
        duration_s=source.duration_s,
        chunk_count=source.chunk_count,
        file_url=source.file_url,
        warnings=source.warnings,
        created_at=source.created_at.isoformat() if source.created_at else "",
    )


def _background_ingest(
    pipeline: IngestionPipeline,
    storage: FileStorage,
    source,
) -> None:
    """
    Run ingestion for one source.

    Called via `BackgroundTasks`, so exceptions here cannot reach the client —
    `ingest_source` already records failures on the row, and this is a second
    net so an unexpected error still flips the status rather than leaving the
    source stuck on 'parsing' forever.
    """
    try:
        path = storage.open_for_read(source.project_id, source.id, source.filename)
        if path is None:
            pipeline.sources.mark_failed(source.id, "Stored file disappeared before ingestion.")
            return
        pipeline.ingest_source(source, path)
    except Exception:  # noqa: BLE001 — background tasks have no caller to tell
        log.exception("background ingest crashed for source %s", source.id)
        try:
            pipeline.sources.mark_failed(source.id, "Ingestion crashed unexpectedly.")
        except Exception:  # noqa: BLE001
            log.exception("could not even mark source %s failed", source.id)


# =============================================================================
#  Upload
# =============================================================================
@router.post(
    "/projects/{project_id}/sources",
    response_model=SourceUploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_sources(
    project_id: str,
    user: CurrentUser,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    settings: Annotated[Settings, Depends(get_settings)] = None,       # type: ignore[assignment]
    projects: Annotated[ProjectRepo, Depends(get_projects)] = None,     # type: ignore[assignment]
    sources: Annotated[SourceRepo, Depends(get_sources)] = None,       # type: ignore[assignment]
    storage: Annotated[FileStorage, Depends(get_storage)] = None,      # type: ignore[assignment]
    pipeline: Annotated[IngestionPipeline, Depends(get_pipeline)] = None,  # type: ignore[assignment]
) -> SourceUploadAccepted:
    """
    Accept one or more files into a project and ingest them in the background.

    Each file gets its own `sources` row so one bad file cannot fail the batch:
    accepted files come back as `queued`, rejected ones with a reason.
    """
    require_project(projects, project_id, user.id)   # 404 for someone else's project

    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files in one request (max {MAX_FILES_PER_REQUEST}).",
        )

    accepted: list[SourceOut] = []
    rejected: list[dict] = []

    for upload in files:
        filename = safe_display_name(upload.filename or "upload")
        source_type = source_type_of(filename)

        if source_type not in supported_types():
            rejected.append(
                {
                    "filename": filename,
                    "reason": f"No parser for '{extension_of(filename) or 'unknown'}' files yet.",
                }
            )
            continue

        limit_mb = settings.upload_limit_mb(source_type)
        if upload.size is not None and upload.size > limit_mb * 1024 * 1024:
            rejected.append(
                {
                    "filename": filename,
                    "reason": f"{upload.size / 1_048_576:.1f} MB exceeds the {limit_mb} MB "
                              f"limit for {source_type} sources.",
                }
            )
            continue

        source = sources.create(project_id, filename, source_type)
        try:
            storage.save_stream(project_id, source.id, filename, upload.file, limit_mb * 1024 * 1024)
        except (ValueError, OSError) as exc:
            sources.delete_for_project(source.id, project_id)
            log.warning("Could not store upload %s: %s", source.id, exc)
            rejected.append({"filename": filename, "reason": (
                f"File exceeds the {limit_mb} MB limit." if isinstance(exc, ValueError)
                else "Could not save this file. Check available disk space and retry."
            )})
            continue
        accepted.append(_to_out(source))
        background.add_task(_background_ingest, pipeline, storage, source)

    if not accepted and rejected:
        raise HTTPException(
            status_code=415,
            detail={"message": "No files were accepted.", "rejected": rejected},
        )

    log.info("upload       : %d file(s) queued into project %s", len(accepted), project_id)
    return SourceUploadAccepted(accepted=accepted, rejected=rejected)


# =============================================================================
#  Read
# =============================================================================
@router.get("/projects/{project_id}/sources", response_model=list[SourceOut])
def list_sources(
    project_id: str,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    sources: Annotated[SourceRepo, Depends(get_sources)],
) -> list[SourceOut]:
    require_project(projects, project_id, user.id)
    return [_to_out(s) for s in sources.list_owned_in_project(project_id, user.id)]


@router.get("/projects/{project_id}/topics", response_model=list[TopicSummary])
def list_topics(
    project_id: str,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    topics: Annotated[TopicRepo, Depends(get_topics)],
) -> list[TopicSummary]:
    """Project-level topics with every source that covers them."""
    require_project(projects, project_id, user.id)
    out: list[TopicSummary] = []
    for topic in topics.list_for_project(project_id):
        out.append(
            TopicSummary(
                id=topic.id,
                slug=topic.slug,
                name=topic.name,
                description=topic.description,
                sources=topics.sources_for(topic.id),
            )
        )
    return out


@router.get("/projects/{project_id}/ingest-status")
def ingest_status(
    project_id: str,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    sources: Annotated[SourceRepo, Depends(get_sources)],
) -> dict:
    """Poll target for a project's processing and chat-ready source lists."""
    require_project(projects, project_id, user.id)
    counts = sources.counts_by_status(project_id)
    listed = sources.list_owned_in_project(project_id, user.id)
    items = [_to_out(source).model_dump(mode="json") for source in listed]
    terminal = counts.get("ready", 0) + counts.get("failed", 0)
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "settled": terminal == sum(counts.values()) and sum(counts.values()) > 0,
        "processing": [source for source in items if source["status"] in {"queued", "parsing", "embedding"}],
        "ready": [source for source in items if source["status"] == "ready"],
        "failed": [source for source in items if source["status"] == "failed"],
    }


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(
    source_id: str,
    user: CurrentUser,
    sources: Annotated[SourceRepo, Depends(get_sources)],
) -> SourceOut:
    source = sources.get_owned(source_id, user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return _to_out(source)


@router.get("/sources/{source_id}/file")
def get_source_file(
    source_id: str,
    user: CurrentUser,
    sources: Annotated[SourceRepo, Depends(get_sources)],
    storage: Annotated[FileStorage, Depends(get_storage)],
) -> FileResponse:
    """
    Stream the original file from local disk.

    Ownership is checked before the path is touched, so this cannot be used to
    probe another learner's filenames.
    """
    source = sources.get_owned(source_id, user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    path = storage.open_for_read(source.project_id, source.id, source.filename)
    if path is None:
        raise HTTPException(
            status_code=410,
            detail="The stored file is no longer available on disk.",
        )
    return FileResponse(
        path,
        media_type=content_type_for(source.filename),
        filename=source.filename,
    )


@router.get("/sources/{source_id}/content", response_model=ParsedContentOut)
def get_source_content(
    source_id: str,
    user: CurrentUser,
    sources: Annotated[SourceRepo, Depends(get_sources)],
) -> ParsedContentOut:
    """
    The parser's markdown for one source — what the model actually ingested.

    Stored during ingestion in `source_contents`, so opening a document shows
    this text first with the original file one tab away. 404 when ingestion
    never stored text (still processing, or failed before parsing).
    """
    source = sources.get_owned(source_id, user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    try:
        content = sources.get_content(source_id)
    except psycopg.errors.UndefinedTable as exc:
        log.error("source_contents table is missing; apply pending database migrations")
        raise HTTPException(
            status_code=503,
            detail="Parsed content storage is not initialized. Apply pending database migrations and retry.",
        ) from exc
    if content is None or not (content.get("markdown") or "").strip():
        raise HTTPException(
            status_code=404,
            detail=(
                "No parsed text for this source yet — "
                "it is still processing or failed before parsing."
            ),
        )
    return ParsedContentOut(
        source_id=source.id,
        filename=source.filename,
        source_type=source.source_type,
        parser=source.parser or "",
        markdown=content["markdown"],
        truncated=bool(content.get("truncated")),
        chars=int(content.get("chars") or len(content["markdown"])),
    )


# =============================================================================
#  Delete
# =============================================================================
@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: str,
    user: CurrentUser,
    sources: Annotated[SourceRepo, Depends(get_sources)],
    storage: Annotated[FileStorage, Depends(get_storage)],
) -> None:
    """
    Delete a source, its chunks and its file.

    The `topic_sources` links cascade, and chunk rows cascade from the source
    row, so no vectors are orphaned. Topic rows themselves are kept — other
    sources in the project may still cover them.
    """
    source = sources.get_owned(source_id, user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    storage.delete(source.project_id, source.id)
    if not sources.delete_for_project(source.id, source.project_id):
        raise HTTPException(status_code=404, detail="Source not found.")
    log.info("source       : %s deleted %s", user.id, source_id)
