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
from concurrent.futures import ThreadPoolExecutor
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
    NoDrift,
    get_pipeline,
    get_projects,
    get_settings,
    get_sources,
    get_storage,
    get_store,
    get_topics,
    require_project,
)
from ..ingestion import IngestionPipeline, supported_types
from ..ingestion.registry import parse_source
from ..repo.projects import ProjectRepo
from ..repo.sources import SourceRepo
from ..repo.topics import TopicRepo
from ..schemas import ParsedContentOut, SourceOut, SourceSegmentOut, SourceUploadAccepted, TopicGraphOut, TopicSummary
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
    created = source.created_at
    if created is not None and getattr(created, "tzinfo", None) is None:
        from datetime import timezone as _tz

        created = created.replace(tzinfo=_tz.utc)
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
        created_at=created.isoformat() if created else "",
    )


def _background_ingest(
    pipeline: IngestionPipeline,
    storage: FileStorage,
    source,
    *,
    reuse_parsed: bool = False,
) -> None:
    """
    Run ingestion for one source.

    Called via `BackgroundTasks`, so exceptions here cannot reach the client —
    `ingest_source` already records failures on the row, and this is a second
    net so an unexpected error still flips the status rather than leaving the
    source stuck on 'parsing' forever. `reuse_parsed` resumes from the stored
    parse (retry path); fresh uploads always parse the file.
    """
    try:
        # The row may be gone (deleted mid-ingest): abort quietly instead of
        # crashing on FK writes later. The file check below covers disk.
        if pipeline.sources.get(source.id) is None:
            log.info("background ingest: source %s was deleted, aborting", source.id)
            return
        path = storage.open_for_read(source.project_id, source.id, source.filename)
        if path is None:
            pipeline.sources.mark_failed(source.id, "Stored file disappeared before ingestion.")
            return
        # On-disk names are `<source_id>.<ext>` by construction: a resolved
        # path for any other id means crossed wiring, and ingesting it would
        # silently index the wrong file's content under this source.
        if not path.name.startswith(source.id):
            log.error("background ingest: path %s does not belong to source %s, refusing",
                      path, source.id)
            pipeline.sources.mark_failed(source.id, "Internal storage mismatch — please re-upload this file.")
            return
        pipeline.ingest_source(source, path, reuse_parsed=reuse_parsed)
    except Exception:  # noqa: BLE001 — background tasks have no caller to tell
        log.exception("background ingest crashed for source %s", source.id)
        try:
            pipeline.sources.mark_failed(source.id, "Ingestion crashed unexpectedly.")
        except Exception:  # noqa: BLE001
            log.exception("could not even mark source %s failed", source.id)


# =============================================================================
#  Upload
# =============================================================================
def _ingest_parallel_ok(settings: Settings) -> bool:
    """True when every ingest stage is API-backed and safe to overlap.

    Local engines are single-resident (the vendored llama-server / audio.cpp
    hold one model at a time, and native AV vectors run on them too), so a
    parse running local transcription while another file embeds would evict
    the resident model. Cloud endpoints multiplex, so overlapping files only
    buys throughput there."
    """
    return (
        settings.embedding_mode == "cloud"
        and settings.transcription_mode == "cloud"
        and settings.audio_embed_mode == "transcript"
        and settings.video_embed_mode == "transcript"
    )


