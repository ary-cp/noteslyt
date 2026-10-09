"""
Central configuration — every tunable lives here and is read from `.env`.

Why pydantic-settings?
    Types are validated at boot, so a typo in `.env` fails loudly at startup
    instead of silently mid-demo. Every field below maps to an UPPER_CASE
    environment variable (e.g. `llm_base_url` <- `LLM_BASE_URL`).

Deployment shape:
    This app runs entirely on one machine against a local Postgres instance,
    with uploaded files on local disk. There is no cloud dependency to
    configure, which is deliberate — see plan.md section 2.

Provider swapping is env-only (chat, embeddings and transcription each have
their own base URL, key and model):
    LM Studio : LLM_BASE_URL=http://localhost:1234/v1   LLM_API_KEY=lm-studio
    Groq      : LLM_BASE_URL=https://api.groq.com/openai/v1   LLM_API_KEY=gsk_...
    OpenAI    : LLM_BASE_URL=https://api.openai.com/v1   LLM_API_KEY=sk-...
    Nothing else in the codebase knows or cares which one is active.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

Capability = Literal["llm", "embedding", "transcription"]


def _host(base_url: str) -> str:
    """Host of a base URL, for comparing two providers (empty when unparseable)."""
    return (urlparse(base_url).netloc or base_url).lower()


def _redact(api_key: str) -> str:
    """Never log a whole key; enough to tell two keys apart."""
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-2:]}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",          # unknown keys in .env are not fatal
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ LLM
    # Any OpenAI-compatible chat endpoint. The `openai` SDK is pointed at
    # LLM_BASE_URL, so LM Studio / Groq / OpenAI / Ollama / vLLM all work.
    llm_base_url: str = "http://localhost:8888/v1"
    llm_api_key: str = "Unsloth-studio"
    llm_model: str = "local-model"
    llm_temperature: float = 0.1       # low = faithful to the source material
    llm_max_tokens: int = 128000

    # ------------------------------------------------------------ Embeddings
    # Any OpenAI-compatible /embeddings endpoint (LM Studio, OpenAI, a local
    # server speaking the OpenAI wire format). Groq has no embeddings API.
    embedding_provider: Literal["openai"] = "openai"
    embedding_base_url: str | None = None   # falls back to llm_base_url
    embedding_api_key: str | None = None    # falls back to llm_api_key
    embedding_model: str = "embeddinggemma-2-GGUF"
    embedding_batch_size: int = 64
    embedding_dim: int = 768             # must match the model + pgvector column
    # How audio/video sources are vectorized:
    #   "transcript" — embed the Whisper/transcript text (default; works with any
    #                   text embedding model, including EmbeddingGemma).
    #   "native"     — embed the raw audio bytes of each ~20 s segment via
    #                   `Embedder.embed_audio` (data-URI POST, same pattern as
    #                   images). Needs a joint audio-text embedding model whose
    #                   audio and text vectors share one space — a text-only
    #                   server may 200 the request while embedding the URI text,
    #                   which silently destroys retrieval. Probes on 2026-10-09
    #                   showed EmbeddingGemma behaves exactly that way (two
    #                   different blobs: cosine 0.96), so keep "transcript"
    #                   unless your endpoint truly encodes audio.
    # Switching modes changes the vector space: re-ingest the corpus
    # (`uv run python scripts/re_embed.py --force`) after switching.
    audio_embed_mode: Literal["transcript", "native"] = "transcript"

    # --------------------------------------------------------- Transcription
    # Speech-to-text for lecture videos. Any OpenAI-compatible
    # /audio/transcriptions endpoint: Groq (whisper-large-v3-turbo, free tier),
    # OpenAI (whisper-1), or a self-hosted whisper.cpp / faster-whisper server.
    transcription_base_url: str | None = None   # falls back to llm_base_url
    transcription_api_key: str | None = None    # falls back to llm_api_key
    transcription_model: str = "whisper-large-v3-turbo"
    # BCP-47 language hint for /audio/transcriptions (e.g. "en"). Unset means
    # auto-detect. Some self-hosted STT engines (whisper.cpp-style) reject
    # `verbose_json` without an explicit language — see app/transcription.py,
    # which falls back to `json` in that case.
    transcription_language: str | None = None

    # ------------------------------------------------------------- Firecrawl
    # POST /v2/parse accepts a raw PDF upload and, with `pages: true`, returns
    # markdown *per physical page* — that is where our exact citations come from.
    firecrawl_api_key: str | None = None
    firecrawl_api_url: str = "https://api.firecrawl.dev"
    firecrawl_max_pages: int = 100          # Firecrawl bills 1 credit / page
    # "auto"      -> Firecrawl when a key is set, else local pdf-inspector/anydoc
    # "firecrawl" -> Firecrawl only (fails loudly if unavailable)
    # "local"     -> pdf-inspector/anydoc only (fully offline)
    parser: Literal["auto", "firecrawl", "local"] = "auto"

    # --------------------------------------------------------------- Database
    # A single local Postgres instance is the whole story — this submission is
    # not deployed, so there is no hosted-database code path to maintain.
    #   DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/StudyCompanion
    #
    # It stays a DSN (not hard-coded host/user) so moving the database is a .env
    # change, and so the same code would work against any Postgres 14+ with
    # pgvector available.
    database_url: str | None = None            # DATABASE_URL — required, no default
    db_pool_min: int = 1
    db_pool_max: int = 10
    db_connect_timeout_s: int = 10
    migrations_dir: Path = Path("./migrations")

    # --------------------------------------------------------------- Storage
    # Uploaded files live on this machine, under DATA_DIR/uploads/<project_id>/.
    # No object storage, no CDN — see app/storage.py.
    data_dir: Path = Path("./data")
    # Per-type upload ceilings in MB. Video and audio dominate ingest cost
    # (transcription + embedding), so they get a tighter default than documents.
    max_upload_mb: int = 50            # documents, images, spreadsheets
    max_video_mb: int = 300
    max_audio_mb: int = 100

    # ------------------------------------------------------------------ RAG
    top_k: int = 6                 # chunks pulled into the prompt
    # Cosine *distance* (1 - similarity). If even the best chunk is farther
    # than this, we refuse before spending an LLM call. Tune per embedding
    # model: MiniLM ~0.75-0.85, text-embedding-3 ~0.7-0.8. Set 2.0 to disable.
    max_distance: float = 0.8
    chunk_size: int = 1200         # characters, never crosses a page boundary
    chunk_overlap: int = 200
    history_turns: int = 6         # prior messages forwarded to the LLM (legacy RAG)

    # ------------------------------------------- Memory assembly (Phase E)
    # One chat turn is built as: global block + project block + chat summary
    # + recent turns verbatim + retrieved context. Every block has an explicit
    # budget so memory can never crowd out retrieved context (grounding).
    memory_chat_window: int = 10       # recent messages kept verbatim
    memory_summarize_threshold: int = 30  # fold older turns into summary past this many messages
    memory_max_facts_global: int = 8  # lines in the global (profile) block
    memory_max_topics: int = 6        # weakest topics in the project block
    memory_context_chars: int = 6000  # cap on retrieved context text
    memory_rewrite: bool = True       # rewrite follow-ups before embedding
    memory_refine_every: int = 6      # global-refinement pass every N user turns

    # ------------------------------------------------ Learner model (BKT)
    # Standard Bayesian Knowledge Tracing priors. p_guess=0.25 because MCQs
    # have four options.
    bkt_p_init: float = 0.30
    bkt_p_transit: float = 0.15
    bkt_p_slip: float = 0.10
    bkt_p_guess: float = 0.25
    quiz_questions: int = 3
    max_topics: int = 8
    # Phase F novelty + verification: a generated stem with cosine similarity
    # at or above this against the learner's recent stems is a near-duplicate
    # and gets regenerated (one extra round, then dropped).
    quiz_similarity_max: float = 0.9
    quiz_regen_rounds: int = 1
    # Fallback absolute tolerance for numerical questions whose generation
    # did not specify one.
    quiz_default_tolerance: float = 0.01

    # ------------------------------------------------------------------ API
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ------------------------------------------------------- Frontend bundle
    # When the built frontend exists, the API serves it on the same port, so
    # the whole app is one process: `cd frontend && npm run build`, then run
    # the server and open the API origin — no separate Vite process needed.
    # Explicit path wins; otherwise <repo>/frontend/dist, then
    # <repo>/frontend/build. Unset/missing means API-only (Vite dev on 5173).
    frontend_dir: Path | None = None

    # ------------------------------------------------------------------ Auth
    # HS256 secret for the session tokens we issue ourselves (plan.md D3).
    # A shared secret is fine here because there is exactly one issuer (this app).
    # Generate one with:  python -c "import secrets; print(secrets.token_urlsafe(48))"
    auth_secret_key: str | None = None
    auth_algorithm: str = "HS256"
    auth_token_ttl_s: int = 60 * 60 * 24 * 7    # 7 days
    # Bound the password hash's cost: argon2's defaults are tuned for servers,
    # and this default is deliberately the minimum.
    password_min_length: int = 8

    # ------------------------------------------------------------ Derived
    #
    # Every capability (chat / embeddings / speech-to-text) resolves its own
    # endpoint here, once. Each has three env vars that behave identically:
    #
    #   <CAP>_BASE_URL  <- provider endpoint, falls back to LLM_BASE_URL
    #   <CAP>_API_KEY   <- provider key,    falls back to LLM_API_KEY
    #   <CAP>_MODEL     <- provider model,  no fallback (model ids differ)
    #
    # So Groq chat + OpenAI embeddings + Groq Whisper is purely .env:
    #   LLM_BASE_URL=https://api.groq.com/openai/v1  LLM_API_KEY=gsk_...
    #   EMBEDDING_BASE_URL=https://api.openai.com/v1 EMBEDDING_API_KEY=sk-...
    #   TRANSCRIPTION_BASE_URL=https://api.groq.com/openai/v1
    #
    # Callers must use these resolved_* properties, never the raw fields, or
    # they will silently fall back to the LLM provider.
    @property
    def resolved_embedding_base_url(self) -> str:
        return self.embedding_base_url or self.llm_base_url

    @property
    def resolved_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key

    @property
    def resolved_transcription_base_url(self) -> str:
        return self.transcription_base_url or self.llm_base_url

    @property
    def resolved_transcription_api_key(self) -> str:
        return self.transcription_api_key or self.llm_api_key

    def endpoint(self, capability: Capability) -> dict[str, str]:
        """Resolved {base_url, api_key, model} for one capability, for logging/diagnostics."""
        if capability == "llm":
            return {"base_url": self.llm_base_url, "api_key": self.llm_api_key, "model": self.llm_model}
        if capability == "embedding":
            return {
                "base_url": self.resolved_embedding_base_url,
                "api_key": self.resolved_embedding_api_key,
                "model": self.embedding_model,
            }
        if capability == "transcription":
            return {
                "base_url": self.resolved_transcription_base_url,
                "api_key": self.resolved_transcription_api_key,
                "model": self.transcription_model,
            }
        raise ValueError(f"Unknown capability: {capability!r}")

    def provider_warnings(self) -> list[str]:
        """
        Boot-time sanity checks for mixed-provider setups.

        The dangerous case is a per-capability BASE_URL *without* a matching
        API_KEY: the fallback silently sends the chat key to a different host
        (e.g. a Groq `gsk_...` posted to api.openai.com -> opaque 401).
        """
        warnings: list[str] = []
        for capability in ("embedding", "transcription"):
            own_base_url = getattr(self, f"{capability}_base_url")
            own_key = getattr(self, f"{capability}_api_key")
            if not own_base_url or own_key:
                continue
            target = _host(self.endpoint(capability)["base_url"])  # type: ignore[arg-type]
            if target != _host(self.llm_base_url):
                warnings.append(
                    f"{capability.upper()}_BASE_URL points at {target} but "
                    f"{capability.upper()}_API_KEY is unset, so the LLM key "
                    f"({_redact(self.llm_api_key)}) will be sent there. "
                    f"Set {capability.upper()}_API_KEY to that provider's key."
                )
        return warnings

    @property
    def auth_configured(self) -> bool:
        return bool(self.auth_secret_key)

    def auth_startup_errors(self) -> list[str]:
        """
        Auth problems that should stop the boot rather than surface as a 500 on
        the first login attempt.
        """
        errors: list[str] = []
        if not self.auth_secret_key:
            errors.append(
                "AUTH_SECRET_KEY is not set, so no one can log in. Generate one with:\n"
                '  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
                "  then add AUTH_SECRET_KEY=<value> to .env"
            )
        elif len(self.auth_secret_key) < 32:
            errors.append(
                f"AUTH_SECRET_KEY is only {len(self.auth_secret_key)} chars; "
                "use at least 32 (secrets.token_urlsafe(48) produces 64)."
            )
        return errors

    @property
    def db_target(self) -> str:
        """Human-readable DB target for logs/health, e.g. 'localhost:5432/StudyCompanion'."""
        if not self.database_url:
            return "unset"
        parsed = urlparse(self.database_url)
        return f"{parsed.hostname or '?'}:{parsed.port or 5432}/{(parsed.path or '/').lstrip('/') or '?'}"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def resolved_frontend_dir(self) -> Path | None:
        """Built frontend to serve, if one exists. See `frontend_dir`."""
        candidates: list[Path] = []
        if self.frontend_dir is not None:
            candidates.append(Path(self.frontend_dir))
        root = Path(__file__).resolve().parents[1]
        candidates.append(root / "frontend" / "dist")
        candidates.append(root / "frontend" / "build")
        for candidate in candidates:
            if (candidate / "index.html").is_file():
                return candidate
        return None

    def upload_limit_mb(self, source_type: str) -> int:
        """
        Per-type ceiling in MB. Takes the stored format id (`video`, `audio`,
        `pdf`, ...), which is what `app/storage.py` puts in `source_type`.
        """
        from .storage import media_kind

        kind = media_kind(source_type)
        if kind == "video" and source_type in ("video",):
            return self.max_video_mb
        if source_type == "audio":
            return self.max_audio_mb
        return self.max_upload_mb

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Singleton accessor — `.env` is parsed exactly once per process."""
    return Settings()
