"""
Audio / video → transcript segment units — Phase D.18.

Lecture recordings become `segment` units (`start_ts`/`end_ts`) via the
per-capability transcription endpoint (`TRANSCRIPTION_*` in `.env` — Groq
Whisper, OpenAI, or a self-hosted server; see `app/transcription.py`).

Wiring: parser functions only receive `(path, settings)`, so the lifespan in
`app/main.py` hands the built `Transcriber` to this module once via
`set_transcriber()`. Anything that ingests without a lifespan (scripts, tests)
gets a readable failure instead of an `AttributeError`.

Video needs audio first: `ffmpeg` extracts 16 kHz mono WAV, split into
10-minute pieces so one API call never exceeds the provider's upload limit
(~25 MB). No `ffmpeg` on PATH → a readable `ParserError`, not a traceback.

Grouping: raw Whisper segments are a few seconds each — one unit per segment
would mean hundreds of embedding calls for a lecture. Segments are merged
until ~20 s or ~350 chars, whichever comes first, so citations read like
VTT cues. The merged unit keeps the
first segment's start as its locator (`segment` value = `[m:ss]`), so the
chunker invariant (one locator per chunk) still holds and citations stay
exact.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Settings
from ..storage import content_type_for
from .parsers import ParserError
from .units import IMAGE, SEGMENT, ParsedSource, SourceUnit, format_timestamp

if TYPE_CHECKING:  # only for typing — avoids importing openai at module load
    from ..transcription import Segment, Transcriber

log = logging.getLogger(__name__)

# Merge targets for transcript segments → units (~15-20 s, so citations
# read like VTT cues instead of minute-long blobs).
_SEGMENT_TARGET_S = 15.0
_SEGMENT_TARGET_CHARS = 400
# One transcription call per chunk of extracted audio (provider upload limits).
_AUDIO_CHUNK_S = 600
_WAV_RATE = 16000


_TRANSCRIBER: Transcriber | None = None


def set_transcriber(transcriber: Transcriber | None) -> None:
    """Called once from `app/main.py:lifespan`. `None` disables media parsing."""
    global _TRANSCRIBER
    _TRANSCRIBER = transcriber


def _require_transcriber() -> Transcriber:
    if _TRANSCRIBER is None:
        raise ParserError(
            "Transcription is not configured for this process. "
            "When running outside the API server, call set_transcriber() first."
        )
    return _TRANSCRIBER


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ParserError(
            "Video ingestion needs `ffmpeg` on PATH to extract audio, and it was not found. "
            "Install ffmpeg (https://ffmpeg.org/download.html) or upload the audio track directly."
        )
    return exe


def _wav_sample_bytes(wav: bytes) -> int:
    """Usable PCM payload size of a WAV buffer.

    Returns 0 for header-only/truncated output. ffmpeg can write a data-chunk
    header declaring N bytes and then deliver fewer (seeking past EOF, or a
    fast-seek overshoot of a few samples), so the declared size alone is not
    trusted. Anything under a second of audio is unusable for an embedding —
    and far below what any real segment slice contains.
    """
    idx = wav.find(b"data")
    if idx < 0 or idx + 8 > len(wav):
        return 0
    declared = int.from_bytes(wav[idx + 4:idx + 8], "little")
    actual = len(wav) - (idx + 8)
    if declared <= 0 or actual < min(declared, 32000):
        return 0
    return actual


def slice_audio_wav(source: Path, start_s: float, end_s: float) -> bytes:
    """Cut `[start_s, end_s]` from an audio/video file as 16 kHz mono WAV bytes.

    Used by native audio embedding (`AUDIO_EMBED_MODE=native`) and by chat's
    audio-clip attachment — one slice per retrieved segment, so the vector and
    the model's ears cover exactly the cited timeframe.
    """
    exe = _ffmpeg()
    span = max(end_s - start_s, 0.5)
    try:
        proc = subprocess.run(
            [exe, "-y", "-v", "error", "-ss", f"{max(start_s, 0.0):.2f}",
             "-i", str(source), "-t", f"{span:.2f}",
             "-vn", "-ac", "1", "-ar", str(_WAV_RATE),
             "-f", "wav", "pipe:1"],
            check=True, timeout=300, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode(errors="replace")[-400:]
        raise ParserError(f"Could not slice audio from {source.name}: {tail or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParserError(f"Audio slicing of {source.name} timed out.") from exc
    if not proc.stdout:
        raise ParserError(f"Audio slice of {source.name} came back empty.")
    if _wav_sample_bytes(proc.stdout) <= 0:
        raise ParserError(
            f"Audio slice [{start_s:.1f}s–{end_s:.1f}s] of {source.name} holds no samples "
            "(segment timestamps run past the file duration?)."
        )
    return proc.stdout


def _extract_wav_chunks(video: Path, workdir: Path) -> list[Path]:
    """
    Video → 16 kHz mono WAV pieces of at most `_AUDIO_CHUNK_S` seconds.

    A single ffmpeg call with the segment muxer; pieces are linear so piece `i`
    starts at `i * _AUDIO_CHUNK_S` seconds.
    """
    exe = _ffmpeg()
    pattern = str(workdir / "part-%03d.wav")
    try:
        subprocess.run(
            [exe, "-y", "-v", "error", "-i", str(video),
             "-vn", "-ac", "1", "-ar", str(_WAV_RATE),
             "-f", "segment", "-segment_time", str(_AUDIO_CHUNK_S), pattern],
            check=True, timeout=1800, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode(errors="replace")[-400:]
        raise ParserError(f"Could not extract audio from {video.name}: {tail or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParserError(f"Audio extraction from {video.name} timed out.") from exc
    return sorted(workdir.glob("part-*.wav"))


def group_segments(
    segments: list[Segment],
    target_s: float = _SEGMENT_TARGET_S,
    target_chars: int = _SEGMENT_TARGET_CHARS,
) -> list[SourceUnit]:
    """Merge whisper-fine segments into ~`target_s`-second `segment` units."""
    spoken = [s for s in segments if s.text and s.text.strip()]
    if not spoken:
        raise ParserError("Transcription returned no speech — silent file, or the wrong language model?")
    units: list[SourceUnit] = []
    buf: list[Segment] = []
    buf_chars = 0

    def flush() -> None:
        if not buf:
            return
        text = " ".join(s.text.strip() for s in buf)
        units.append(
            SourceUnit(
                index=len(units) + 1,
                text=text,
                locator_kind=SEGMENT,
                locator_value=format_timestamp(buf[0].start),
                start_ts=buf[0].start,
                end_ts=buf[-1].end,
            )
        )
        buf.clear()

    for seg in spoken:
        buf.append(seg)
        buf_chars += len(seg.text)
        span = seg.end - buf[0].start
        if span >= target_s or buf_chars >= target_chars:
            flush()
            buf_chars = 0
    flush()
    # Re-index defensively (flush order is already sequential).
    for i, u in enumerate(units):
        u.index = i + 1
    return units


def parse_audio(path: Path, settings: Settings) -> ParsedSource:  # noqa: ARG001
    transcriber = _require_transcriber()
    try:
        segments = transcriber.transcribe(path)
    except Exception as exc:
        raise ParserError(f"Transcription of {path.name} failed: {exc}") from exc
    units = group_segments(segments)
    minutes = (units[-1].end_ts or 0.0) / 60.0
    warnings = [f"Transcribed {minutes:.1f} min of audio into {len(units)} segments."]
    return ParsedSource(units=units, parser="whisper", warnings=warnings)


def parse_video(path: Path, settings: Settings) -> ParsedSource:
    transcriber = _require_transcriber()
    with tempfile.TemporaryDirectory(prefix="study-video-") as tmp:
        workdir = Path(tmp)
        pieces = _extract_wav_chunks(path, workdir)
        if not pieces:
            raise ParserError(f"No audio track found in {path.name}.")
        segments: list[Segment] = []
        for i, piece in enumerate(pieces):
            try:
                got = transcriber.transcribe(piece)
            except Exception as exc:
                raise ParserError(f"Transcription of {path.name} (part {i + 1}/{len(pieces)}) failed: {exc}") from exc
            offset = i * _AUDIO_CHUNK_S
            for s in got:
                s.start += offset
                s.end += offset
            segments.extend(got)
    units = group_segments(
        segments,
        target_s=settings.video_segment_seconds,
        target_chars=_SEGMENT_TARGET_CHARS,
    )
    minutes = (units[-1].end_ts or 0.0) / 60.0
    warnings = [f"Transcribed {minutes:.1f} min of video audio into {len(units)} segments."]
    return ParsedSource(units=units, parser="whisper-video", warnings=warnings)


def parse_image(path: Path, settings: Settings) -> ParsedSource:  # noqa: ARG001
    """Validate an image and create one image-addressed unit without captioning."""
    if path.suffix.lower() == ".svg":
        raise ParserError(f"{path.name} is a vector SVG — convert it to PNG first.")
    try:
        from PIL import Image
    except ImportError as exc:
        raise ParserError(f"Pillow is not installed: {exc}") from exc
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            frame = img.convert("RGB") if getattr(img, "is_animated", False) else img.convert("RGB")
            width, height = frame.width, frame.height
    except Exception as exc:
        raise ParserError(f"Could not read {path.name} as an image: {exc}") from exc

    label = path.stem[:60] or "image"
    unit = SourceUnit(
        index=1,
        text=f"Image content embedded directly ({width} × {height} pixels).",
        locator_kind=IMAGE,
        locator_value=label,
    )
    return ParsedSource(units=[unit], parser="multimodal-image-embedding")
