"""
Speech-to-text client. Follows the same provider-agnostic pattern as `llm.py`:
any OpenAI-compatible `/audio/transcriptions` endpoint works — Groq (free
whisper-large-v3-turbo), OpenAI, or a self-hosted whisper server, selected
purely through `.env` (`TRANSCRIPTION_*`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from .config import Settings


@dataclass
class Segment:
    start: float   # seconds
    end: float
    text: str


class Transcriber:
    name: str

    def __init__(self, base_url: str, api_key: str, model: str):
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.base_url = base_url
        self.name = f"openai-compatible:{model}"

    def transcribe(self, audio_path: Path) -> list[Segment]:
        with audio_path.open("rb") as fh:
            result = self._client.audio.transcriptions.create(
                model=self.model,
                file=fh,
                response_format="verbose_json",
            )
        raw = getattr(result, "segments", None) or []
        return [
            Segment(start=float(s.get("start", 0.0) if isinstance(s, dict) else getattr(s, "start", 0.0)),
                    end=float(s.get("end", 0.0) if isinstance(s, dict) else getattr(s, "end", 0.0)),
                    text=str(s.get("text", "") if isinstance(s, dict) else getattr(s, "text", "")))
            for s in raw
        ]


def build_transcriber(settings: Settings) -> Transcriber:
    """Speech-to-text endpoint, resolved independently of the chat provider.

    Falls back to the LLM endpoint when `TRANSCRIPTION_BASE_URL`/`_API_KEY` are
    unset (handy when one server hosts both), but a Groq chat model with OpenAI
    Whisper — or Groq chat + Groq Whisper while embeddings sit on OpenAI — is
    just three env vars.
    """
    base_url = settings.resolved_transcription_base_url
    api_key = settings.resolved_transcription_api_key
    return Transcriber(base_url, api_key, settings.transcription_model)
