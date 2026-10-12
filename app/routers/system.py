"""
Operator endpoints for the vendored llama.cpp embedding backend and the
vendored audio.cpp transcription backend.

`GET /system/llama` reports engine/model presence, running state and update
progress. `POST /system/llama/update` refreshes the engine build in the
background (202 immediately; poll GET for `update_state`). Same shape for
`/system/audio-cpp` (transcription).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..config import Settings
from ..deps import CurrentUser, get_settings

router = APIRouter(tags=["system"])


@router.get("/system/llama")
def llama_status(
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Engine + model presence, server state, update progress."""
    if settings.embedding_mode != "local":
        return {"mode": "cloud", "detail": "EMBEDDING_MODE=cloud — vectors come from EMBEDDING_*."}
    from ..llama_embed import get_embedding_manager

    try:
        manager = get_embedding_manager()
    except RuntimeError as exc:
        return {"mode": "local", "initialized": False, "detail": str(exc)}
    return {"mode": "local", "initialized": True, **manager.status().as_dict()}


@router.post("/system/llama/update", status_code=202)
def llama_update(
    user: CurrentUser,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Download the newest llama.cpp build and swap it in (background)."""
    if settings.embedding_mode != "local":
        raise HTTPException(status_code=400, detail="Engine updates apply to EMBEDDING_MODE=local only.")
    from ..llama_embed import get_embedding_manager

    try:
        manager = get_embedding_manager()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(manager.update_engine)
    return {"status": "started", "detail": "Poll GET /system/llama for update_state."}


@router.get("/system/audio-cpp")
def audio_cpp_status(
    user: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """audio.cpp engine + ASR model presence, server state, update progress."""
    if settings.transcription_mode != "local":
        return {"mode": "cloud", "detail": "TRANSCRIPTION_MODE=cloud — transcripts come from TRANSCRIPTION_*."}
    from ..audio_cpp import get_transcription_manager

    try:
        manager = get_transcription_manager()
    except RuntimeError as exc:
        return {"mode": "local", "initialized": False, "detail": str(exc)}
    return {"mode": "local", "initialized": True, **manager.status().as_dict()}


@router.post("/system/audio-cpp/update", status_code=202)
def audio_cpp_update(
    user: CurrentUser,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Download the newest audio.cpp build and swap it in (background)."""
    if settings.transcription_mode != "local":
        raise HTTPException(status_code=400, detail="Engine updates apply to TRANSCRIPTION_MODE=local only.")
    from ..audio_cpp import get_transcription_manager

    try:
        manager = get_transcription_manager()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(manager.update_engine)
    return {"status": "started", "detail": "Poll GET /system/audio-cpp for update_state."}
