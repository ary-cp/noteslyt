"""
Project ("book") endpoints.

Every route requires a session token and is scoped to the caller. A project id
belonging to another learner returns 404, not 403 — otherwise the id space is
enumerable (plan.md section 5).

This router is deliberately narrow: CRUD plus aggregate stats. Source upload and
ingestion land in Phase C; the `stats` block is what a project list needs to show
"3 sources · 12 topics · 4 chats" without N+1 queries.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import (
    CurrentUser,
    get_memory,
    get_projects,
    get_sources,
    get_storage,
    get_store,
    get_topics,
    require_project,
)
from ..learner.sm2 import is_due
from ..repo.memory import ProjectMemoryRepo
from ..repo.projects import ProjectRepo
from ..repo.sources import SourceRepo
from ..repo.topics import TopicRepo
from ..schemas import (
    ProjectCreate,
    ProjectListItem,
    ProjectMemoryOut,
    ProjectOut,
    ProjectStats,
    ProjectUpdate,
    TopicMemoryOut,
)
from ..storage import FileStorage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


def _iso(value) -> str:
    if value is None:
        return ""
    # See chats router: tag naive SQLite UTC rows so browsers don't skew.
    if getattr(value, "tzinfo", None) is None:
        from datetime import timezone as _tz

        value = value.replace(tzinfo=_tz.utc)
    return value.isoformat()


def _to_out(project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        title=project.title,
        subject=project.subject,
        description=project.description,
        archived=project.archived,
        created_at=_iso(project.created_at),
        updated_at=_iso(project.updated_at),
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
) -> ProjectOut:
    """A project is one subject: its own corpus, topics, chats and memory."""
    project = projects.create(
        user.id,
        title=payload.title.strip(),
        subject=payload.subject.strip(),
        description=payload.description.strip(),
    )
    log.info("project     : %s created '%s'", user.id, project.title)
    return _to_out(project)


@router.get("", response_model=list[ProjectListItem])
def list_projects(
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    sources: Annotated[SourceRepo, Depends(get_sources)],
    topics: Annotated[TopicRepo, Depends(get_topics)],
) -> list[ProjectListItem]:
    """The caller's projects only, newest first, with aggregate counts."""
    owned = projects.list_for_user(user.id)
    if not owned:
        return []
    ids = [p.id for p in owned]
    store_counts = sources.chunk_counts(ids)
    return [
        ProjectListItem(
            **_to_out(p).model_dump(),
            stats=ProjectStats(
                sources=sources.count_for_project(p.id),
                topics=topics.count_for_project(p.id),
                chunks=store_counts.get(p.id, 0),
                # chats count lands in Phase E; 0 until the table is wired up
                chats=0,
            ),
        )
        for p in owned
    ]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
) -> ProjectOut:
    return _to_out(require_project(projects, str(project_id), user.id))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
) -> ProjectOut:
    require_project(projects, str(project_id), user.id)   # 404 before we touch anything
    fields = {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in payload.model_dump(exclude_none=True).items()
    }
    updated = projects.update(str(project_id), user.id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return _to_out(updated)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    storage: Annotated[FileStorage, Depends(get_storage)],
    store=Depends(get_store),
) -> None:
    """
    Cascade-delete a project and remove its uploaded files.

    The database cascade handles sources, chunks, topics, chats and project
    memory; `storage.delete_project` sweeps the bytes off disk, so deleting a
    project cannot leak disk. Vectors go first (Chroma has no SQL cascade):
    a failed row delete leaves the row (repairable), never searchable ghosts.
    """
    if not projects.delete(str(project_id), user.id):
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        store.delete_project(str(project_id))
    except Exception:
        log.exception("vector cleanup failed for project %s", project_id)
    storage.delete_project(str(project_id))
    log.info("project     : %s deleted %s", user.id, project_id)


@router.get("/{project_id}/memory", response_model=ProjectMemoryOut)
def get_project_memory(
    project_id: uuid.UUID,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    memory: Annotated[ProjectMemoryRepo, Depends(get_memory)],
) -> ProjectMemoryOut:
    """Tier-2 state: per-topic mastery, weakest topics, coverage, study streak."""
    require_project(projects, str(project_id), user.id)
    rows = memory.list_for_project(user.id, str(project_id))
    topics = [
        TopicMemoryOut(
            topic_id=r.topic_id,
            slug=r.slug,
            name=r.name,
            description=r.description or "",
            p_mastery=round(float(r.p_mastery), 4),
            attempts=r.attempts,
            correct=r.correct,
            coverage_status=r.coverage_status,
            easiness=float(getattr(r, "easiness", 2.5) or 2.5),
            repetitions=int(getattr(r, "repetitions", 0) or 0),
            interval_days=float(getattr(r, "interval_days", 0.0) or 0.0),
            due_at=r.due_at.isoformat() if getattr(r, "due_at", None) else None,
            label=memory.mastery_label(float(r.p_mastery)),
            last_practiced_at=r.last_practiced_at.isoformat() if r.last_practiced_at else None,
        )
        for r in rows
    ]
    weakest = sorted(topics, key=lambda t: (t.p_mastery, t.name))[:5]
    coverage = {"not_started": 0, "in_progress": 0, "mastered": 0}
    for t in topics:
        coverage[t.coverage_status] = coverage.get(t.coverage_status, 0) + 1
    return ProjectMemoryOut(
        project_id=str(project_id),
        topics=topics,
        weakest=weakest,
        coverage=coverage,
        due_review=sum(1 for t in topics if t.due_at and is_due(t.due_at)),
        streak=memory.streak(user.id, str(project_id)),
    )