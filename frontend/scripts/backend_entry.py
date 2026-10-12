"""Frozen executable entry point for the NotesLyt desktop API."""

from __future__ import annotations

import sys
import os
from pathlib import Path

# Let PyInstaller discover the application package from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn

from app.main import app

# PyInstaller's windowed bootloader does not provide console streams. Keep
# Python logging and Uvicorn safe while Tauri owns the visible application UI.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("NOTESLYT_API_PORT", "8000")),
        log_level="info",
    )
