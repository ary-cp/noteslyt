"""Bundled-first ffmpeg/ffprobe resolution.

Desktop (Tauri) ships ffmpeg inside the installer so learners never install
it. The website uses the same lookup: a bundled copy when present, system
PATH otherwise.

Search order (bundled ALWAYS wins over system PATH):

1. `FFMPEG_PATH` / `FFPROBE_PATH` env — how the Tauri shell hands the
   bundled binary to the Python backend (`frontend/src-tauri/src/main.rs`
   sets these to the resource-dir copy). An operator may also pin a custom
   build this way.
2. Known bundled locations (no env needed):
   - Tauri resource dir: `NOTESLYT_RESOURCE_DIR/ffmpeg/<os-subdir>/`
   - PyInstaller frozen dir: `sys._MEIPASS/ffmpeg/<os-subdir>/` or next to
     the frozen exe (`backend-runtime/noteslyt-api/ffmpeg/...`).
   - Repo checkout: `frontend/src-tauri/binaries-ffmpeg/<os-subdir>/`
     (populated by `frontend/scripts/fetch-ffmpeg.mjs`), `third_party/ffmpeg/`.
3. System `PATH` (`shutil.which`) — website fallback.

The `<os-subdir>` layout matches the fetch script:
`windows-x64`, `macos-arm64`, `macos-x64`, `linux-x64`.
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

_FFMPEG_NAMES = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg",)
_FFPROBE_NAMES = ("ffprobe.exe", "ffprobe") if os.name == "nt" else ("ffprobe",)

_OS_SUBDIRS: tuple[str, ...]
if sys.platform == "win32":
    _OS_SUBDIRS = ("windows-x64", "windows", "")
elif sys.platform == "darwin":
    _OS_SUBDIRS = ("macos-arm64", "macos-x64", "macos", "")
else:
    _OS_SUBDIRS = ("linux-x64", "linux", "")


def _is_exe(path: Path) -> bool:
    try:
        return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))
    except OSError:
        return False


def _bundled_dirs() -> list[Path]:
    """Candidate directories that may hold a shipped ffmpeg copy."""
    dirs: list[Path] = []

    def add(p: Path | str | None) -> None:
        if not p:
            return
        try:
            path = Path(p)
        except Exception:
            return
        dirs.append(path)

    resource_dir = os.environ.get("NOTESLYT_RESOURCE_DIR")
    if resource_dir:
        base = Path(resource_dir) / "ffmpeg"
        for sub in _OS_SUBDIRS:
            add(base / sub if sub else base)

    # PyInstaller frozen layout: binaries sit beside the exe or under _MEIPASS.
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            base = Path(meipass) / "ffmpeg"
            for sub in _OS_SUBDIRS:
                add(base / sub if sub else base)
        exe = Path(sys.executable).parent
        for anchor in (exe, exe / "ffmpeg"):
            if anchor.is_dir():
                for sub in _OS_SUBDIRS:
                    add(anchor / sub if sub else anchor)

    # Repo checkout (dev + website self-host): fetch script output + manual drop.
    here = Path(__file__).resolve()
    repo_root = here.parent.parent  # app/ffmpeg.py -> repo root
    binaries = repo_root / "frontend" / "src-tauri" / "binaries-ffmpeg"
    for sub in _OS_SUBDIRS:
        if sub:
            add(binaries / sub)
    add(binaries)
    add(repo_root / "third_party" / "ffmpeg")
    for sub in _OS_SUBDIRS:
        if sub:
            add(repo_root / "third_party" / "ffmpeg" / sub)

    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        add(Path(data_dir) / "ffmpeg")

    # Deduplicate while keeping order.
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = os.path.normcase(str(d))
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def _find_in_dirs(names: tuple[str, ...]) -> Path | None:
    for directory in _bundled_dirs():
        if not directory.is_dir():
            continue
        for name in names:
            candidate = directory / name
            if _is_exe(candidate):
                return candidate
    return None


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str | None:
    """Absolute path to the bundled-first ffmpeg binary, or None."""
    env = os.environ.get("FFMPEG_PATH")
    if env and _is_exe(Path(env)):
        return str(Path(env))
    found = _find_in_dirs(_FFMPEG_NAMES)
    if found:
        return str(found)
    return shutil.which("ffmpeg")


@lru_cache(maxsize=1)
def ffprobe_exe() -> str | None:
    """Absolute path to the bundled-first ffprobe binary, or None."""
    env = os.environ.get("FFPROBE_PATH")
    if env and _is_exe(Path(env)):
        return str(Path(env))
    found = _find_in_dirs(_FFPROBE_NAMES)
    if found:
        return str(found)
    return shutil.which("ffprobe")


@lru_cache(maxsize=1)
def ffmpeg_source() -> str:
    """Where the active ffmpeg comes from: bundled | system | missing."""
    env = os.environ.get("FFMPEG_PATH")
    if env and _is_exe(Path(env)):
        return "bundled"
    if _find_in_dirs(_FFMPEG_NAMES):
        return "bundled"
    if shutil.which("ffmpeg"):
        return "system"
    return "missing"


def clear_cache() -> None:
    """Drop cached lookups (tests that mutate env / stage fake binaries)."""
    ffmpeg_exe.cache_clear()
    ffprobe_exe.cache_clear()
    ffmpeg_source.cache_clear()
