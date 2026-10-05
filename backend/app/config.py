"""
Central configuration — every tunable lives here and is read from `backend/.env`.

Why pydantic-settings?
    Types are validated at boot, so a typo in `.env` fails loudly at startup
    instead of silently mid-demo. Every field below maps to an UPPER_CASE
    environment variable (e.g. `llm_base_url` <- `LLM_BASE_URL`).

Provider swapping (the whole point of this file):
    LM Studio : LLM_BASE_URL=http://localhost:1234/v1   LLM_API_KEY=lm-studio
    Groq      : LLM_BASE_URL=https://api.groq.com/openai/v1   LLM_API_KEY=gsk_...
    OpenAI    : LLM_BASE_URL=https://api.openai.com/v1   LLM_API_KEY=sk-...
    Nothing else in the codebase knows or cares which one is active.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_model: str = "local-model"
    llm_temperature: float = 0.1       # low = faithful to the source material
    llm_max_tokens: int = 1024

    # ------------------------------------------------------------ Embeddings
    # "openai" -> an OpenAI-compatible /embeddings endpoint (LM Studio exposes
    #             one; OpenAI obviously does; Groq does NOT).
    # "local"  -> all-MiniLM-L6-v2 running on CPU via Chroma's bundled ONNX
    #             runtime. Zero API keys, works offline. Ideal with Groq.
    embedding_provider: Literal["openai", "local"] = "local"
    embedding_base_url: str | None = None   # falls back to llm_base_url
    embedding_api_key: str | None = None    # falls back to llm_api_key
    embedding_model: str = "text-embedding-3-small"
    embedding_batch_size: int = 64

    # ------------------------------------------------------------- Firecrawl
    # POST /v2/parse accepts a raw PDF upload and, with `pages: true`, returns
    # markdown *per physical page* — that is where our exact citations come from.
    firecrawl_api_key: str | None = None
    firecrawl_api_url: str = "https://api.firecrawl.dev"
    firecrawl_max_pages: int = 100          # Firecrawl bills 1 credit / page
    # "auto"      -> Firecrawl when a key is set, else local PyMuPDF
    # "firecrawl" -> Firecrawl only (fails loudly if unavailable)
    # "local"     -> PyMuPDF only (fully offline)
    parser: Literal["auto", "firecrawl", "local"] = "auto"

    # --------------------------------------------------------------- Storage
    data_dir: Path = Path("./data")          # uploads/, chroma/, app.sqlite3
    collection_name: str = "study_materials"

    # ------------------------------------------------------------------ RAG
    top_k: int = 6                 # chunks pulled into the prompt
    # Cosine *distance* (1 - similarity). If even the best chunk is farther
    # than this, we refuse before spending an LLM call. Tune per embedding
    # model: MiniLM ~0.75-0.85, text-embedding-3 ~0.7-0.8. Set 2.0 to disable.
    max_distance: float = 0.8
    chunk_size: int = 1200         # characters, never crosses a page boundary
    chunk_overlap: int = 200
    history_turns: int = 6         # prior messages forwarded to the LLM

    # ------------------------------------------------ Learner model (BKT)
    # Standard Bayesian Knowledge Tracing priors. p_guess=0.25 because MCQs
    # have four options.
    bkt_p_init: float = 0.30
    bkt_p_transit: float = 0.15
    bkt_p_slip: float = 0.10
    bkt_p_guess: float = 0.25
    quiz_questions: int = 3
    max_topics: int = 8

    # ------------------------------------------------------------------ API
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_mb: int = 50        # Firecrawl's own ceiling is 50 MB

    # ------------------------------------------------------------ Derived
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "app.sqlite3"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Singleton accessor — `.env` is parsed exactly once per process."""
    return Settings()
