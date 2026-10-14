"""Start the real frozen desktop API with disposable data and no console."""
from pathlib import Path
import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    exe = args.executable.resolve()
    assert exe.is_file(), exe
    with tempfile.TemporaryDirectory(prefix="noteslyt-desktop-") as directory:
        data = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "EMBEDDING_", "TRANSCRIPTION_", "DATABASE_", "AUTH_", "DATA_", "NOTESLYT_"))}
        env.update(DATA_DIR=str(data), NOTESLYT_API_PORT=str(port),
                   MIGRATIONS_DIR=str(root / "migrations"), SQLITE_MIGRATIONS_DIR=str(root / "migrations/sqlite"),
                   FRONTEND_DIR=str(root / "frontend/dist"), AUTH_SECRET_KEY="disposable-desktop-test-key-only-123456789",
                   EMBEDDING_MODE="cloud", TRANSCRIPTION_MODE="cloud", EMBEDDING_DIM="768",
                   LLM_BASE_URL="http://127.0.0.1:1/v1", LLM_API_KEY="test-only")
        for attempt in range(2):
            process = subprocess.Popen([str(exe)], cwd=data, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"Frozen API exited: {process.returncode}")
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                            assert response.status == 200
                            json.load(response)
                        break
                    except (OSError, ValueError):
                        time.sleep(.25)
                else:
                    raise RuntimeError("Frozen API did not start within 60 seconds")
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                    assert b"<html" in response.read().lower()
                time.sleep(2)
                assert process.poll() is None, "API exited after becoming ready"
                assert (data / "noteslyt.db").exists()
                print(f"PASS: frozen API {'fresh start' if attempt == 0 else 'restart'} + health + UI", flush=True)
            except Exception:
                log = data / "logs/backend.log"
                if log.exists():
                    print(log.read_text(encoding="utf-8")[-12000:], flush=True)
                raise
            finally:
                process.terminate()
                process.wait(timeout=10)
    print("DESKTOP BUNDLE VERIFIED")


if __name__ == "__main__":
    main()
