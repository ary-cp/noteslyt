"""Frozen executable entry point for the NotesLyt desktop API."""

from __future__ import annotations

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


_LOG_FILE = _log_file()
if _LOG_FILE is not None:
    try:
        _stream = open(_LOG_FILE, "a", encoding="utf-8")
        _stream.write("\n===== NotesLyt backend start =====\n")
        _stream.flush()
        # Keep console streams working when present (dev), mirror to file
        # when frozen with --noconsole (stdout/stderr are None there).
        if sys.stdout is None:
            sys.stdout = _stream
        if sys.stderr is None:
            sys.stderr = _stream
        try:
            faulthandler.enable(file=_stream)
        except Exception:
            pass
    except OSError:
        _LOG_FILE = None
else:
    # PyInstaller's windowed bootloader does not provide console streams. Keep
    # Python logging and Uvicorn safe while Tauri owns the visible application UI.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

# Let PyInstaller discover the application package from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn

from app.main import app


def _run() -> None:
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(os.environ.get("NOTESLYT_API_PORT", "8000")),
            log_level="info",
        )
    except BaseException:
        # Frozen + --noconsole means nobody sees this traceback otherwise.
        # Persist it so `%APPDATA%/NotesLyt/data/logs/backend.log` explains
        # why the app never reached the login page.
        try:
            if _LOG_FILE is not None:
                with open(_LOG_FILE, "a", encoding="utf-8") as fh:
                    fh.write("FATAL backend crash:\n")
                    traceback.print_exc(file=fh)
                    fh.flush()
        except OSError:
            pass
        raise


if __name__ == "__main__":
    _run()