def _background_ingest_batch(
    pipeline: IngestionPipeline,
    storage: FileStorage,
    sources,
    max_workers: int = 3,
) -> None:
    """Ingest one upload batch concurrently (API-backed configs only).

    Same per-file semantics as `_background_ingest` — each file keeps its
    own row and failure reason — but files overlap instead of running
    back-to-back: while file 1 embeds, file 2 already parses. Bounded at
    three workers so one 20-file batch cannot exhaust API rate limits or
    hold twenty parsed corpora in memory at once. SQLite serializes the
    concurrent row transactions via busy-timeout (verified); Postgres runs
    them truly concurrently. Exceptions stay inside
    `_background_ingest`, which records them on the row."""
    workers = max(1, min(max_workers, len(sources)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ingest") as pool:
        futures = [pool.submit(_background_ingest, pipeline, storage, source)
                 for source in sources]
        for future in futures:
            try:
                future.result()
            except Exception:  # noqa: BLE001 — recorded on the row already
                log.exception("batched ingest worker raised")


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
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
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

    pending: list = []
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
        pending.append(source)

    if len(pending) > 1 and _ingest_parallel_ok(settings):
        background.add_task(_background_ingest_batch, pipeline, storage, list(pending))
    else:
        for source in pending:
            background.add_task(_background_ingest, pipeline, storage, source)
    if not accepted and rejected:
        raise HTTPException(
            status_code=415,
            detail={"message": "No files were accepted.", "rejected": rejected},
        )

    log.info("upload       : %d file(s) queued into project %s", len(accepted), project_id)
    return SourceUploadAccepted(accepted=accepted, rejected=rejected)


# =============================================================================
#  Retry
# =============================================================================
@router.post(
    "/sources/{source_id}/retry",
    response_model=SourceOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_source(
    source_id: str,
    user: CurrentUser,
    background: BackgroundTasks,
    sources: Annotated[SourceRepo, Depends(get_sources)] = None,      # type: ignore[assignment]
    storage: Annotated[FileStorage, Depends(get_storage)] = None,     # type: ignore[assignment]
    pipeline: Annotated[IngestionPipeline, Depends(get_pipeline)] = None,  # type: ignore[assignment]
    fresh: bool = False,
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> SourceOut:
    """
    Re-run ingestion for a failed source and pick up where it failed.

    By default the retry resumes from the stored parse — transcription (and
    captions) are NOT redone, only the steps after the failure run again
    (topics, embeddings, storage). `fresh=true` re-parses from the file
    instead (parser/STT upgrades). Retrying a still-processing or ready
    source is refused; a foreign id is 404 like everywhere else.
    """
    source = sources.get_owned(source_id, user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    if source.status in ("queued", "parsing", "embedding"):
        raise HTTPException(
            status_code=409,
            detail="This source is still processing — wait for it to settle before retrying.",
        )
    if source.status == "ready":
        raise HTTPException(
            status_code=422,
            detail="This source already ingested. Delete and re-upload it to ingest again.",
        )
    sources.set_status(
        source.id, "queued",
        "retry queued — resuming after the last failure" if not fresh else "retry queued — fresh parse",
        warnings=[],
    )
    background.add_task(_background_ingest, pipeline, storage, source, reuse_parsed=not fresh)
    log.info("retry        : %s requeued %s (fresh=%s)", user.id, source_id, fresh)
    row = sources.get(source.id)
    assert row is not None
    return _to_out(row)


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
                subtopics=topic.subtopics or [],
                sources=topics.sources_for(topic.id),
            )
        )
    return out


@router.get("/projects/{project_id}/topic-graph", response_model=TopicGraphOut)
def get_topic_graph(
    project_id: str,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    pipeline: Annotated[IngestionPipeline, Depends(get_pipeline)],
) -> TopicGraphOut:
    """Prerequisite graph over the project's topics, for the flow map."""
    require_project(projects, project_id, user.id)
    return TopicGraphOut(**pipeline.topic_graph(project_id))


@router.post("/projects/{project_id}/topic-graph/refresh", response_model=TopicGraphOut)
def refresh_topic_graph(
    project_id: str,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    pipeline: Annotated[IngestionPipeline, Depends(get_pipeline)],
) -> TopicGraphOut:
    """Re-infer prerequisite edges from the project's current topics (one LLM call)."""
    require_project(projects, project_id, user.id)
    try:
        return TopicGraphOut(**pipeline.refresh_prereq_edges(project_id))
    except Exception as exc:  # noqa: BLE001 — readable error, never a bare 500
        raise HTTPException(status_code=502, detail=f"Prerequisite inference failed: {exc}") from exc


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


@router.get("/sources/{source_id}/segments", response_model=list[SourceSegmentOut])
def get_source_segments(
    source_id: str,
    user: CurrentUser,
    sources: Annotated[SourceRepo, Depends(get_sources)],
) -> list[SourceSegmentOut]:
    """
    Timestamped transcript segments for one source, oldest first.

    The viewer uses these to highlight the segment containing a cited
    timestamp and scroll to it (audio/video). Empty — not 404 — when the
    source has no timed chunks yet. 404 for another learner's source id
    (never 403) so ids are not enumerable.
    """
    if sources.get_owned(source_id, user.id) is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return [SourceSegmentOut(**row) for row in sources.list_segments(source_id, user.id)]


# =============================================================================
#  Delete
# =============================================================================
@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: str,
    user: CurrentUser,
    sources: Annotated[SourceRepo, Depends(get_sources)],
    storage: Annotated[FileStorage, Depends(get_storage)],
    store=Depends(get_store),
) -> None:
    """
    Delete a source, its chunks and its file.

    The `topic_sources` links cascade, and chunk rows cascade from the source
    row, so no vectors are orphaned. Topic rows themselves are kept — other
    sources in the project may still cover them. Vectors go first: a failed
    SQL delete leaves the row (repairable), while the reverse would leave
    deleted content retrievable.
    """
    source = sources.get_owned(source_id, user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    storage.delete(source.project_id, source.id)
    try:
        store.delete_source(source.id)
    except Exception:
        log.exception("vector cleanup failed for source %s", source.id)
        raise HTTPException(status_code=500, detail="Could not delete this source. Please retry.")
    if not sources.delete_for_project(source.id, source.project_id):
        raise HTTPException(status_code=404, detail="Source not found.")
    log.info("source       : %s deleted %s", user.id, source_id)
