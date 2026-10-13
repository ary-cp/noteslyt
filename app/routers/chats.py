"""
Chat endpoints — server-side conversations over one project's corpus (Phase E).

The client never sends `history`: turns live in `chat_messages`, the prompt
is assembled server-side from the three memory tiers, and answers carry
citations audited against the retrieved locators. Ownership is 404-based —
another learner's chat id is indistinguishable from a missing one.
"""

from __future__ import annotations

import logging
import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from ..chat import ChatMessageNotFoundError, ChatNotFoundError, NoReadySourcesError
from ..deps import (
    CurrentUser,
    NoDrift,
    get_chats,
    get_chat_service,
    get_projects,
    get_settings,
    require_chat,
    require_project,
)
from ..repo.chats import ChatRepo
from ..repo.projects import ProjectRepo
from ..schemas import (
    ChatCreate,
    ChatDetailOut,
    ChatMessageOut,
    ChatOut,
    ChatRename,
    SendMessageRequest,
    SendMessageResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["chats"])


def _iso(value) -> str:
    if value is None:
        return ""
    # Postgres timestamptz arrives aware; SQLite TEXT rows arrive naive UTC.
    # Naive values must be tagged UTC, otherwise the browser parses them as
    # local time and every timestamp skews by the UTC offset (e.g. ~5h in IST).
    if getattr(value, "tzinfo", None) is None:
        from datetime import timezone as _tz

        value = value.replace(tzinfo=_tz.utc)
    return value.isoformat()


def _chat_out(chat, message_count: int = 0) -> ChatOut:
    return ChatOut(
        id=chat.id,
        project_id=chat.project_id,
        title=chat.title,
        summary=chat.summary or "",
        message_count=message_count,
        created_at=_iso(chat.created_at),
        last_message_at=_iso(chat.last_message_at),
    )


def _message_out(message) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        citations=message.citations or [],
        grounded=message.grounded,
        created_at=_iso(message.created_at),
    )


