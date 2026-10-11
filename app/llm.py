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
import math
import re
from typing import Any, Callable, Protocol, TypeVar

import httpx
from openai import BadRequestError, InternalServerError, OpenAI
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

    def embed_audio(self, audio: bytes, mime: str) -> list[float]: ...

    def embed_video(self, video: bytes, mime: str) -> list[float]: ...


class OpenAICompatibleEmbedder:
    """Calls an OpenAI-style /embeddings endpoint (OpenAI, LM Studio, vLLM, ...)."""

    def __init__(self, base_url: str, api_key: str, model: str, batch_size: int = 64,
                 max_input_chars: int = 1500):
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.batch_size = batch_size
        self.max_input_chars = max_input_chars
        self.name = f"openai-compatible:{model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Expand over-long inputs into sub-pieces first: endpoints reject a
        # single input past their token/batch ceiling (llama-server 500s past
        # its physical batch), which would otherwise fail the whole batch.
        expanded: list[str] = []
        owner: list[int] = []
        for i, t in enumerate(texts):
            parts = _split_long(t, self.max_input_chars) if len(t) > self.max_input_chars else [t]
            for p in parts:
                owner.append(i)
                expanded.append(p)
        piece_vecs: list[list[float]] = []
        for i in range(0, len(expanded), self.batch_size):
            batch = [t if t.strip() else " " for t in expanded[i : i + self.batch_size]]
            try:
                response = self._client.embeddings.create(model=self.model, input=batch)
            except (BadRequestError, InternalServerError) as exc:
                if not _is_too_large_error(exc):
                    raise
                # Even a pre-split piece can overflow a tiny server batch
                # (dense scripts pack more tokens per character): halve each
                # piece recursively and mean-pool instead of failing the batch.
                for t in batch:
                    piece_vecs.append(self._embed_small(t, depth=0))
                continue
            # The API may return items out of order; sort by index to be safe.
            piece_vecs.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        grouped: dict[int, list[list[float]]] = {}
        for o, v in zip(owner, piece_vecs):
            grouped.setdefault(o, []).append(v)
        return [_mean_pool(grouped[i]) for i in range(len(texts))]

    def _embed_small(self, text: str, depth: int) -> list[float]:
        """Embed one piece, halving past endpoint length limits (then pooling)."""
        if depth > 6 or len(text) <= 64:
            # Small enough to blame the endpoint, not the input: let it raise.
            response = self._client.embeddings.create(
                model=self.model, input=[text if text.strip() else " "]
            )
            return list(response.data[0].embedding)
        mid = len(text) // 2
        # Split near the middle on a sentence/line boundary when possible.
        cut = -1
        for m in re.finditer(r"\n+|(?<=[.!?।])\s+", text):
            if m.start() <= mid:
                cut = m.end()
            else:
                break
        cut = cut if cut > 0 else mid
        halves = [text[:cut], text[cut:]]
        nonblank = [h for h in halves if h.strip()] or [" "]
        return _mean_pool([self._embed_small(h, depth + 1) for h in nonblank])

    def _post_media(self, content: list[dict], timeout: float, what: str) -> list[float]:
        """POST one multimodal embedding request in llama.cpp's nested shape.

        Per ggml-org/llama.cpp#30082, media parts must nest one level deeper
        than OpenAI's chat format: each `input` item is `{"content": [...]}`.
        A flat data-URI string is tokenized as text (61k tokens for 2 s of
        audio) and a bare parts array is a 400 — both verified live. Anything
        without a flat float vector raises instead of returning garbage.
        """
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": [{"content": content}]},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json().get("data") or []
        emb = data[0].get("embedding") if data else None
        if not isinstance(emb, list) or not emb or not isinstance(emb[0], (int, float)):
            raise ValueError(f"Embedding endpoint returned no usable vector for the {what}")
        return [float(x) for x in emb]

    def embed_image(self, image: bytes, mime: str) -> list[float]:
        """Send one image for encoding into the shared space (verified 0.88 red-vs-blue)."""
        data_url = f"data:{mime};base64," + base64.b64encode(image).decode("ascii")
        return self._post_media(
            [{"type": "image_url", "image_url": {"url": data_url}}], 120.0, "image"
        )

    def embed_audio(self, audio: bytes, mime: str) -> list[float]:
        """Send one audio slice for encoding into the shared space.

        Verified live: determinism 1.0, sine-vs-noise 0.83, noise-vs-chord
        0.80. Pure tones cluster (~0.95) — the encoder hears content, and
        content-free sines have little to separate. Requires a joint model
        served with its projector (e.g. embeddinggemma-2-GGUF + mmproj under
        llama-server); anything else fails loudly here or at boot.
        """
        fmt = mime.split("/")[-1].lower() or "wav"
        data_b64 = base64.b64encode(audio).decode("ascii")
        return self._post_media(
            [{"type": "input_audio", "input_audio": {"data": data_b64, "format": fmt}}],
            180.0,
            "audio",
        )

    def embed_video(self, video: bytes, mime: str) -> list[float]:
        """Send one video clip for encoding into the shared space.

        Verified live on embeddinggemma-2 + mmproj under llama-server (2 s
        clip: 768-d, determinism 1.0, cross-modal 0.62 with its description).
        Token cost is steep (~500 tokens/s at 640 px), so only short clips fit
        the server batch — longer inputs 500 and the caller falls back.
        """
        fmt = mime.split("/")[-1].lower() or "mp4"
        data_b64 = base64.b64encode(video).decode("ascii")
        return self._post_media(
            [{"type": "input_video", "input_video": {"data": data_b64, "format": fmt}}],
            300.0,
            "video",
        )

