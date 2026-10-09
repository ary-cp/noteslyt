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
from openai import OpenAI
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

    def complete(self, system: str, user: str, **kwargs) -> str:
        return self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 2048,
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
        self, system: str, user: str, *, response_model: type[TStructured],
        max_tokens: int = 2048,
    ) -> TStructured:
        """Request JSON Schema output, then validate it into a Pydantic model.

        Some OpenAI-compatible gateways return a successful HTTP envelope with
        `choices: null`; the SDK's `.parse()` helper crashes while iterating it.
        Read the normal completion response first so we can handle that case
        and still validate any usable fallback response with Pydantic.
        """
        schema_name = re.sub(r"[^a-zA-Z0-9_-]", "_", response_model.__name__)[:64]
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            },
        )
        choices = getattr(response, "choices", None)
        if choices:
            message = choices[0].message
            if message.refusal:
                raise ValueError(f"The model refused structured output: {message.refusal}")
            raw = message.content or ""
            if raw:
                try:
                    return response_model.model_validate_json(raw)
                except Exception as exc:
                    log.warning("Structured response failed Pydantic validation: %s", exc)
        else:
            log.warning("Structured response from %s had no choices; trying validated JSON mode", self.base_url)

        # Provider returned no usable schema response. JSON mode is a fallback
        # only; Pydantic still rejects missing or incorrectly typed fields.
        fallback = self.complete_json(
            system + "\nMatch the required response schema exactly.",
            user,
            max_tokens=max_tokens,
            json_mode=True,
        )
        return response_model.model_validate(fallback)


# =============================================================================
#  Embeddings
# =============================================================================
class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_image(self, image: bytes, mime: str) -> list[float]: ...

    def embed_audio(self, audio: bytes, mime: str) -> list[float]: ...


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

    def embed_audio(self, audio: bytes, mime: str) -> list[float]:
        """Send one audio slice as a data URI to an audio-aware embedding endpoint.

        Same provider-extension pattern as `embed_image`: the selected
        server/model must decode the data URI as audio *into the same vector
        space as text*. A text-only server may 200 while embedding the URI
        text — vectors then look valid but retrieval is meaningless. Use
        `AUDIO_EMBED_MODE=native` only with a joint audio-text model.
        """
        data_url = f"data:{mime};base64," + base64.b64encode(audio).decode("ascii")
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": data_url},
            timeout=180.0,
        )
        response.raise_for_status()
        data = response.json().get("data") or []
        if not data or not data[0].get("embedding"):
            raise ValueError("Embedding endpoint returned no vector for the audio")
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