@router.post("/projects/{project_id}/chats", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
def create_chat(
    project_id: uuid.UUID,
    payload: ChatCreate,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    chats: Annotated[ChatRepo, Depends(get_chats)],
) -> ChatOut:
    """Start a named conversation inside a project. Many chats per project."""
    require_project(projects, project_id, user.id)
    chat = chats.create(project_id, user.id, payload.title)
    log.info("chat        : %s opened %s in project %s", user.id, chat.id, project_id)
    return _chat_out(chat)


@router.get("/projects/{project_id}/chats", response_model=list[ChatOut])
def list_chats(
    project_id: uuid.UUID,
    user: CurrentUser,
    projects: Annotated[ProjectRepo, Depends(get_projects)],
    chats: Annotated[ChatRepo, Depends(get_chats)],
) -> list[ChatOut]:
    require_project(projects, project_id, user.id)
    return [
        ChatOut(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            summary="",
            message_count=row["message_count"],
            created_at=_iso(row["created_at"]),
            last_message_at=_iso(row["last_message_at"]),
        )
        for row in chats.list_for_project(project_id, user.id)
    ]


@router.get("/chats/{chat_id}", response_model=ChatDetailOut)
def get_chat(
    chat_id: uuid.UUID,
    user: CurrentUser,
    chats: Annotated[ChatRepo, Depends(get_chats)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ChatDetailOut:
    """A chat plus one page of its history (oldest first)."""
    chat = require_chat(chats, chat_id, user.id)
    messages = chats.list_messages(chat_id, limit=limit, offset=offset)
    return ChatDetailOut(
        chat=_chat_out(chat, message_count=chats.count_messages(chat_id)),
        messages=[_message_out(m) for m in messages],
        total=chats.count_messages(chat_id),
    )


@router.patch("/chats/{chat_id}", response_model=ChatOut)
def rename_chat(
    chat_id: uuid.UUID,
    payload: ChatRename,
    user: CurrentUser,
    chats: Annotated[ChatRepo, Depends(get_chats)],
) -> ChatOut:
    require_chat(chats, chat_id, user.id)
    chat = chats.rename(chat_id, user.id, payload.title)
    if chat is None:  # pragma: no cover — require_chat already proved ownership
        raise HTTPException(status_code=404, detail="Chat not found.")
    return _chat_out(chat, message_count=chats.count_messages(chat_id))


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(
    chat_id: uuid.UUID,
    user: CurrentUser,
    chats: Annotated[ChatRepo, Depends(get_chats)],
) -> None:
    if not chats.delete_owned(chat_id, user.id):
        raise HTTPException(status_code=404, detail="Chat not found.")
    log.info("chat        : %s deleted %s", user.id, chat_id)


@router.post("/chats/{chat_id}/messages", response_model=SendMessageResponse)
def send_message(
    chat_id: uuid.UUID,
    payload: SendMessageRequest,
    user: CurrentUser,
    background: BackgroundTasks,
    chats: Annotated[ChatRepo, Depends(get_chats)],
    service=Depends(get_chat_service),
    settings=Depends(get_settings),
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> SendMessageResponse:
    """Send one message; get a grounded, cited answer. Replaces `POST /chat`."""
    require_chat(chats, chat_id, user.id)
    try:
        result = service.send(
            user.id, str(chat_id), payload.content,
            response_style=payload.response_style,
            source_ids=payload.source_ids,
        )
    except ChatNotFoundError:
        raise HTTPException(status_code=404, detail="Chat not found.") from None

    # Bounded global-memory refinement (Phase E.25): every Nth user turn, in
    # the background, never on the request path.
    try:
        if chats.user_message_count(str(chat_id)) % max(1, settings.memory_refine_every) == 0:
            background.add_task(service.refine_profile, user.id)
    except Exception:  # noqa: BLE001 — refinement scheduling must not fail the turn
        log.warning("Refinement scheduling skipped for chat %s", chat_id, exc_info=True)

    return SendMessageResponse(**result)


@router.post("/chats/{chat_id}/messages/stream")
async def send_message_stream(
    chat_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    payload: SendMessageRequest,
    user: CurrentUser,
    chats: Annotated[ChatRepo, Depends(get_chats)],
    service=Depends(get_chat_service),
    settings=Depends(get_settings),
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> StreamingResponse:
    """Stream generated tokens as SSE, followed by the audited response."""
    require_chat(chats, chat_id, user.id)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def emit(kind: str, value: object) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, value))

    async def events():
        task = asyncio.create_task(asyncio.to_thread(
            service.send, user.id, str(chat_id), payload.content,
            lambda token: emit("token", token),
            payload.response_style,
            payload.source_ids,
            background_tasks.add_task,
        ))
        heartbeat = loop.time()
        try:
            while not task.done() or not queue.empty():
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps({'type': kind, 'value': value}, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # Keep intermediaries from buffering an idle vision/retrieval request.
                    if loop.time() - heartbeat >= 10:
                        yield ": working\n\n"
                        heartbeat = loop.time()
            result = await task
            count = chats.user_message_count(str(chat_id))
            if count % max(1, settings.memory_refine_every) == 0:
                asyncio.create_task(asyncio.to_thread(service.refine_profile, user.id))
            yield f"data: {json.dumps({'type': 'final', 'value': result}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 — emit a stream error instead of truncating silently
            log.exception("streamed chat failed for %s", chat_id)
            yield f"data: {json.dumps({'type': 'error', 'value': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chats/{chat_id}/messages/{message_id}/edit", response_model=SendMessageResponse)
def edit_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: SendMessageRequest,
    user: CurrentUser,
    background: BackgroundTasks,
    chats: Annotated[ChatRepo, Depends(get_chats)],
    service=Depends(get_chat_service),
    settings=Depends(get_settings),
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> SendMessageResponse:
    """Edit a question in place: the old text is replaced and the answer regenerates.

    The target user message and every later turn are removed first, so the
    edited question shows where the old one was instead of appending a copy.
    """
    require_chat(chats, chat_id, user.id)
    try:
        result = service.edit_and_resend(
            user.id, str(chat_id), str(message_id), payload.content,
            response_style=payload.response_style,
            source_ids=payload.source_ids,
        )
    except ChatNotFoundError:
        raise HTTPException(status_code=404, detail="Chat not found.") from None
    except ChatMessageNotFoundError:
        raise HTTPException(status_code=404, detail="Message not found.") from None
    except NoReadySourcesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    try:
        if chats.user_message_count(str(chat_id)) % max(1, settings.memory_refine_every) == 0:
            background.add_task(service.refine_profile, user.id)
    except Exception:  # noqa: BLE001 — refinement scheduling must not fail the turn
        log.warning("Refinement scheduling skipped for chat %s", chat_id, exc_info=True)

    return SendMessageResponse(**result)


@router.post("/chats/{chat_id}/messages/{message_id}/edit/stream")
async def edit_message_stream(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    payload: SendMessageRequest,
    user: CurrentUser,
    chats: Annotated[ChatRepo, Depends(get_chats)],
    service=Depends(get_chat_service),
    settings=Depends(get_settings),
    _drift_guard: NoDrift = None,  # type: ignore[assignment]  # 409 while embedding drifted
) -> StreamingResponse:
    """Stream a regenerated answer for an edited question, as SSE plus the final payload."""
    require_chat(chats, chat_id, user.id)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def emit(kind: str, value: object) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, value))

    async def events():
        task = asyncio.create_task(asyncio.to_thread(
            service.edit_and_resend, user.id, str(chat_id), str(message_id), payload.content,
            lambda token: emit("token", token),
            payload.response_style,
            payload.source_ids,
            background_tasks.add_task,
        ))
        heartbeat = loop.time()
        try:
            while not task.done() or not queue.empty():
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield f"data: {json.dumps({'type': kind, 'value': value}, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if loop.time() - heartbeat >= 10:
                        yield ": working\n\n"
                        heartbeat = loop.time()
            result = await task
            count = chats.user_message_count(str(chat_id))
            if count % max(1, settings.memory_refine_every) == 0:
                asyncio.create_task(asyncio.to_thread(service.refine_profile, user.id))
            yield f"data: {json.dumps({'type': 'final', 'value': result}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 — emit a stream error instead of truncating silently
            log.exception("streamed chat edit failed for %s", chat_id)
            yield f"data: {json.dumps({'type': 'error', 'value': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