def _split_long(text: str, budget: int) -> list[str]:
    """Split over-long input on paragraph/sentence boundaries (≤ budget chars).

    Falls back to hard cuts for runs without boundaries (dense tables, URLs).
    Devanagari danda counts as a sentence end for Hindi-heavy chunks.
    """
    if len(text) <= budget:
        return [text]
    pieces: list[str] = []
    current = ""
    for part in re.split(r"(\n+|(?<=[.!?।])\s+)", text):
        if not part:
            continue
        if not current.strip() or len(current) + len(part) <= budget:
            current += part
        else:
            pieces.append(current)
            current = part
    if current:
        pieces.append(current)
    out: list[str] = []
    for p in pieces:
        while len(p) > budget:
            out.append(p[:budget])
            p = p[budget:]
        if p.strip():
            out.append(p)
    return out or [text[:budget]]


def _is_too_large_error(exc: Exception) -> bool:
    """True when the endpoint refused an input for length (not auth/quota)."""
    detail = str(exc).lower()
    code = str(getattr(exc, "code", "") or "").lower()
    return (
        "too large" in detail
        or "context_length_exceeded" in code
        or "max_tokens" in detail
        or ("batch" in detail and ("large" in detail or "size" in detail or "exceed" in detail))
    )


def _mean_pool(vecs: list[list[float]]) -> list[float]:
    """Average sub-piece vectors into one L2-normalized vector per input."""
    if len(vecs) == 1:
        return [float(x) for x in vecs[0]]
    dim = len(vecs[0])
    avg = [0.0] * dim
    for v in vecs:
        if len(v) != dim:
            raise ValueError(f"Cannot pool vectors of mixed widths {[len(x) for x in vecs]}.")
        for j, x in enumerate(v):
            avg[j] += x
    n = len(vecs)
    avg = [x / n for x in avg]
    norm = math.sqrt(sum(x * x for x in avg))
    if norm > 0:
        avg = [x / norm for x in avg]
    return avg


def verify_native_support(embedder: Embedder) -> None:
    """Boot guard for native audio/video embedding modes.

    Embeds two maximally different blobs and refuses to boot when they come
    back near-identical — the signature of an engine that tokenizes the
    data URI as text instead of encoding bytes (LM Studio's text-only
    /embeddings endpoint scores ~0.97). Silent corruption is worse than a
    loud boot error; run `scripts/probe_multimodal.py` for the full report.
    """
    import math
    import struct
    import wave as _wave
    import io as _io

    def sine_wav(freq: float) -> bytes:
        buf = _io.BytesIO()
        with _wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            n = 16000 * 2
            w.writeframes(b"".join(
                struct.pack("<h", int(9000 * math.sin(2 * math.pi * freq * i / 16000)))
                for i in range(n)
            ))
        return buf.getvalue()

    if not hasattr(embedder, "embed_audio"):
        raise RuntimeError(
            "Native embedding modes need an embedder with byte inputs; "
            "the configured backend only embeds text."
        )
    a = embedder.embed_audio(sine_wav(440.0), "audio/wav")
    # A fixed pseudo-noise buffer: maximally different bytes, no dependency.
    rng_state = 12345

    def noise() -> bytes:
        nonlocal rng_state
        out = bytearray()
        for _ in range(16000 * 2):
            rng_state = (1103515245 * rng_state + 12345) & 0x7FFFFFFF
            out += struct.pack("<h", (rng_state % 20001) - 10000)
        buf = _io.BytesIO()
        with _wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(bytes(out))
        return buf.getvalue()

    b = embedder.embed_audio(noise(), "audio/wav")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    similarity = dot / (na * nb)
    if similarity >= 0.90:
        raise RuntimeError(
            f"Native embedding modes are enabled but the embedding endpoint encodes "
            f"bytes as text (two different audio blobs: cosine {similarity:.4f}). "
            f"Set AUDIO_EMBED_MODE=transcript (and VIDEO_EMBED_MODE=transcript), or serve "
            f"a joint model from llama.cpp and re-run scripts/probe_multimodal.py."
        )


def build_embedder(settings: Settings) -> Embedder:
    """Factory: picks the embedding backend from `.env`.

    Endpoint comes from the *resolved* settings, so embeddings can live on a
    different server than chat (Groq has no /embeddings endpoint, so Groq chat
    + OpenAI embeddings is the common pairing). Unset values inherit the LLM
    provider, which keeps a single-server setup a three-line .env.
    """
    if settings.embedding_mode == "local":
        from .llama_embed import ManagedEmbedder, get_embedding_manager

        return ManagedEmbedder(get_embedding_manager(), settings.embedding_model,
                               settings.embedding_batch_size, settings.embedding_max_input_chars)
    base_url = settings.resolved_embedding_base_url
    api_key = settings.resolved_embedding_api_key
    return OpenAICompatibleEmbedder(base_url, api_key, settings.embedding_model,
                                    settings.embedding_batch_size, settings.embedding_max_input_chars)
