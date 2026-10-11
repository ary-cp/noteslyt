"""
Video keyframes for vision captioning.

The configured text embedder cannot see pixels (EmbeddingGemma provably embeds
a data-URI's *text* instead of its bytes), so frames are captioned by the vision
chat model and the caption text joins the segment unit. Same locator, same
chunk — the visual content becomes searchable, topic-tagged and citable with
zero schema change. If a joint audio-visual embedding model is ever configured,
native frame vectors can hang off this same extraction helper.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "Video frame extraction needs `ffmpeg` on PATH. "
            "Install ffmpeg (https://ffmpeg.org/download.html)."
        )
    return exe


def extract_frame(video: Path, at_s: float, width: int) -> bytes:
    """One JPEG frame at `at_s` seconds, scaled to `width` px (aspect kept)."""
    exe = _ffmpeg()
    try:
        proc = subprocess.run(
            [exe, "-y", "-v", "error", "-ss", f"{max(at_s, 0.0):.2f}",
             "-i", str(video), "-frames:v", "1",
             "-vf", f"scale={max(width, 160)}:-1", "-q:v", "4",
             "-f", "mjpeg", "pipe:1"],
            check=True, timeout=120, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode(errors="replace")[-300:]
        raise RuntimeError(f"Frame extraction failed at {at_s:.1f}s: {tail or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Frame extraction timed out at {at_s:.1f}s.") from exc
    if not proc.stdout:
        raise RuntimeError(f"Frame at {at_s:.1f}s came back empty.")
    return proc.stdout


def slice_clip(video: Path, start_s: float, duration_s: float, width: int = 720) -> bytes:
    """Cut `duration_s` seconds of video from `start_s` (mp4 bytes, fast seek).

    Frames wider than `width` px are downscaled (aspect kept); narrower input
    passes through untouched, so low-res originals are never upscaled into
    fake tokens. MP4 needs a seekable output, so this goes through a temp
    file rather than a pipe. Raises with a readable reason on failure.
    """
    exe = _ffmpeg()
    if duration_s <= 0:
        raise RuntimeError(f"Cannot cut a {duration_s}s clip.")
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        try:
            subprocess.run(
                [exe, "-y", "-v", "error", "-ss", f"{max(start_s, 0.0):.2f}",
                 "-i", str(video), "-t", f"{duration_s:.2f}",
                 "-vf", f"scale='min({max(width, 160)},iw)':-2",
                 "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                 str(tmp.name)],
                check=True, timeout=180, capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or b"").decode(errors="replace")[-300:]
            raise RuntimeError(f"Clip cut failed at {start_s:.1f}s: {tail or exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Clip cut timed out at {start_s:.1f}s.") from exc
        data = Path(tmp.name).read_bytes()
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    if not data or b"ftyp" not in data[:32]:
        raise RuntimeError(f"Clip at {start_s:.1f}s came back empty.")
    return data


def ahash(jpeg: bytes) -> int:
    """64-bit average hash for near-duplicate detection (static slides)."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is not installed.") from exc
    with Image.open(io.BytesIO(jpeg)) as img:
        gray = img.convert("L").resize((8, 8))
        px = list(gray.getdata())
    avg = sum(px) / len(px)
    bits = 0
    for v in px:
        bits = (bits << 1) | (1 if v > avg else 0)
    return bits


def frame_distance(h1: int, h2: int) -> int:
    """Hamming distance between two hashes — small means near-identical."""
    return bin(h1 ^ h2).count("1")


FRAME_CAPTION_SYSTEM = """You describe one lecture-video frame for a study index.
Output 1-3 short sentences covering what a studying student needs: slide
titles, equations and diagrams transcribed verbatim, figures/charts and what
they show, visible on-screen text. Plain text, no preamble, no markdown.
If the frame is black, blurred, transitional, or carries no study content,
reply with exactly: NO_CONTENT"""
