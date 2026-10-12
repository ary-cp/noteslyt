"""
Layered configuration: explicit env/.env  >  admin DB settings  >  defaults.

Two deployment shapes, one binary:

    Desktop app   — near-empty `.env`; the first user is admin and configures
                    models/keys/tuning in-app (stored in `app_settings`).
    Website       — hoster pins keys in `.env`; those keys are "locked" and the
                    settings API refuses PATCHes for them (UI shows read-only).

"Explicit" means the operator actually set it: present in `os.environ` or in
the `.env` file pydantic-settings reads. Everything else falls through to the
DB row, then to the built-in default.

Secrets (`SECRET_KEYS`) are Fernet-encrypted at rest with a key derived from
`AUTH_SECRET_KEY`, so no new env var was needed. Values are stored `enc1:...`;
anything else is a programming error and fails loudly.

The singleton from `get_settings()` stays the object the whole process reads
(`app.state.settings`, services, ingestion guards). `apply_db_overrides()`
materializes the DB layer onto it at boot and after every admin PATCH — a
single-user desktop and a single-process website never race this; multi-process
deployments should pin via env instead (documented, not supported).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from .config import Settings

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids import cycles
    from .db import Database

log = logging.getLogger(__name__)

ENC_PREFIX = "enc1:"

# ------------------------------------------------------------------ allowlist
# Every key an admin may change in-app, with its UI group. Anything not listed
# here is operator-only (.env): DATABASE_URL, AUTH_*, DATA_DIR, FRONTEND_DIR,
# CORS_ORIGINS, DB_POOL_*, MIGRATIONS_DIR, PASSWORD_MIN_LENGTH,
# EMBEDDING_PROVIDER (single choice), and the local-engine network internals
# (hosts/ports/batch sizes — changing those under a running server is
# misleading, since the vendored servers only pick them up on restart).
EDITABLE: dict[str, str] = {
    # chat
    "llm_base_url": "chat", "llm_api_key": "chat", "llm_model": "chat",
    "llm_temperature": "chat", "llm_max_tokens": "chat",
    # embeddings (endpoint + model + behaviour; engine internals stay env-only)
    "embedding_mode": "embeddings", "embedding_base_url": "embeddings",
    "embedding_api_key": "embeddings", "embedding_model": "embeddings",
    "embedding_protocol": "embeddings", "embedding_dim": "embeddings", "embedding_batch_size": "embeddings",
    "embedding_max_input_chars": "embeddings",
    "embedding_local_dir": "embeddings", "embedding_local_repo": "embeddings",
    "embedding_local_model_file": "embeddings",
    "embedding_local_mmproj_file": "embeddings",
    "embedding_local_model_path": "embeddings",
    "embedding_local_mmproj_path": "embeddings",
    "embedding_idle_unload_s": "embeddings",
    "llama_github_repo": "embeddings",
    "audio_embed_mode": "media", "audio_segment_seconds": "media",
    "video_embed_mode": "media", "video_segment_seconds": "media",
    "video_clip_seconds": "media", "video_clip_width": "media",
    # transcription
    "transcription_mode": "transcription", "transcription_base_url": "transcription",
    "transcription_api_key": "transcription", "transcription_model": "transcription",
    "transcription_chunk_s": "transcription",
    "transcription_language": "transcription",
    "transcription_local_dir": "transcription",
    "transcription_local_repo": "transcription",
    "transcription_local_model_repo": "transcription",
    "transcription_local_model_file": "transcription",
    "transcription_local_model_path": "transcription",
    "transcription_local_model_id": "transcription",
    "transcription_local_family": "transcription",
    "transcription_idle_unload_s": "transcription",
    # video keyframes
    "video_captions": "media", "video_frame_every_n": "media",
    "video_frame_width": "media", "video_frame_hamming": "media",
    "video_max_frames": "media",
    # parsing
    "firecrawl_api_key": "parsing", "firecrawl_api_url": "parsing",
    "firecrawl_max_pages": "parsing", "parser": "parsing",
    # retrieval + memory + learner
    "top_k": "retrieval", "max_distance": "retrieval",
    "chunk_size": "retrieval", "chunk_overlap": "retrieval",
    "pdf_page_images": "retrieval", "pdf_page_image_max_pages": "retrieval",
    "history_turns": "retrieval",
    "memory_chat_window": "memory", "memory_summarize_threshold": "memory",
    "memory_max_facts_global": "memory", "memory_max_topics": "memory",
    "memory_context_chars": "memory", "memory_rewrite": "memory",
    "memory_refine_every": "memory",
    "bkt_p_init": "learner", "bkt_p_transit": "learner",
    "bkt_p_slip": "learner", "bkt_p_guess": "learner",
    "quiz_questions": "learner", "max_topics": "learner",
    "quiz_similarity_max": "learner", "quiz_regen_rounds": "learner",
    "quiz_default_tolerance": "learner",
    # uploads
    "max_upload_mb": "uploads", "max_video_mb": "uploads",
    "max_audio_mb": "uploads",
}

SECRET_KEYS = frozenset({
    "llm_api_key", "embedding_api_key", "transcription_api_key",
    "firecrawl_api_key",
})

# Changing any of these invalidates existing vectors: the PATCH response warns
# and (Phase 3) the banner offers a one-click re-embed. Never silent.
REEMBED_SENSITIVE = frozenset({
    "embedding_model", "embedding_dim", "embedding_mode",
    "audio_embed_mode", "video_embed_mode",
})


# ------------------------------------------------------------------ encryption
def _fernet(settings: Settings):
    """Fernet rooted in AUTH_SECRET_KEY (SHA-256 → 32 bytes → b64).

    Rotating AUTH_SECRET_KEY invalidates stored API keys (they decrypt-fail and
    read as unset) — acceptable: rotation is rare and the failure is loud, not
    silent corruption. Imported lazily so `cryptography` only loads when secrets
    are actually stored.
    """
    from cryptography.fernet import Fernet

    if not settings.auth_secret_key:
        raise RuntimeError("AUTH_SECRET_KEY is required to store encrypted settings.")
    digest = hashlib.sha256(settings.auth_secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, settings: Settings) -> str:
    return ENC_PREFIX + _fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str, settings: Settings) -> str | None:
    """Plaintext, or None when undecryptable (rotated secret, manual tampering)."""
    if not stored.startswith(ENC_PREFIX):
        return None
    try:
        return _fernet(settings).decrypt(stored[len(ENC_PREFIX):].encode()).decode()
    except Exception:
        log.warning("A stored API key failed to decrypt (AUTH_SECRET_KEY rotated?) — treating as unset.")
        return None


# ------------------------------------------------------------- explicit (.env)
@lru_cache(maxsize=1)
def _dotenv_keys(dotenv_path: str = ".env") -> frozenset[str]:
    """Upper-case KEYs explicitly set in the dotenv file (mirrors Settings' env_file).

    Keys with an EMPTY value (`FIRECRAWL_API_KEY=`) do NOT count: pydantic
    reads them as empty/unset, so treating them as "explicit" would lock the
    UI for a key the operator never actually configured.
    """
    try:
        text = Path(dotenv_path).read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        raw_key, _, raw_value = line.partition("=")
        key = raw_key.strip().strip("'\"")
        value = raw_value.strip().strip("'\"")
        if key and value:
            keys.add(key.upper())
    return frozenset(keys)


def explicit_field_names(settings: Settings | None = None) -> set[str]:
    """Fields the operator set via process env or `.env` (these lock the UI)."""
    dotenv = _dotenv_keys()
    names: set[str] = set()
    for field_name in Settings.model_fields:
        env_name = field_name.upper()
        # Empty values (FIRECRAWL_API_KEY=) mean "unset", never "explicit".
        if (env_name in os.environ and os.environ[env_name] != "") or env_name in dotenv:
            names.add(field_name)
    return names


def locked_keys(settings: Settings | None = None) -> set[str]:
    """Editable keys pinned by the operator — PATCH must refuse these."""
    return explicit_field_names(settings) & set(EDITABLE)


# ------------------------------------------------------------------ coercion
_adapters: dict[str, TypeAdapter] = {}


def _adapter_for(field_name: str) -> TypeAdapter:
    adapter = _adapters.get(field_name)
    if adapter is None:
        adapter = TypeAdapter(Settings.model_fields[field_name].annotation)
        _adapters[field_name] = adapter
    return adapter


def parse_stored_value(field_name: str, raw: str, settings: Settings) -> Any:
    """Coerce one DB string to the field's type (secrets are decrypted first)."""
    if field_name in SECRET_KEYS:
        if raw.startswith(ENC_PREFIX):
            decrypted = decrypt_secret(raw, settings)
            if decrypted is None:
                raise ValueError(
                    f"{field_name.upper()} is stored but cannot be decrypted — "
                    "AUTH_SECRET_KEY was rotated. Re-enter the key."
                )
            return decrypted
        # Plaintext legacy row (pre-encryption): accept once, rewrite encrypted.
        raw_value: str = raw
    else:
        raw_value = raw
    annotation = Settings.model_fields[field_name].annotation
    try:
        return _adapter_for(field_name).validate_python(raw_value)
    except Exception as exc:
        raise ValueError(f"{field_name.upper()} has an invalid stored value {raw_value!r}: {exc}") from exc


def serialize_for_store(field_name: str, value: Any, settings: Settings) -> str:
    """Python value → DB string (secrets encrypted). None is handled by callers."""
    if field_name in SECRET_KEYS:
        return encrypt_secret(str(value), settings)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def coerce_patch_value(field_name: str, value: Any, settings: Settings) -> Any:
    """Validate one PATCH value against the field type (raises ValueError)."""
    if value is None:
        return None
    try:
        return _adapter_for(field_name).validate_python(value)
    except Exception as exc:
        raise ValueError(f"{field_name.upper()}: invalid value {value!r} — {exc}") from exc


# ------------------------------------------------------------------ overlay
def load_db_values(db: Database) -> dict[str, str]:
    """Raw stored rows, or {} when the table does not exist yet (pre-0008 DB)."""
    try:
        from .repo.app_settings import AppSettingsRepo

        return AppSettingsRepo(db).get_all()
    except Exception as exc:
        if "does not exist" in str(exc) or "no such table" in str(exc).lower():
            return {}
        raise


def apply_db_overrides(settings: Settings, db: Database) -> dict[str, str]:
    """Materialize the DB layer onto the singleton. Returns {key: source}.

    Raises RuntimeError naming the key when a stored row no longer parses —
    a corrupt row must fail loudly at boot, never silently misconfigure the app.
    """
    from .repo.app_settings import AppSettingsRepo  # noqa: F401  (ensures table path)

    stored = load_db_values(db)
    locked = locked_keys(settings)
    sources: dict[str, str] = {}
    for key in EDITABLE:
        if key in locked:
            sources[key] = "env"
            continue
        if key in stored:
            try:
                setattr(settings, key, parse_stored_value(key, stored[key], settings))
            except ValueError as exc:
                raise RuntimeError(f"Invalid admin setting in database: {exc}") from exc
            # Seconds-look: a plaintext secret row is upgraded to encrypted form.
            if key in SECRET_KEYS and not stored[key].startswith(ENC_PREFIX):
                AppSettingsRepo(db).set(key, serialize_for_store(key, getattr(settings, key), settings))
            sources[key] = "db"
        else:
            sources[key] = "default"
    return sources


def ensure_resolved_settings(settings: Settings) -> Settings:
    """Best-effort overlay for scripts without a lifespan (re_embed, verify*).

    Opens a short connection, applies overrides when the table exists, and
    never fails when the DB is unreachable — the caller already reports that.
    """
    try:
        from .backend import create_database
    except Exception:
        return settings
    try:
        db = create_database(settings)
    except Exception:
        return settings
    try:
        apply_db_overrides(settings, db)
    except Exception as exc:
        log.warning("Admin settings overlay skipped: %s", exc)
    finally:
        try:
            db.close()
        except Exception:
            pass
    return settings


__all__ = [
    "EDITABLE", "SECRET_KEYS", "REEMBED_SENSITIVE",
    "encrypt_secret", "decrypt_secret",
    "explicit_field_names", "locked_keys",
    "parse_stored_value", "serialize_for_store", "coerce_patch_value",
    "load_db_values", "apply_db_overrides", "ensure_resolved_settings",
]
