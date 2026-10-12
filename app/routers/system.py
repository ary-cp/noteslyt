"""
Operator endpoints for the vendored llama.cpp embedding backend and the
vendored audio.cpp transcription backend.

`GET /system/llama` reports engine/model presence, running state and update
progress. `POST /system/llama/update` refreshes the engine build in the
background (202 immediately; poll GET for `update_state`). Same shape for
`/system/audio-cpp` (transcription).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from ..config import Settings
from ..deps import AdminUser, CurrentUser, get_settings
from ..schemas import EmbeddingStatusOut, SettingItemOut, SettingsOut, SettingsPatchOut, SetupStatusOut, ModelsFetchRequest

router = APIRouter(tags=["system"])
log = logging.getLogger(__name__)


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


# ------------------------------------------------------- first-run setup
SETUP_COMPLETED_KEY = "setup.completed"

@router.get("/system/setup/status", response_model=SetupStatusOut)
def setup_status(user: CurrentUser, request: Request) -> SetupStatusOut:
    """First-run onboarding state for the admin setup wizard.

    The wizard shows when an admin signs in and setup is not completed.
    Fresh databases have no flag row (nothing completed yet); upgrades are
    backfilled complete by migration 0009 when users already exist.
    """
    from ..repo.app_settings import SystemMetaRepo

    try:
        completed = SystemMetaRepo(request.app.state.db).get(SETUP_COMPLETED_KEY) == "true"
    except Exception:
        completed = False
    return SetupStatusOut(setup_completed=completed, is_admin=getattr(user, "is_admin", False))


@router.post("/system/setup/complete")
def complete_setup(admin: AdminUser, request: Request) -> dict:
    """Mark first-run setup done (wizard Finish or Skip)."""
    from ..repo.app_settings import SystemMetaRepo

    SystemMetaRepo(request.app.state.db).set(SETUP_COMPLETED_KEY, "true")
    log.info("Setup      : admin %s marked first-run setup complete", getattr(admin, "id", "?"))
    return {"completed": True}


@router.post("/system/models")
def list_endpoint_models(payload: ModelsFetchRequest, admin: AdminUser) -> dict:
    """List model ids from any OpenAI-compatible `/models` endpoint.

    Powers the onboarding model pickers: the admin types a base URL and gets
    a dropdown instead of guessing model ids. Admin-only (it can probe
    arbitrary internal URLs, though admins already control every URL the
    server calls). POST — never GET — so the key stays out of URL logs.
    Nothing is stored. Short timeout; failures are 502 with the reason.
    """
    import httpx

    base = (payload.base_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=422, detail="base_url is required.")
    headers = {"Authorization": f"Bearer {payload.api_key}"} if payload.api_key else {}

    def _fetch(url: str) -> list[str]:
        try:
            response = httpx.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _probe_failed(url, f"HTTP {exc.response.status_code}") from exc
        except Exception as exc:
            raise _probe_failed(url, str(exc) or type(exc).__name__) from exc
        try:
            data = response.json().get("data", [])
        except ValueError as exc:
            raise _probe_failed(url, "not a JSON /models response") from exc
        ids = sorted({str(m.get("id")) for m in data
                      if isinstance(m, dict) and m.get("id")})
        return [{"id": mid} for mid in ids[:200]]

    def _probe_failed(url: str, reason: str) -> HTTPException:
        return HTTPException(status_code=502, detail=f"Could not list models at {url}: {reason}.")

    try:
        return {"models": _fetch(f"{base}/models")}
    except HTTPException as exc:
        # Bare-host bases (http://localhost:1234) deserve one retry with /v1.
        if exc.status_code == 502 and not base.endswith("/v1") and "/v1/" not in base:
            try:
                return {"models": _fetch(f"{base}/v1/models")}
            except HTTPException:
                pass
        raise


# ------------------------------------------------------- re-embed job (Ph.3)
@router.post("/system/reembed", status_code=202)
def start_reembed(
    admin: AdminUser,
    request: Request,
    background: BackgroundTasks,
    force: bool = False,
    fresh_parse: bool = False,
) -> dict:
    """Re-embed the whole corpus in the background (202 immediately).

    Resize vector columns to the model's width + re-ingest every stored file.
    Reads, settings and history keep serving while it runs; vector routes stay
    409 until it lands. 409 when a job is already running, or when embeddings
    are in sync and `force` is false (same-width swap needs force=true).
    `fresh_parse` re-parses/transcribes from the files instead of reusing the
    stored parses (needed after an STT/parser upgrade; otherwise transcripts
    are reused, never re-billed).
    """
    from ..reembed import job_snapshot, run_reembed_job, start_reembed_job

    try:
        snapshot = start_reembed_job(request.app, force=force)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(run_reembed_job, request.app, force, fresh_parse)
    log.info("Reembed    : admin %s started job (force=%s fresh_parse=%s)",
             getattr(admin, "id", "?"), force, fresh_parse)
    return {**snapshot, "detail": "Poll GET /system/reembed/status for progress."}


@router.get("/system/reembed/status")
def reembed_status(user: CurrentUser, request: Request) -> dict:
    """Re-embed job progress. Any authed user (the banner polls this)."""
    from ..reembed import job_snapshot

    return job_snapshot(request.app)


# ------------------------------------------------------- admin settings (Ph.2)
def _settings_view(settings: Settings, db_values: dict[str, str], locked: set[str]) -> SettingsOut:
    from ..settings_overlay import EDITABLE, SECRET_KEYS

    items: list[SettingItemOut] = []
    for key in sorted(EDITABLE, key=lambda k: (EDITABLE[k], k)):
        # The width is auto-detected from the endpoint — only show it when an
        # operator pinned it (env) or an admin saved it (db). Otherwise it is
        # noise next to values the admin actually manages.
        if key == "embedding_dim" and key not in locked and key not in db_values:
            continue
        secret = key in SECRET_KEYS
        is_locked = key in locked
        source = "env" if is_locked else ("db" if key in db_values else "default")
        current = getattr(settings, key)
        if secret:
            items.append(SettingItemOut(
                key=key, group=EDITABLE[key], source=source, locked=is_locked,
                secret=True, value=None, configured=current is not None and current != "",
            ))
        else:
            items.append(SettingItemOut(
                key=key, group=EDITABLE[key], source=source, locked=is_locked,
                secret=False, value=current, configured=True,
            ))
    return SettingsOut(settings=items, is_admin=True)


@router.get("/system/settings", response_model=SettingsOut)
def get_admin_settings(
    admin: AdminUser,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SettingsOut:
    """Full settings view for admins. Secrets are never returned in plaintext."""
    from ..settings_overlay import load_db_values, locked_keys

    return _settings_view(settings, load_db_values(request.app.state.db), locked_keys(settings))


@router.patch("/system/settings", response_model=SettingsPatchOut)
def patch_admin_settings(
    payload: dict[str, Any],
    admin: AdminUser,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SettingsPatchOut:
    """Apply admin overrides (null clears back to env/default) and hot-reload.

    Locked keys (pinned by operator env/.env) are refused with 422. A failed
    service rebuild rolls back BOTH the database rows and the live singleton,
    so a bad endpoint can never half-apply.
    """
    from ..repo.app_settings import AppSettingsRepo
    from ..services import build_ai_services
    from ..settings_overlay import (
        EDITABLE, REEMBED_SENSITIVE, coerce_patch_value, load_db_values,
        locked_keys, serialize_for_store,
    )

    unknown = sorted(k for k in payload if k not in EDITABLE)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings keys: {', '.join(unknown)}")
    locked = locked_keys(settings)
    blocked = sorted(k for k in payload if k in locked)
    if blocked:
        raise HTTPException(
            status_code=422,
            detail=f"Locked by server configuration (.env): {', '.join(blocked)}. "
                   "Ask the hoster to change these, or unset them to manage in-app.",
        )
    coerced: dict[str, Any] = {}
    for key, raw in payload.items():
        try:
            coerced[key] = coerce_patch_value(key, raw, settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    db = request.app.state.db
    repo = AppSettingsRepo(db)
    old_rows = load_db_values(db)
    old_singleton = {key: getattr(settings, key) for key in coerced}
    touch = [k for k in coerced if k in REEMBED_SENSITIVE
             and _norm(coerced[k]) != _norm(old_singleton[k])]

    to_write = {k: serialize_for_store(k, v, settings) for k, v in coerced.items() if v is not None}
    to_clear = [k for k, v in coerced.items() if v is None]
    auto_warnings: list[str] = []
    # New endpoint/model but no explicit width: probe the NEW endpoint and use
    # its width for this session (plus the re-embed below). Deliberately NOT
    # persisted: the width is re-detected from the endpoint on every boot, so
    # a stored value would only masquerade as a hand-set pin in the UI (which
    # hides auto-detected dims). Probes a copy — the singleton is only touched
    # on success, and a failed probe warns softly instead of rolling back.
    endpoint_touched = any(
        k in coerced and _norm(coerced[k]) != _norm(old_singleton[k])
        for k in ("embedding_model", "embedding_base_url",
                  "embedding_api_key", "embedding_mode")
    )
    previous_dim = settings.embedding_dim
    if endpoint_touched and "embedding_dim" not in coerced:
        if "embedding_dim" in locked:
            auto_warnings.append(
                f"Endpoint changed but EMBEDDING_DIM={settings.embedding_dim} is pinned "
                "in .env (locked) — update or remove it there; the UI cannot override it."
            )
        else:
            from ..migration_runner import probe_model_dim as _probe

            probed_width = _probe(settings.model_copy(update={
                k: v for k, v in coerced.items() if v is not None}))
            if probed_width is None:
                auto_warnings.append(
                    "Endpoint changed but its width could not be probed "
                    "(unreachable). Re-save when it is up, or set EMBEDDING_DIM manually."
                )
            elif probed_width != settings.embedding_dim:
                setattr(settings, "embedding_dim", probed_width)
                auto_warnings.append(
                    f"Probed the new endpoint: {probed_width}-d vectors — no manual "
                    "width needed. Re-embed to use it."
                )
    # Auto-added keys need old values too, or a rollback below would leave
    # the singleton half-restored.
    for key in coerced:
        old_singleton.setdefault(key, getattr(settings, key, None))
    old_singleton.setdefault("embedding_dim", previous_dim)
    try:
        if to_write:
            repo.set_many(to_write)
        for key in to_clear:
            repo.delete(key)
        for key, value in coerced.items():
            if value is None:
                default = Settings.model_fields[key].get_default(call_default_factory=True)
                setattr(settings, key, default)
            else:
                setattr(settings, key, value)
        # Clearing EMBEDDING_DIM re-arms auto-detect: probe now (services are
        # about to rebuild and the endpoint is up), else roll everything back.
        if settings.embedding_dim is None:
            from ..drift import last_known_width
            from ..migration_runner import probe_model_dim

            probed = probe_model_dim(settings)
            if probed is not None:
                settings.embedding_dim = probed
            else:
                known = last_known_width(db)
                if known is not None:
                    settings.embedding_dim = known
                else:
                    raise RuntimeError(
                        "EMBEDDING_DIM cleared but no width is known (endpoint "
                        "unreachable, nothing stored) — set an explicit width instead."
                    )
        build_ai_services(request.app)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Settings hot-reload failed; rolling back")
        try:
            restore = {k: old_rows[k] for k in coerced if k in old_rows}
            if restore:
                repo.set_many(restore)
            for key in coerced:
                if key not in old_rows:
                    repo.delete(key)
            for key, value in old_singleton.items():
                setattr(settings, key, value)
        except Exception:
            log.exception("Settings rollback ALSO failed — restart to re-apply DB state")
        raise HTTPException(status_code=500, detail=f"Settings not applied (rolled back): {exc}") from exc

    warnings: list[str] = []
    needs_reembed = bool(touch)
    if needs_reembed:
        warnings.append(
            "Embedding configuration changed (" + ", ".join(sorted(touch)) + "). "
            "Existing vectors are from the previous model/space: POST "
            "/system/reembed (or scripts/re_embed.py) before trusting retrieval."
        )
    warnings.extend(auto_warnings)
    # Guards read app.state — refresh it now so the banner/409s agree with the
    # new settings immediately (no restart, no waiting for the next request).
    try:
        from ..drift import compute_drift

        request.app.state.embedding_drift = compute_drift(settings, db)
    except Exception:
        log.exception("Could not refresh drift state after settings PATCH")
    applied = {k: ("cleared" if coerced[k] is None else "db") for k in coerced}
    log.info("Settings  : admin %s updated %d key(s): %s",
             getattr(admin, "id", "?"), len(applied), ", ".join(sorted(applied)))
    return SettingsPatchOut(applied=applied, warnings=warnings, needs_reembed=needs_reembed)


def _norm(value: Any) -> str:
    from pathlib import Path

    if value is None:
        return ""
    if isinstance(value, Path):
        return str(value)
    return str(value)


@router.get("/system/settings/status", response_model=EmbeddingStatusOut)
def embedding_status(
    user: CurrentUser,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    probe: bool = False,
) -> EmbeddingStatusOut:
    """Drift ground truth for the re-embed banner. Any authed user; no secrets.

    `drift` combines the cheap signals (live widths + model vs ground truth)
    with a live probe when the endpoint answers. `revert_to` names the model
    to PATCH back to in order to clear *model* drift without re-embedding
    ("use previous settings").

    The probe is opt-in (`?probe=true`) because waking the embedding endpoint
    evicts a running transcription server (single-resident local engines) —
    the banner and routine polls must never kill an in-flight ingest.
    """
    from ..drift import compute_drift

    db = request.app.state.db
    state = compute_drift(settings, db)
    probed: int | None = None
    if probe:
        from ..migration_runner import probe_model_dim

        try:
            probed = probe_model_dim(settings)
        except Exception:
            probed = None
    detail = state.detail
    is_drift = state.drift
    if not is_drift and probed is not None:
        known = {v for v in list(state.live_widths.values()) + [settings.embedding_dim]
                 if v is not None}
        if known and probed not in known:
            is_drift = True
            detail = (f"Embedding model emits {probed}-d vectors but the database holds "
                      f"{sorted(known)}-d vectors. Re-embed before trusting retrieval.")
    return EmbeddingStatusOut(
        embedding_model=settings.embedding_model,
        resolved_dim=settings.embedding_dim,
        live_widths=state.live_widths,
        probed_width=probed,
        drift=is_drift,
        detail=detail,
        last_model=state.last_model,
        last_dim=state.last_dim,
        revert_to=state.revert_to,
    )
