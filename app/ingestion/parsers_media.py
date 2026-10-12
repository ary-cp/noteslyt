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
pieces of at most `transcription_chunk_s` seconds (default 5 min) so one API
call never exceeds the provider's upload limit (~25 MB) *or* the STT model's
encoder window (Parakeet TDT fails past ~5 min). No `ffmpeg` on PATH → a
readable `ParserError`, not a traceback.

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
from ..transcription import audio_duration_s
from .parsers import ParserError
from .units import IMAGE, SEGMENT, ParsedSource, SourceUnit, format_timestamp

if TYPE_CHECKING:  # only for typing — avoids importing openai at module load
    from ..transcription import Segment, Transcriber

log = logging.getLogger(__name__)

# Merge targets for transcript segments → units (~15-20 s, so citations
# read like VTT cues instead of minute-long blobs).
_SEGMENT_TARGET_S = 15.0
_SEGMENT_TARGET_CHARS = 400
# Fallback transcription piece size (seconds) when settings carry no
# `transcription_chunk_s` (scripts, tests). Must fit the STT encoder window.
_TRANSCRIPTION_CHUNK_S = 300.0
_WAV_RATE = 16000


def transcription_chunk_s(settings: Settings) -> float:
    """Max audio seconds per transcription request (env-tunable, 1–5+ min)."""
    try:
        return max(float(getattr(settings, "transcription_chunk_s", 0.0) or 0.0), 30.0)
    except (TypeError, ValueError):
        return _TRANSCRIPTION_CHUNK_S


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
            "Audio/video ingestion needs `ffmpeg` on PATH to decode audio, and it was not found. "
            "Install ffmpeg (https://ffmpeg.org/download.html) or upload a WAV file directly."
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


def _extract_wav_chunks(media: Path, workdir: Path, chunk_s: float) -> list[Path]:
    """
    Media → 16 kHz mono WAV pieces of at most `chunk_s` seconds.

    A single ffmpeg call with the segment muxer; pieces are linear so piece `i`
    starts at `i * chunk_s` seconds. Works for audio and video inputs alike
    (`-vn` drops the video track when there is one).
    """
    exe = _ffmpeg()
    pattern = str(workdir / "part-%03d.wav")
    try:
        subprocess.run(
            [exe, "-y", "-v", "error", "-i", str(media),
             "-vn", "-ac", "1", "-ar", str(_WAV_RATE),
             "-f", "segment", "-segment_time", str(chunk_s), pattern],
            check=True, timeout=1800, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode(errors="replace")[-400:]
        raise ParserError(f"Could not extract audio from {media.name}: {tail or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParserError(f"Audio extraction from {media.name} timed out.") from exc
    return sorted(workdir.glob("part-*.wav"))


def _ensure_wav(path: Path, workdir: Path) -> Path:
    """Transcode non-WAV audio to 16 kHz mono WAV; WAV passes through untouched.

    The vendored STT server only accepts WAV uploads, while cloud Whisper
    endpoints take mp3/m4a/ogg natively — but Whisper resamples to 16 kHz mono
    internally anyway, so normalizing up front is lossless for accuracy and
    makes every backend behave identically. Only the short-file path uses
    this (long files already go through the chunked `_extract_wav_chunks`).
    """
    if path.suffix.lower() == ".wav":
        return path
    exe = _ffmpeg()
    out = workdir / (path.stem + ".wav")
    try:
        subprocess.run(
            [exe, "-y", "-v", "error", "-i", str(path),
             "-vn", "-ac", "1", "-ar", str(_WAV_RATE),
             "-f", "wav", str(out)],
            check=True, timeout=1800, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode(errors="replace")[-400:]
        raise ParserError(f"Could not decode audio from {path.name}: {tail or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParserError(f"Audio conversion of {path.name} timed out.") from exc
    try:
        payload = out.read_bytes()
    except OSError as exc:
        raise ParserError(f"Audio conversion of {path.name} produced no output.") from exc
    if _wav_sample_bytes(payload) <= 0:
        raise ParserError(f"Could not decode audio from {path.name} (no audio stream?).")
    return out


def _transcribe_pieces(
    transcriber: Transcriber,
    pieces: list[Path],
    chunk_s: float,
    filename: str,
) -> list[Segment]:
    """Transcribe each WAV piece, shifting segments back onto the file timeline."""
    segments: list[Segment] = []
    for i, piece in enumerate(pieces):
        try:
            got = transcriber.transcribe(piece)
        except Exception as exc:
            raise ParserError(
                f"Transcription of {filename} (part {i + 1}/{len(pieces)}, "
                f"~{chunk_s:.0f}s pieces) failed: {exc}. "
                "If the STT model rejects long inputs, lower TRANSCRIPTION_CHUNK_S."
            ) from exc
        offset = i * chunk_s
        for s in got:
            s.start += offset
            s.end += offset
        segments.extend(got)
    return segments


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


def parse_audio(path: Path, settings: Settings) -> ParsedSource:
    transcriber = _require_transcriber()
    chunk_s = transcription_chunk_s(settings)
    # Long files go in pieces (one request per ~chunk_s seconds) so a lecture
    # never exceeds the STT encoder window. Short files go in one request —
    # normalized to WAV first, because the vendored STT server rejects MP3
    # and friends while cloud endpoints quietly accept them. Duration unknown
    # (no ffprobe) → try single, the model error names TRANSCRIPTION_CHUNK_S
    # when it is too long.
    duration = audio_duration_s(path)
    if duration > chunk_s:
        with tempfile.TemporaryDirectory(prefix="study-audio-") as tmp:
            pieces = _extract_wav_chunks(path, Path(tmp), chunk_s)
            if not pieces:
                raise ParserError(f"No audio stream found in {path.name}.")
            segments = _transcribe_pieces(transcriber, pieces, chunk_s, path.name)
    else:
        with tempfile.TemporaryDirectory(prefix="study-audio-") as tmp:
            wav_path = _ensure_wav(path, Path(tmp))
            try:
                segments = transcriber.transcribe(wav_path)
            except Exception as exc:
                raise ParserError(
                    f"Transcription of {path.name} failed: {exc}. "
                    "If the STT model rejects long inputs, lower TRANSCRIPTION_CHUNK_S."
                ) from exc
    # Audio parts are coarse (~3 min) so each part maps to one embeddable
    # audio clip downstream (transcript + audio dual vectors, like video).
    # ~180 s of speech is ~2500 chars, so the char target scales with it.
    window_s = max(float(getattr(settings, "audio_segment_seconds", 180.0) or 180.0), 15.0)
    units = group_segments(segments, target_s=window_s, target_chars=int(window_s * 14))
    minutes = (units[-1].end_ts or 0.0) / 60.0
    warnings = [f"Transcribed {minutes:.1f} min of audio into {len(units)} segments."]
    return ParsedSource(units=units, parser="whisper", warnings=warnings)


def parse_video(path: Path, settings: Settings) -> ParsedSource:
    transcriber = _require_transcriber()
    chunk_s = transcription_chunk_s(settings)
    with tempfile.TemporaryDirectory(prefix="study-video-") as tmp:
        workdir = Path(tmp)
        pieces = _extract_wav_chunks(path, workdir, chunk_s)
        if not pieces:
            raise ParserError(f"No audio track found in {path.name}.")
        segments = _transcribe_pieces(transcriber, pieces, chunk_s, path.name)
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
