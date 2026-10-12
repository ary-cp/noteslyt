"""
Embedding-drift ground truth (Phase 3, experimental).

Two independent drift signals, both cheap (no endpoint probe):

1. **Width drift** — `settings.embedding_dim` disagrees with a live `vector(N)`
   column. A model swap without re-embedding fails every ingest/query at the
   Postgres insert, so this must block vector writes/reads.
2. **Model drift** — `system_meta.embedding.last_model` (written after every
   successful ingest and every re-embed) disagrees with the currently
   configured `embedding_model` while chunks exist. This catches same-width
   model swaps, where widths agree but the vector spaces are incomparable.

`compute_drift()` is the single place both are evaluated — boot, the settings
PATCH path, the 409 guard and the status endpoint all read it, so the banner,
the guard and the boot log can never disagree. Probing the endpoint is
deliberately NOT part of it (slow, needs network); the status endpoint adds a
probed comparison on top for the "endpoint says X" hint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .db import describe_embedding_drift

if TYPE_CHECKING:  # pragma: no cover
    from .config import Settings
    from .db import Database

log = logging.getLogger(__name__)

LAST_MODEL_KEY = "embedding.last_model"
LAST_DIM_KEY = "embedding.last_dim"


@dataclass
class DriftState:
    drift: bool = False
    detail: str = ""
    resolved_dim: int | None = None
    live_widths: dict[str, int | None] = field(default_factory=dict)
    current_model: str = ""
    last_model: str | None = None
    last_dim: int | None = None
    chunks: int = 0
    # Model to PATCH back to in order to clear *model* drift without
    # re-embedding ("use previous settings"). None when not applicable.
    revert_to: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "drift": self.drift,
            "detail": self.detail,
            "resolved_dim": self.resolved_dim,
            "live_widths": dict(self.live_widths),
            "current_model": self.current_model,
            "last_model": self.last_model,
            "last_dim": self.last_dim,
            "chunks": self.chunks,
            "revert_to": self.revert_to,
        }


def read_meta(db: Database) -> dict[str, str]:
    """Machine-managed facts, or {} before migration 0008 exists."""
    try:
        from .repo.app_settings import SystemMetaRepo

        return SystemMetaRepo(db).get_all()
    except Exception as exc:
        if "does not exist" in str(exc) or "no such table" in str(exc).lower():
            return {}
        raise


def setup_completed(db: Database) -> bool:
    """Whether first-run onboarding finished (missing flag = fresh install)."""
    try:
        return read_meta(db).get("setup.completed") == "true"
    except Exception:
        return False


def last_known_width(db: Database) -> int | None:
    """Best-effort width when the endpoint is unreachable: live column first,
    then the last ingested width in ground truth (covers SQLite, which has no
    fixed columns). None when nothing is known."""
    try:
        live = db.embedding_dim()
    except Exception:
        live = None
    if live is not None:
        return live
    try:
        raw = read_meta(db).get(LAST_DIM_KEY)
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


def record_ingest_ground_truth(db: Database, model: str, dim: int | None) -> None:
    """Remember what produced the vectors. Best-effort: never fails an ingest."""
    try:
        from .repo.app_settings import SystemMetaRepo

        meta = SystemMetaRepo(db)
        meta.set(LAST_MODEL_KEY, model)
        if dim is not None:
            meta.set(LAST_DIM_KEY, str(dim))
    except Exception as exc:  # noqa: BLE001 — meta is auxiliary, chunks are not
        log.warning("Could not record embedding ground truth: %s", exc)


def compute_drift(settings: Settings, db: Database) -> DriftState:
    """Evaluate both drift signals. Never raises for drift — only for DB errors."""
    live = {
        "chunks.embedding": db.embedding_dim(),
        "questions.stem_embedding": db.vector_width("questions", "stem_embedding"),
    }
    try:
        total_chunks = db.count("chunks")
    except Exception:
        total_chunks = 0
    meta = read_meta(db)
    last_model = meta.get(LAST_MODEL_KEY)
    last_dim: int | None = None
    if meta.get(LAST_DIM_KEY) not in (None, ""):
        try:
            last_dim = int(meta[LAST_DIM_KEY])
        except ValueError:
            last_dim = None

    details: list[str] = []
    backend = getattr(db, "backend", "postgres")
    if backend == "sqlite":
        # No fixed columns: width truth is the last ingested width in meta.
        if (last_dim is not None and settings.embedding_dim is not None
                and last_dim != settings.embedding_dim and total_chunks > 0):
            details.append(
                f"Embedding width is {settings.embedding_dim}-d but the stored "
                f"vectors are {last_dim}-d."
            )
    else:
        width_drift = describe_embedding_drift(
            {k: v for k, v in {"EMBEDDING_DIM": settings.embedding_dim, **live}.items()
             if v is not None}
        )
        details.extend(width_drift)

    model_drift = (
        last_model is not None
        and last_model != settings.embedding_model
        and total_chunks > 0
    )
    if model_drift:
        details.append(
            f"Embedding model is {settings.embedding_model!r} but the stored vectors "
            f"were produced by {last_model!r}."
        )

    state = DriftState(
        drift=bool(details),
        detail=" ".join(details),
        resolved_dim=settings.embedding_dim,
        live_widths=live,
        current_model=settings.embedding_model,
        last_model=last_model,
        last_dim=last_dim,
        chunks=total_chunks,
        revert_to=last_model if model_drift else None,
    )
    return state


__all__ = [
    "DriftState", "LAST_MODEL_KEY", "LAST_DIM_KEY",
    "compute_drift", "read_meta", "setup_completed", "last_known_width",
    "record_ingest_ground_truth",
]
