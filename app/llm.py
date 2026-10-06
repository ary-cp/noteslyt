"""
Provider-agnostic LLM and embedding clients.

Design principle
----------------
Everything speaks the OpenAI wire protocol. The official `openai` SDK is
instantiated with a *dynamic* `base_url` + `api_key` taken from `.env`, so
swapping LM Studio <-> Groq <-> OpenAI is a config change, never a code change.

Two independent clients are built because chat and embeddings often come from
different places (e.g. Groq for fast Llama inference + local MiniLM for
embeddings, since Groq has no /embeddings endpoint).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from openai import OpenAI

from .config import Settings

log = logging.getLogger(__name__)

# Reasoning models (DeepSeek-R1, Qwen3 in LM Studio, ...) wrap their chain of
# thought in <think> tags. We never want that in a student's chat window.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def extract_json(text: str) -> dict:
    """
    Robust JSON extraction: tolerates ```json fences, chatty preambles and
    trailing commentary. Local models are not always well-behaved.
    """
    cleaned = strip_think(text)
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned.strip(), flags=re.IGNORECASE | re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"No JSON object found in LLM output: {text[:200]!r}")
        return json.loads(cleaned[start : end + 1])


# =============================================================================
#  Chat LLM
# =============================================================================
class LLMClient:
    """Thin wrapper over `openai.OpenAI(...).chat.completions`."""

    def __init__(self, settings: Settings):
        self._client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens
        self.base_url = settings.llm_base_url

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Plain text completion over an explicit message list."""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        content = response.choices[0].message.content or ""
        return strip_think(content)

    def complete(self, system: str, user: str, **kwargs) -> str:
        return self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )

    def complete_json(self, system: str, user: str, *, max_tokens: int = 2048) -> dict:
        """
        Ask for a JSON object and parse it defensively. We deliberately do NOT
        rely on `response_format=json_object` because not every OpenAI-compatible
        server implements it; a strong instruction + tolerant parser is portable.
        """
        system_json = system + "\n\nRespond with ONE valid JSON object and nothing else — no prose, no code fences."
        raw = self.complete(system_json, user, temperature=0.0, max_tokens=max_tokens)
        return extract_json(raw)


# =============================================================================
#  Embeddings
# =============================================================================
class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedder:
    """Calls an OpenAI-style /embeddings endpoint (OpenAI, LM Studio, vLLM, ...)."""

    def __init__(self, base_url: str, api_key: str, model: str, batch_size: int = 64):
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.batch_size = batch_size
        self.name = f"openai-compatible:{model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t if t.strip() else " " for t in texts[i : i + self.batch_size]]
            response = self._client.embeddings.create(model=self.model, input=batch)
            # The API may return items out of order; sort by index to be safe.
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors


class LocalEmbedder:
    """
    all-MiniLM-L6-v2 (384-dim) via the ONNX runtime that ships with ChromaDB.
    No API key, CPU-only, ~80 MB one-time download on first use. This is what
    lets the demo run with Groq (no embeddings API) or fully offline.
    """

    name = "local:all-MiniLM-L6-v2"

    def __init__(self):
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        self._fn = DefaultEmbeddingFunction()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [[float(x) for x in vec] for vec in self._fn(texts)]  # type: ignore[arg-type]


def build_embedder(settings: Settings) -> Embedder:
    """Factory: picks the embedding backend from `.env`."""
    if settings.embedding_provider == "local":
        log.info("Embeddings: local MiniLM (ONNX, CPU)")
        return LocalEmbedder()
    base_url = settings.embedding_base_url or settings.llm_base_url
    api_key = settings.embedding_api_key or settings.llm_api_key
    log.info("Embeddings: %s @ %s", settings.embedding_model, base_url)
    return OpenAICompatibleEmbedder(base_url, api_key, settings.embedding_model, settings.embedding_batch_size)
