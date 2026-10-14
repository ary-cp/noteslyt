"""Frozen executable entry point for the NotesLyt desktop API."""

from __future__ import annotations

import multiprocessing
import sys
import os
import faulthandler
import traceback
from pathlib import Path


def _log_file() -> Path | None:
    candidate = os.environ.get("NOTESLYT_LOG_FILE")
    if candidate:
        try:
            path = Path(candidate)
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            return None
    try:
        data_dir = Path(os.environ.get("DATA_DIR", "."))
        if not data_dir.is_absolute():
            data_dir = Path.cwd() / data_dir
        path = data_dir / "logs" / "backend.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None


def main() -> None:
    multiprocessing.freeze_support()
    path = _log_file()
    stream = None
    if path is not None:
        try:
            stream = path.open("a", encoding="utf-8", buffering=1)
            stream.write("\n===== NotesLyt backend start =====\n")
            try:
                faulthandler.enable(file=stream)
            except Exception:
                pass
        except OSError:
            pass
    # Always provide safe streams before imports in windowed executables,
    # including when opening the diagnostic log fails.
    if sys.stdout is None:
        sys.stdout = stream or open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = stream or open(os.devnull, "w", encoding="utf-8")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        import uvicorn
        from app.main import app
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("NOTESLYT_API_PORT", "8000")), log_level="info")
    except BaseException:
        traceback.print_exc(file=stream or sys.stderr)
        if stream is not None:
            stream.flush()
        raise


if __name__ == "__main__":
    main()
