"""
Speech-to-text client. Follows the same provider-agnostic pattern as `llm.py`:
any OpenAI-compatible `/audio/transcriptions` endpoint works — Groq (free
whisper-large-v3-turbo), OpenAI, or a self-hosted whisper server, selected
purely through `.env` (`TRANSCRIPTION_*`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from .config import Settings


@dataclass
class Segment:
    start: float   # seconds
    end: float
    text: str


def _parse_timestamp(ts: str) -> float:
    """Parse `HH:MM:SS,mmm`, `HH:MM:SS.mmm`, `MM:SS.mmm` or seconds."""
    ts = ts.strip().replace(",", ".")
    if ":" not in ts:
        try:
            return float(ts)
        except ValueError:
            return 0.0
    parts = ts.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    except ValueError:
        return 0.0
    return 0.0


def parse_srt_vtt(text: str) -> list[Segment]:
    """Parse SRT/VTT transcript text into timestamped segments.

    Both formats use `start --> end` lines followed by caption text; VTT
    just adds a `WEBVTT` header. Returns [] when nothing parses, so the
    caller can fall back to a single plain segment.
    """
    segments: list[Segment] = []
    # Split on blank lines into caption blocks.
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        # Drop WEBVTT header / NOTE / cue identifiers.
        if len(lines) == 1 and (
            lines[0].upper().startswith("WEBVTT") or lines[0].upper().startswith("NOTE")
        ):
            continue
        arrow_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if arrow_idx is None:
            continue
        times = lines[arrow_idx].split("-->")
        if len(times) != 2:
            continue
        start = _parse_timestamp(times[0])
        end = _parse_timestamp(times[1])
        caption = " ".join(lines[arrow_idx + 1 :]).strip()
        if not caption:
            continue
        segments.append(Segment(start=start, end=end, text=caption))
    return segments


def audio_duration_s(path: Path) -> float:
    """Duration of an audio/video file in seconds via ffprobe, 0.0 if unknown."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # ffmpeg-only installs still ship no ffprobe on Windows; try ffmpeg -i.
        return 0.0
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, timeout=60, check=False,
        )
        return float((proc.stdout or b"").decode().strip() or 0.0)
    except Exception:
        return 0.0


def synthesize_segments(text: str, audio_path: Path, target_s: float = 20.0) -> list[Segment]:
    """Spread plain transcript text over the audio duration as VTT-like cues.

    Used only when the STT engine returns `json`/`text` with no timestamps
    (like your local server): sentences are grouped to ~20 s / ~350 chars
    (mirrors the `parsers_media` merge target) and each group gets
    `start`/`end` interpolated by character share, so downstream units cite
    `[0:00]`, `[0:18]`, `[0:37]`, … instead of minute-long blobs.
    When the duration is unknown, it is estimated from word count (~150 wpm).
    """
    text = " ".join(text.split())
    if not text:
        return []
    duration = audio_duration_s(audio_path)
    if duration <= 0:
        words = max(len(text.split()), 1)
        duration = words / 150.0 * 60.0
    # Split into sentences, keep delimiters.
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    sentences = [s.strip() for s in sentences if s.strip()] or [text]
    # Group sentences to ~350 chars (~20 s at ~150 wpm).
    groups: list[str] = []
    buf = ""
    for sent in sentences:
        candidate = (buf + " " + sent).strip() if buf else sent
        if buf and len(candidate) > 350:
            groups.append(buf)
            buf = sent
        else:
            buf = candidate
    if buf:
        groups.append(buf)
    total_chars = sum(len(g) for g in groups) or 1
    segments: list[Segment] = []
    cursor = 0.0
    for i, group in enumerate(groups):
        share = len(group) / total_chars
        span = max(share * duration, 1.0)
        start = cursor if i > 0 else 0.0
        end = min(cursor + span, duration) if i < len(groups) - 1 else duration
        segments.append(Segment(start=round(start, 2), end=round(end, 2), text=group))
        cursor = end
    return segments


