"""
Provider-agnostic LLM and embedding clients.

Design principle
----------------
Everything speaks the OpenAI wire protocol. The official `openai` SDK is
instantiated with a *dynamic* `base_url` + `api_key` taken from `.env`, so
swapping LM Studio <-> Groq <-> OpenAI is a config change, never a code change.

Two independent clients are built because chat and embeddings often come from
different places (e.g. Groq for fast Llama inference + OpenAI for embeddings,
since Groq has no /embeddings endpoint). Transcription lives in
`app/transcription.py` and follows the same per-task base_url/api_key/model
pattern.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Callable, Protocol, TypeVar

import httpx
from openai import BadRequestError, OpenAI
from pydantic import BaseModel

from .config import Settings

log = logging.getLogger(__name__)
TStructured = TypeVar("TStructured", bound=BaseModel)

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
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Plain text completion over an explicit message list."""
        if on_chunk is not None:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=max_tokens or self.max_tokens,
                stream=True,
            )
            parts: list[str] = []
            for event in stream:
                delta = event.choices[0].delta.content or ""
                if delta:
                    parts.append(delta)
                    on_chunk(delta)
            return strip_think("".join(parts))
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        content = response.choices[0].message.content or ""
        return strip_think(content)

    def complete(self, system: str, user: str | list[dict], **kwargs) -> str:
        return self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )

    def complete_json(
        self, system: str, user: str | list[dict], *, max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> dict:
        """
        Ask for a JSON object and parse it defensively. We deliberately do NOT
        rely on `response_format=json_object` because not every OpenAI-compatible
        server implements it; a strong instruction + tolerant parser is portable.
        """
        system_json = system + "\n\nRespond with ONE valid JSON object and nothing else — no prose, no code fences."
        if json_mode:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_json}, {"role": "user", "content": user}],
                temperature=0.0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            choices = getattr(response, "choices", None)
            if not choices:
                raise ValueError(f"JSON completion from {self.base_url} returned no choices")
            raw = choices[0].message.content
            if not raw or not raw.strip():
                raise ValueError(f"JSON completion from {self.base_url} returned empty content")
        else:
            raw = self.complete(system_json, user, temperature=0.0, max_tokens=max_tokens)
        return extract_json(raw)

    def complete_structured(
        self, system: str, user: str | list[dict], *, response_model: type[TStructured],
        max_tokens: int = 2048,
    ) -> TStructured:
        """Try schema, JSON, then portable JSON prompting; validate every response."""
        schema = response_model.model_json_schema()
        schema_name = re.sub(r"[^a-zA-Z0-9_-]", "_", response_model.__name__)[:64]
        instruction = system + "\nReturn ONE JSON object matching this schema, without prose:\n" + json.dumps(schema)
        formats = [
            {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            {"type": "json_object"},
            None,
        ]
        last_error = None
        for response_format in formats:
            kwargs = {"response_format": response_format} if response_format else {}
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": instruction}, {"role": "user", "content": user}],
                    temperature=0.0, max_tokens=max_tokens, **kwargs,
                )
            except BadRequestError as exc:
                # Gateways may not implement response_format. Authentication,
                # rate limits and network failures are not format fallbacks.
                detail = str(exc).lower()
                if response_format and any(word in detail for word in ("response_format", "json_schema", "json_object", "structured", "schema")):
                    last_error = exc
                    continue
                raise
            choices = getattr(response, "choices", None)
            if not choices:
                last_error = ValueError("The model returned no completion.")
                continue
            message = choices[0].message
            if getattr(message, "refusal", None):
                raise ValueError("The model could not generate study material from these sources.")
            try:
                return response_model.model_validate(extract_json(message.content or ""))
            except (ValueError, TypeError) as exc:
                last_error = exc
                log.warning("Invalid structured completion; trying the next supported format")
        raise ValueError("The model returned incomplete or invalid JSON. Please retry or use a model with structured-output support.") from last_error


# =============================================================================
#  Embeddings
# =============================================================================
class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_image(self, image: bytes, mime: str) -> list[float]: ...


class OpenAICompatibleEmbedder:
    """Calls an OpenAI-style /embeddings endpoint (OpenAI, LM Studio, vLLM, ...)."""

    def __init__(self, base_url: str, api_key: str, model: str, batch_size: int = 64):
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
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

    def embed_image(self, image: bytes, mime: str) -> list[float]:
        """Send one image as a data URI to an image-aware embedding endpoint.

        This is a provider extension, not part of the OpenAI embeddings
        contract. The selected server/model must decode the data URI as an
        image; a text-only server may accept it while embedding the URI text.
        """
        data_url = f"data:{mime};base64," + base64.b64encode(image).decode("ascii")
        # Keep the data URI as input; several OpenAI-compatible servers reject
        # llama.cpp's non-standard multimodal content-array payload.
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": data_url},
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json().get("data") or []
        if not data or not data[0].get("embedding"):
            raise ValueError("Embedding endpoint returned no vector for the image")
        return data[0]["embedding"]


def build_embedder(settings: Settings) -> Embedder:
    """Factory: picks the embedding backend from `.env`.

    Endpoint comes from the *resolved* settings, so embeddings can live on a
    different server than chat (Groq has no /embeddings endpoint, so Groq chat
    + OpenAI embeddings is the common pairing). Unset values inherit the LLM
    provider, which keeps a single-server setup a three-line .env.
    """
    base_url = settings.resolved_embedding_base_url
    api_key = settings.resolved_embedding_api_key
    return OpenAICompatibleEmbedder(base_url, api_key, settings.embedding_model, settings.embedding_batch_size)
