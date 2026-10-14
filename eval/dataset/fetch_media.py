"""Fetch the two pinned YouTube sources for the Ohm's-law eval dataset.

    uv run python eval/dataset/fetch_media.py [--out eval/dataset/files]

Media is NOT committed (YouTube standard license + size); this script
restores it reproducibly. Transcripts (*.en.vtt) ARE committed in
eval/dataset/raw/ as the grounding reference. Requires `uvx yt-dlp`
and ffmpeg on PATH.

  1. uZ-m91IEkjQ — "What Is OHM'S Law? [Explained in Under 5 Minutes]"
     (Instrumentation & Control, 4:42, theory) -> ohms_law_video.mp4
  2. 9WB82CvGIa8 — "HOW TO CONNECT OHM'S LAW CIRCUIT IN 4 MINS"
     (Make Me Scientific, 3:53, board practical) -> circuit_demo_audio.m4a
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SOURCES = [
    {"id": "uZ-m91IEkjQ", "kind": "video", "out": "ohms_law_video.mp4"},
    {"id": "9WB82CvGIa8", "kind": "audio", "out": "circuit_demo_audio.m4a"},
]


def _dl_base() -> list[str]:
    """yt-dlp invocation: project venv first, uvx fallback (used to fetch)."""
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                       check=True, capture_output=True)
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        return ["uvx", "yt-dlp"]


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval/dataset/files")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH — video/audio ingestion needs it.")
        return 2
    dl = _dl_base()
    for src in SOURCES:
        dest = out / src["out"]
        if dest.exists():
            print(f"exists, skipping: {dest.name} ({dest.stat().st_size} bytes)")
            continue
        url = f"https://www.youtube.com/watch?v={src['id']}"
        if src["kind"] == "video":
            _run(dl + ["--js-runtimes", "node",
                  "-f", "bv*[height<=480]+ba/b[height<=480]/b",
                  "--merge-output-format", "mp4", "-o", str(out / "%(id)s.%(ext)s"), url])
        else:
            _run(dl + ["--js-runtimes", "node",
                  "-f", "ba/b", "-x", "--audio-format", "m4a",
                  "-o", str(out / "%(id)s.%(ext)s"), url])
        # yt-dlp writes <id>.ext; rename to the stable corpus name.
        ext = ".mp4" if src["kind"] == "video" else ".m4a"
        got = out / f"{src['id']}{ext}"
        if got.exists():
            got.rename(dest)
        print(f"ready: {dest.name} ({dest.stat().st_size} bytes)")
    print("MEDIA READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