class Transcriber:
    name: str

    def __init__(self, base_url: str, api_key: str, model: str, language: str | None = None):
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.base_url = base_url
        self.language = language
        self.name = f"openai-compatible:{model}"
        # Remembered server quirk (see transcribe): once verbose_json without
        # a language fails, skip it on every later call instead of replaying
        # a doomed request (+ the SDK's own retries) per audio piece.
        self._needs_language = False

    def _create(self, audio_path: Path, response_format: str, language: str | None):
        with audio_path.open("rb") as fh:
            kwargs: dict = {
                "model": self.model,
                "file": fh,
                "response_format": response_format,
            }
            if language:
                kwargs["language"] = language
            return self._client.audio.transcriptions.create(**kwargs)

    @staticmethod
    def _format_error(exc: Exception) -> bool:
        # Only capability errors justify another expensive transcription call.
        status = getattr(exc, "status_code", None)
        message = str(exc).lower()
        return status in (400, 422, 501) and any(term in message for term in (
            "response_format", "verbose_json", "language", "not implemented", "unsupported format",
        ))

    def transcribe(self, audio_path: Path) -> list[Segment]:
        # MOSS does not report detected language for verbose_json/SRT/VTT.
        # JSON preserves automatic language detection, including Hindi lectures.
        preferred = getattr(self, "_working_format", None)
        formats = [preferred] if preferred else (["json", "text"] if "moss" in self.model.lower() else ["verbose_json", "json", "text"])
        if preferred:
            formats += [fmt for fmt in ("json", "text") if fmt != preferred]
        for index, fmt in enumerate(formats):
        fallback_lang = self.language or "en"
        if self._needs_language:
            # Known quirk of this endpoint: verbose_json requires a language.
            # Go straight to the working params — no wasted round trips.
            try:
                result = self._create(audio_path, "verbose_json", fallback_lang)
            except Exception:
                result = self._transcribe_with_timestamps(audio_path, fallback_lang)
            return self._to_segments(result, audio_path)
        try:
            result = self._create(audio_path, "verbose_json", self.language)
        except Exception as exc:
            # Some self-hosted engines (e.g. whisper.cpp-style servers like
            # LM Studio's local STT) answer 501 to `verbose_json` without an
            # explicit `language`: "Send the 'language' parameter, or use
            # response_format 'json' or 'text'." Retry with timestamps intact
            # first (verbose_json + language, then srt/vtt), and only use
            # plain `json`/`text` as a last resort (redistributed over the
            # audio duration so citations still get [m:ss] timeframes).
            msg = str(exc).lower()
            retryable = (
                "verbose_json" in msg
                or "language" in msg
                or "501" in msg
                or "not implemented" in msg
            )
            if not retryable:
                raise
            self._needs_language = True
            try:
                result = self._create(audio_path, "verbose_json", fallback_lang)
            except Exception:
                result = self._transcribe_with_timestamps(audio_path, fallback_lang)
        return self._to_segments(result, audio_path)

    def _transcribe_with_timestamps(self, audio_path: Path, language: str):
        """Fallback chain that preserves per-segment timestamps.

        Order: `srt` → `vtt` (both carry `start --> end` cues and are widely
        supported, including by servers that reject `verbose_json`), then
        plain `json` (no timestamps — kept as a single segment).
        """
        last_exc: Exception | None = None
        for fmt in ("srt", "vtt", "json"):
            try:
                result = self._create(audio_path, fmt, self.language)
            except Exception as exc:
                if index == len(formats) - 1 or not self._format_error(exc):
                    raise
                continue
            self._working_format = fmt
            return self._to_segments(result, audio_path)
        raise RuntimeError("Transcription failed")

    def _to_segments(self, result, audio_path: Path) -> list[Segment]:
        # `srt`/`vtt`/`text` come back as raw strings — parse cues so each
        # segment keeps its timeframe (the `.vtt`-style citations downstream
        # depend on it). Plain `json`/`text` has no cues: redistribute its
        # sentences over the audio duration so each segment still gets a
        # estimated [m:ss] timeframe instead of collapsing to one `0:00` block.
        if isinstance(result, str):
            parsed = parse_srt_vtt(result)
            if parsed:
                return parsed
            text = result.strip()
            return synthesize_segments(text, audio_path) if text else []
        raw = getattr(result, "segments", None) or []
        if not raw:
            text = str(getattr(result, "text", "") or "").strip()
            return synthesize_segments(text, audio_path) if text else []
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
    return Transcriber(base_url, api_key, settings.transcription_model, settings.transcription_language)
