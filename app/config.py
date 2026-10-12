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
from pydantic import field_validator

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
    # Longest single input sent to /embeddings, in characters. Longer chunk
    # texts are split on sentence boundaries and mean-pooled, because the
    # endpoint rejects one input over its physical batch (llama-server's
    # default 512 tokens 500s on ~641-token table chunks; dense scripts run
    # more tokens per character, so this stays conservative).
    embedding_max_input_chars: int = 1500
    embedding_dim: int = 768             # must match the model + pgvector column
    # Where vectors come from:
    #   "local" — the default. A vendored llama-server (downloaded to
    #             EMBEDDING_LOCAL_DIR on first boot) serves the joint model
    #             below, with wake-on-API and idle unload. No provider
    #             endpoint needed; EMBEDDING_*_URL/KEY are ignored.
    #   "cloud" — classic behaviour: EMBEDDING_BASE_URL/API_KEY/MODEL win.
    # Scripts without an app lifespan (eval, mocks) must use cloud.
    embedding_mode: Literal["local", "cloud"] = "local"
    # Vendored engine + weights layout (override the model paths to use your
    # own files instead of the managed download):
    #   <dir>/engine/   llama-server build + BUILD_TAG
    #   <dir>/models/   weights + mmproj projector
    #   <dir>/logs/     llama-server.log
    embedding_local_dir: Path = Path("./data/llama")
    embedding_local_host: str = "127.0.0.1"
    embedding_local_port: int = 8899
    embedding_local_ctx: int = 8192
    # Physical batch (llama.cpp -ub/--ubatch-size, default 512): the longest
    # single input the server accepts. Raised so ordinary chunks embed in one
    # call; the client still splits anything longer (see above). Note the
    # server caps ubatch at the logical batch below, so raise both together.
    embedding_local_ubatch_size: int = 2048
    # Video sampling rate for the embedding endpoint (llama.cpp --video-fps).
    # Default 1.0: at 1 fps a 20 s clip costs ~1.7k tokens (~84/frame at
    # 320 px), fitting ubatch 2048; at 4.0 the same clip costs ~6.6k.
    # Still overrideable via EMBEDDING_LOCAL_VIDEO_FPS.
    embedding_local_video_fps: float = 1.0
    # Logical batch (llama.cpp -b/--batch-size, default 2048): upper bound for
    # ubatch. Raise only alongside ubatch — it reserves compute buffers and
    # costs RAM whether used or not.
    embedding_local_batch_size: int = 2048
    embedding_local_repo: str = "ggml-org/embeddinggemma-2-GGUF"
    embedding_local_model_file: str = "embeddinggemma-2-Q8_0.gguf"
    embedding_local_mmproj_file: str = "mmproj-embeddinggemma-2-Q8_0.gguf"
    embedding_local_model_path: Path | None = None
    embedding_local_mmproj_path: Path | None = None
    embedding_idle_unload_s: int = 300   # 0 = keep the server up forever (5 min idle unload)
    llama_github_repo: str = "ggml-org/llama.cpp"
    # How audio/video sources are vectorized:
    #   "transcript" — embed the Whisper/transcript text (default; works with any
    #                   text embedding model, including EmbeddingGemma).
    #   "native"     — embed the raw audio bytes of each ~20 s segment via
    #                   `Embedder.embed_audio` (nested input_audio parts, per
    #                   ggml-org/llama.cpp#30082). Needs a joint audio-text
    #                   audio and text vectors share one space, served by an
    #                   engine that really encodes bytes: LM Studio's embedding
    #                   endpoint is text-only and tokenizes the data URI as text
    #                   (verified 2026-10-10: sine-vs-noise cosine 0.97, two
    #                   different images 0.996 — the vectors look valid but
    #                   retrieval is meaningless). Use llama.cpp with a joint
    #                   model (e.g. embeddinggemma-2-GGUF + mmproj) and confirm
    #                   with `uv run python scripts/probe_multimodal.py` first.
    # Switching modes changes the vector space: re-ingest the corpus
    # (`uv run python scripts/re_embed.py --force`) after switching.
    audio_embed_mode: Literal["transcript", "native"] = "transcript"
    # Audio is broken into 3-minute parts carrying BOTH signals (mirrors
    # video): each part's transcript text plus one embedded audio clip of the
    # same timeframe, so retrieval matches what was said and what it sounded
    # like. Parts stay ~180 s so one clip fits the transcription + embedding
    # path; shorter transcript units are grouped up to this window.
    audio_segment_seconds: float = 180.0

    # How lecture-video segments are vectorized:
    #   "transcript" — embed the transcript (+ vision captions when enabled).
    #   "native"     — embed the segment's midpoint keyframe via
    #                   `Embedder.embed_image`, so retrieval matches what is
    #                   *shown*, not just what is said. Same precondition as
    #                   audio native: joint model + byte-encoding engine, proven
    #                   by `scripts/probe_multimodal.py`, then re-ingest.
    # (With AUDIO_EMBED_MODE=native, video falls back to soundtrack vectors;
    # VIDEO_EMBED_MODE=native takes precedence for video and uses frames.)
    video_embed_mode: Literal["transcript", "native"] = "transcript"
    # Video is broken into short parts carrying BOTH signals: each part's
    # transcript text plus one embedded video clip of the same timeframe, so
    # retrieval matches what was said and what was shown. Parts stay ~10 s
    # so one clip fits the embedding batch (see VIDEO_CLIP_* below).
    video_segment_seconds: float = 10.0
    # Middle-clip seconds embedded per segment instead of a still keyframe
    # (needs VIDEO_EMBED_MODE=native + a video-capable embedding endpoint).
    # 0 keeps stills. Proven recipe for full ~10 s parts in one vector:
    # EMBEDDING_LOCAL_VIDEO_FPS=1.0, VIDEO_CLIP_SECONDS=10 (or 0 for the
    # whole part — same thing at 10 s parts), VIDEO_CLIP_WIDTH=720 max
    # (≈1.7k tokens: 10 frames × ~169/frame, fits ubatch 2048, ~2 s on GPU).
    # Wider input is downscaled to the width; narrower passes through, so
    # small originals never pay upscaling tokens. Oversize clips fall back
    # to the still with a warning naming the fix.
    video_clip_seconds: float = 0.0
    video_clip_width: int = 720        # max width px; downscale-only, never upscale

    # --------------------------------------------------------- Transcription
    # Speech-to-text for lecture audio/video. Two modes, mirroring embeddings:
    #   "cloud" — the default. Any OpenAI-compatible /audio/transcriptions
    #             endpoint: Groq (whisper-large-v3-turbo, free tier), OpenAI
    #             (whisper-1), or a self-hosted whisper server. TRANSCRIPTION_*
    #             URL/KEY/MODEL win; nothing is downloaded.
    #   "local" — a vendored audio.cpp server (downloaded to
    #             TRANSCRIPTION_LOCAL_DIR on first boot) serves a local ASR
    #             model (default Qwen3-ASR-0.6B Q8) with wake-on-API and idle
    #             unload. TRANSCRIPTION_*_URL/KEY are ignored in this mode;
    #             TRANSCRIPTION_MODEL selects the server's model id.
    # Scripts without an app lifespan (eval, mocks) must use cloud.
    transcription_mode: Literal["local", "cloud"] = "cloud"
    transcription_base_url: str | None = None   # falls back to llm_base_url (cloud)
    transcription_api_key: str | None = None    # falls back to llm_api_key (cloud)
    transcription_model: str = "whisper-large-v3-turbo"
    # Vendored audio.cpp engine + ASR weights layout (override the model path
    # to use your own files instead of the managed download):
    #   <dir>/engine/   audio.cpp build + BUILD_TAG
    #   <dir>/models/   ASR weights (GGUF directory or single file)
    #   <dir>/logs/     audiocpp-server.log
    #   <dir>/server.json  generated server config (host/port/backend/models)
    transcription_local_dir: Path = Path("./data/audio-cpp")
    transcription_local_host: str = "127.0.0.1"
    transcription_local_port: int = 8898
    transcription_local_repo: str = "0xShug0/audio.cpp"
    transcription_local_model_repo: str = "audio-cpp/audio.cpp-gguf"
    transcription_local_model_file: str = "Qwen3-ASR-0.6B-GGUF/qwen3-asr-0.6b-q8_0.gguf"
    transcription_local_model_path: Path | None = None
    transcription_local_model_id: str = "qwen3-asr"
    transcription_local_family: str = "qwen3_asr"
    transcription_idle_unload_s: int = 300   # 0 = keep the server up forever (5 min idle unload)
    # Longest audio slice sent in ONE transcription request, in seconds.
    # Must fit the STT model's encoder window, not just the provider upload
    # limit: a 10-min piece against Parakeet TDT fails with
    # "relative position frames exceed maximum" even though 5 min works.
    # Recipe: test your model directly first, then set this at or below what
    # worked (60/120/180/300 for 1/2/3/5 min). Applies to video pieces and to
    # long audio files, which are split into same-sized pieces.
    transcription_chunk_s: float = 300.0
    # BCP-47 language hint for /audio/transcriptions (e.g. "en"). Unset means
    # auto-detect. Some self-hosted STT engines (whisper.cpp-style) reject
    # `verbose_json` without an explicit language — see app/transcription.py,
    # which falls back to `json` in that case.
    transcription_language: str | None = None

    # ------------------------------------------------------- Video keyframes
    # Lecture videos carry a visual track the transcript misses (slides,
    # equations, diagrams). One frame per Nth segment unit is captioned by the
    # vision chat model and the caption joins the unit text — same locator,
    # same chunk, so captions are searchable and citable with any text
    # embedder. Near-duplicate frames (static slides) are skipped by hash.
    # A text-only chat model cannot caption: the first vision failure stops
    # captioning for that ingest with a readable warning, transcript intact.
    video_captions: bool = True
    video_frame_every_n: int = 3        # caption every Nth segment unit
    video_frame_width: int = 640        # px; aspect preserved
    video_frame_hamming: int = 6        # below this distance a frame is a dup
    video_max_frames: int = 120         # per-source caption budget

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
    data_dir: Path = Path("./data")    # Per-type upload ceilings in MB. Video and audio dominate ingest cost
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
    # Visual retrieval: every PDF page is also rendered to PNG and embedded as
    # an image chunk ([Image N]), so charts/diagrams survive even where the
    # text parse loses them. Text chunks are untouched — retrieval matches both.
    # Needs a joint embedding model (embed_image); text-only endpoints record
    # a warning and ingest text-only. Slides need LibreOffice, else text-only.
    pdf_page_images: bool = True
    pdf_page_image_max_pages: int = 200   # per-source render budget (0 = all)
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

    @field_validator("transcription_chunk_s")
    @classmethod
    def _check_transcription_chunk(cls, v: float) -> float:
        if not 30.0 <= float(v) <= 1800.0:
            raise ValueError(
                f"TRANSCRIPTION_CHUNK_S={v} is outside 30–1800 s. "
                "Below 30 s wastes calls; above 30 min exceeds every STT encoder window."
            )
        return v

    @field_validator("audio_segment_seconds")
    @classmethod
    def _check_audio_segment(cls, v: float) -> float:
        if not 15.0 <= float(v) <= 600.0:
            raise ValueError(
                f"AUDIO_SEGMENT_SECONDS={v} is outside 15–600 s. "
                "Use 60/120/180/300 for 1/2/3/5 min parts."
            )
        return v

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
    def embedding_local_base_url(self) -> str:
        return f"http://{self.embedding_local_host}:{self.embedding_local_port}/v1"

    @property
    def resolved_embedding_base_url(self) -> str:
        if self.embedding_mode == "local":
            return self.embedding_local_base_url
        return self.embedding_base_url or self.llm_base_url

    @property
    def resolved_embedding_api_key(self) -> str:
        if self.embedding_mode == "local":
            return "local"
        return self.embedding_api_key or self.llm_api_key

    @property
    def transcription_local_base_url(self) -> str:
        return f"http://{self.transcription_local_host}:{self.transcription_local_port}/v1"

    @property
    def resolved_transcription_base_url(self) -> str:
        if self.transcription_mode == "local":
            return self.transcription_local_base_url
        return self.transcription_base_url or self.llm_base_url

    @property
    def resolved_transcription_api_key(self) -> str:
        if self.transcription_mode == "local":
            return "local"
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
            if self.transcription_mode == "local":
                return {
                    "base_url": self.transcription_local_base_url,
                    "api_key": "local",
                    "model": self.transcription_local_model_id,
                }
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
        if self.embedding_mode == "local" and self.embedding_base_url:
            warnings.append(
                "EMBEDDING_MODE=local ignores EMBEDDING_BASE_URL/API_KEY/MODEL — "
                "vectors come from the vendored server. Unset them or switch "
                "to EMBEDDING_MODE=cloud to avoid confusion."
            )
        if self.transcription_mode == "local" and self.transcription_base_url:
            warnings.append(
                "TRANSCRIPTION_MODE=local ignores TRANSCRIPTION_BASE_URL/API_KEY — "
                "transcripts come from the vendored audio.cpp server. Unset them or switch "
                "to TRANSCRIPTION_MODE=cloud to avoid confusion."
            )
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
