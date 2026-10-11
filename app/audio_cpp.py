"""
Managed local transcription server (audio.cpp) — the `TRANSCRIPTION_MODE=local` path.

Why this exists: lecture audio/video needs speech-to-text with timestamps, and
the cloud path (`TRANSCRIPTION_MODE=cloud`, Groq/OpenAI Whisper) is not always
available. This module vendors the audio.cpp `audiocpp_server` binary into
`data/audio-cpp/` and runs a local ASR model there:

    TRANSCRIPTION_MODE=local   engine + ASR weights auto-download on first boot,
                               server starts lazily on first transcription call
                               (wake-on-API) and unloads after idle. Cloud
                               TRANSCRIPTION_*_URL/KEY lines are ignored.
    TRANSCRIPTION_MODE=cloud   classic behaviour (default): TRANSCRIPTION_*
                               in .env win, nothing is downloaded.

Engine builds come from `0xShug0/audio.cpp` releases. Asset choice is
system-aware: NVIDIA GPUs (`nvidia-smi` present) get a CUDA build, everyone
else gets a CPU build (portable preferred — release AVX-512 builds crash on
older CPUs). macOS gets a Metal build.

Layout under the local dir (default `./data/audio-cpp`, override with
`TRANSCRIPTION_LOCAL_DIR`):

    engine/       extracted audio.cpp build + BUILD_TAG
    models/       ASR weights (default Qwen3-ASR-0.6B Q8 GGUF)
    logs/         audiocpp-server.log
    server.json   generated server config (host/port/backend/models)

The server speaks OpenAI-style `/v1/audio/transcriptions` (multipart
file+model, like Whisper), so the existing `Transcriber` client works
unchanged — `LocalTranscriber` below just wakes the server first.

Only the standard library is used here — downloads, extraction and process
management must work before any third-party import quirks matter.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llama_embed import download_file, extract_archive

log = logging.getLogger(__name__)

_MANAGER: LocalTranscriptionServer | None = None

_GITHUB_API = "https://api.github.com/repos/{repo}/releases?per_page=30"


# ================================================================== releases
def _github_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "noteslyt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _is_audio_asset(name: str) -> bool:
    lower = name.lower()
    return (
        lower.startswith("audio-")
        and "-bin-" in lower
        and lower.endswith((".zip", ".tar.gz", ".tgz"))
        and "source" not in lower
        and "sha256" not in lower
        and "checksums" not in lower
    )


def _has_nvidia_gpu() -> bool:
    return shutil.which("nvidia-smi") is not None


def _platform_keywords() -> list[str]:
    """Asset-name fragments preferred on this machine, strongest first."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    cuda = _has_nvidia_gpu()
    if system == "windows":
        if cuda:
            return ["windows-x64-cuda", "win-cuda", "cuda12", "cuda"]
        return ["windows-x64-cpu-portable", "windows-x64-cpu", "cpu-portable", "cpu"]
    if system == "linux":
        if cuda:
            return ["ubuntu-x64-cuda", "cuda12", "cuda"]
        return ["ubuntu-x64-cpu-portable", "ubuntu-x64-cpu", "cpu-portable", "cpu"]
    if system == "darwin":
        if machine in {"arm64", "aarch64"}:
            return ["macos-arm64-metal", "macos-metal", "metal", "arm64"]
        return ["macos-x64-metal", "macos-metal", "metal", "x64"]
    return ["cpu-portable", "cpu"] if not cuda else ["cuda", "cpu"]


def pick_audio_asset(release: dict[str, Any], keywords: list[str] | None = None) -> dict[str, Any] | None:
    """Best audio.cpp binary asset for this platform. Pure function — unit-testable."""
    keywords = [k.lower() for k in (keywords or _platform_keywords())]
    system = platform.system().lower()
    cuda = _has_nvidia_gpu()
    scored: list[tuple[int, dict[str, Any]]] = []
    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        if not _is_audio_asset(name):
            continue
        lower = name.lower()
        if system == "windows" and not any(x in lower for x in ("windows", "win")):
            continue
        if system == "linux" and not any(x in lower for x in ("linux", "ubuntu")):
            continue
        if system == "darwin" and not any(x in lower for x in ("macos", "darwin")):
            continue
        # Never serve a CUDA bundle to a CPU box (400 MB of dead weight that
        # still needs CUDA DLLs) and never serve CPU-only to a CUDA box when a
        # CUDA build exists — the keyword scores below only rank survivors.
        if not cuda and "cuda" in lower:
            continue
        score = sum(30 - 4 * i for i, kw in enumerate(keywords) if kw and kw in lower)
        if lower.endswith((".zip", ".tar.gz", ".tgz")):
            score += 2
        if "portable" in lower and not cuda:
            score += 3  # broader CPU compat (release AVX-512 builds crash old CPUs)
        if any(x in lower for x in ("colab", "snapdragon", "android")):
            score -= 10
        if score > 0:
            scored.append((score, asset))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], str(x[1].get("name", ""))))
    return scored[0][1]


def newest_audio_release(repo: str) -> dict[str, Any]:
    """Newest release that ships audio.cpp binaries. Raises readably on failure."""
    try:
        req = urllib.request.Request(
            _GITHUB_API.format(repo=repo), headers={"User-Agent": "noteslyt/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            releases = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        raise RuntimeError(f"Could not list {repo} releases: {exc}") from exc
    if not isinstance(releases, list) or not releases:
        raise RuntimeError(f"No releases found for {repo}.")

    def sort_key(r: dict[str, Any]) -> tuple[int, str]:
        tag = str(r.get("tag_name", ""))
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", tag)
        if m:
            return (int(m.group(1)) * 10_000 + int(m.group(2)) * 100 + int(m.group(3)), str(r.get("published_at", "")))
        return (-1, str(r.get("published_at", "")))

    ordered = sorted(releases, key=sort_key, reverse=True)
    for rel in ordered:
        if rel.get("draft"):
            continue
        if any(_is_audio_asset(str(a.get("name", ""))) for a in rel.get("assets", [])):
            return rel
    raise RuntimeError(f"No release of {repo} ships audio.cpp binaries.")


def default_backend() -> str:
    """Server `backend` for this machine: cuda > metal (macOS) > cpu."""
    if _has_nvidia_gpu():
        return "cuda"
    if platform.system().lower() == "darwin":
        return "metal"
    return "cpu"


# ==================================================================== manager
@dataclass
class TranscriptionEngineStatus:
    installed: bool = False
    build_tag: str = ""
    binary: str = ""
    running: bool = False
    url: str = ""
    model_present: bool = False
    model_path: str = ""
    backend: str = ""
    update_state: str = "idle"          # idle|checking|downloading|ready|error
    update_message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "build_tag": self.build_tag,
            "binary": self.binary,
            "running": self.running,
            "url": self.url,
            "model_present": self.model_present,
            "model_path": self.model_path,
            "backend": self.backend,
            "update_state": self.update_state,
            "update_message": self.update_message,
        }


class LocalTranscriptionServer:
    """Downloads, runs, sleeps and updates a vendored audio.cpp STT server."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.root = Path(settings.transcription_local_dir).expanduser().resolve()
        self.engine_dir = self.root / "engine"
        self.models_dir = self.root / "models"
        self.logs_dir = self.root / "logs"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_used = 0.0
        self._idle_thread: threading.Thread | None = None
        self._update_state = "idle"
        self._update_message = ""
        for d in (self.engine_dir, self.models_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- properties
    @property
    def base_url(self) -> str:
        s = self.settings
        return f"http://{s.transcription_local_host}:{s.transcription_local_port}/v1"

    def binary(self) -> Path | None:
        if os.name == "nt":
            names = ["audiocpp_server.exe", "audiocpp-server.exe", "server.exe"]
        else:
            names = ["audiocpp_server", "audiocpp-server", "server"]
        if self.engine_dir.exists():
            for name in names:
                hits = sorted(self.engine_dir.rglob(name))
                hits = [h for h in hits if h.is_file()]
                if hits:
                    return hits[0].resolve()
        found = shutil.which("audiocpp_server") or shutil.which("audiocpp-server")
        return Path(found).resolve() if found else None

    def build_tag(self) -> str:
        tag_file = self.engine_dir / "BUILD_TAG"
        return tag_file.read_text(encoding="utf-8").strip() if tag_file.exists() else ""

    def model_file(self) -> Path:
        """Resolve the ASR weights, honouring an explicit user path first."""
        s = self.settings
        if s.transcription_local_model_path:
            path = Path(s.transcription_local_model_path).expanduser().resolve()
            if not path.exists():
                raise RuntimeError(
                    f"Transcription model override does not exist: {path}. "
                    "Fix the path or unset TRANSCRIPTION_LOCAL_MODEL_PATH to use "
                    "the managed download."
                )
            return path
        return self.models_dir / Path(s.transcription_local_model_file).name

    def server_config_path(self) -> Path:
        return self.root / "server.json"

    # ---------------------------------------------------------------- ensure
    def ensure_engine(self) -> Path:
        """Return a working audiocpp_server binary, downloading it on first boot."""
        exe = self.binary()
        if exe:
            return exe
        log.warning("audio.cpp engine not found under %s — downloading a build.", self.engine_dir)
        self._update_state, self._update_message = "downloading", "fetching audio.cpp build"
        try:
            tag = self._install_engine()
        finally:
            self._update_state, self._update_message = "idle", ""
        exe = self.binary()
        if not exe:
            raise RuntimeError("Engine download finished but no audiocpp_server binary was found.")
        log.warning("audio.cpp engine ready (%s).", tag)
        return exe

    def _install_engine(self) -> str:
        s = self.settings
        release = newest_audio_release(s.transcription_local_repo)
        asset = pick_audio_asset(release)
        if not asset:
            raise RuntimeError(
                f"Release {release.get('tag_name')} ships no audio.cpp binary for this platform. "
                "Install audio.cpp manually and point TRANSCRIPTION_LOCAL_DIR at it."
            )
        name = str(asset["name"])
        url = asset["browser_download_url"]
        tmp_dir = self.root / "downloads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        archive = tmp_dir / name
        log.warning("Downloading audio.cpp %s (%s)…", release.get("tag_name"), name)
        download_file(url, archive, f"audio.cpp {release.get('tag_name')}")
        staging = self.root / f".staging-engine-{int(time.time())}"
        extract_archive(archive, staging)
        backup = None
        if self.engine_dir.exists():
            backup = self.root / f".backup-engine-{int(time.time())}"
            self.engine_dir.rename(backup)
        staging.rename(self.engine_dir)
        (self.engine_dir / "BUILD_TAG").write_text(str(release.get("tag_name", "")), encoding="utf-8")
        try:
            archive.unlink()
        except OSError:
            pass
        if backup:
            shutil.rmtree(backup, ignore_errors=True)
        return str(release.get("tag_name", ""))

    def ensure_model_file(self) -> Path:
        """Return the ASR weights, downloading the default package on first boot."""
        path = self.model_file()
        if path.exists():
            return path
        s = self.settings
        if s.transcription_local_model_path:
            raise RuntimeError(f"Transcription model override does not exist: {path}.")
        url = f"https://huggingface.co/{s.transcription_local_model_repo}/resolve/main/{s.transcription_local_model_file}"
        log.warning("Downloading transcription model %s…", Path(s.transcription_local_model_file).name)
        download_file(url, path, path.name)
        log.warning("Saved %s (%.1f MB).", path.name, path.stat().st_size / 1e6)
        return path

    def write_server_config(self) -> Path:
        """Generate the audio.cpp server.json for the managed model."""
        s = self.settings
        model_path = self.ensure_model_file()
        config = {
            "host": s.transcription_local_host,
            "port": s.transcription_local_port,
            "backend": default_backend(),
            "device": 0,
            "threads": max(os.cpu_count() or 4, 1),
            "lazy_load": True,
            "models": [
                {
                    "id": s.transcription_local_model_id,
                    "family": s.transcription_local_family,
                    "path": str(model_path),
                    "task": "asr",
                    "mode": "offline",
                }
            ],
        }
        cfg_path = self.server_config_path()
        cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return cfg_path

    def ensure_ready(self) -> str:
        """Full boot path: engine + model + running server. Returns base URL."""
        self.ensure_engine()
        self.ensure_model_file()
        self.ensure_running()
        self.touch()
        return self.base_url

    # ------------------------------------------------------------ lifecycle
    def is_running(self) -> bool:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            self._proc = None
            return False
        try:
            req = urllib.request.Request(
                f"http://{self.settings.transcription_local_host}:"
                f"{self.settings.transcription_local_port}/health",
                headers={"User-Agent": "noteslyt/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return True  # process alive; health endpoint may still be warming up

    def ensure_running(self, timeout_s: int = 600) -> None:
        """Start the server unless already up (wake-on-API), then wait for health."""
        with self._lock:
            if self.is_running():
                return
            # Only one vendored engine stays resident: loading the ASR model
            # unloads the embedding server first so the two never share memory.
            self._unload_peer_embedder()
            exe = self.ensure_engine()
            cfg = self.write_server_config()
            cmd = [str(exe), "--config", str(cfg)]
            log.warning("Starting local transcription server (%s)…", self.model_file().name)
            log.info("audiocpp_server launch: %s", " ".join(cmd))
            log_file = (self.logs_dir / "audiocpp-server.log").open("a", encoding="utf-8")
            try:
                kwargs: dict[str, Any] = {"stdout": log_file, "stderr": subprocess.STDOUT}
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._proc = subprocess.Popen(cmd, cwd=str(self.root), **kwargs)
            finally:
                log_file.close()
            self._start_idle_thread()
        deadline = time.monotonic() + timeout_s
        wanted = self.settings.transcription_local_model_id
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                raise RuntimeError("Local transcription server exited during startup — see logs/audiocpp-server.log.")
            try:
                base = f"http://{self.settings.transcription_local_host}:{self.settings.transcription_local_port}"
                req = urllib.request.Request(f"{base}/health", headers={"User-Agent": "noteslyt/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status != 200:
                        raise RuntimeError("not ready")
                req = urllib.request.Request(f"{base}/v1/models", headers={"User-Agent": "noteslyt/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    body = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
                ids = {str(m.get("id", "")) for m in body.get("data", [])} if isinstance(body, dict) else set()
                if not wanted or wanted in ids or ids:
                    log.warning("Local transcription server ready at %s.", self.base_url)
                    self._last_used = time.monotonic()
                    return
            except Exception:
                pass
            time.sleep(2.0)
        raise RuntimeError(f"Local transcription server did not become healthy within {timeout_s}s.")

    def touch(self) -> None:
        """Mark activity (resets the idle-unload clock). Call before each batch."""
        self._last_used = time.monotonic()
        self._start_idle_thread()

    @staticmethod
    def _unload_peer_embedder() -> None:
        """Unload the embedding server if resident, freeing memory for the ASR model.

        No-op when the embedding backend was never initialized (cloud mode or
        a process without the app lifespan). `stop()` on an idle peer is also
        a no-op, so this only ever kills a live server.
        """
        try:
            from .llama_embed import get_embedding_manager

            peer = get_embedding_manager()
        except (RuntimeError, ImportError):
            return
        if peer.is_running():
            log.warning("Unloading embedding server to free memory for the transcription model.")
            peer.stop()

    def _idle_timeout(self) -> int:
        try:
            return int(getattr(self.settings, "transcription_idle_unload_s", 300))
        except (TypeError, ValueError):
            return 300

    def _start_idle_thread(self) -> None:
        if self._idle_thread is not None or self._idle_timeout() <= 0:
            return
        interval = min(30.0, max(1.0, self._idle_timeout() / 2.0))

        def watch() -> None:
            while True:
                time.sleep(interval)
                idle_for = time.monotonic() - self._last_used
                if self._proc is not None and idle_for >= self._idle_timeout():
                    log.warning("Transcription server idle %.0fs — unloading to free memory.", idle_for)
                    self.stop()

        thread = threading.Thread(target=watch, name="audio-cpp-idle-unload", daemon=True)
        thread.start()
        self._idle_thread = thread

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
        log.warning("Local transcription server stopped.")

    # ---------------------------------------------------------------- status
    def status(self) -> TranscriptionEngineStatus:
        try:
            model_path = self.model_file()
            present = model_path.exists()
        except RuntimeError:
            model_path, present = Path(""), False
        return TranscriptionEngineStatus(
            installed=self.binary() is not None,
            build_tag=self.build_tag(),
            binary=str(self.binary() or ""),
            running=self.is_running(),
            url=self.base_url,
            model_present=present,
            model_path=str(model_path),
            backend=default_backend(),
            update_state=self._update_state,
            update_message=self._update_message,
        )

    # ---------------------------------------------------------------- update
    def check_update(self) -> dict[str, str]:
        release = newest_audio_release(self.settings.transcription_local_repo)
        latest = str(release.get("tag_name", ""))
        current = self.build_tag()
        asset = pick_audio_asset(release)
        return {
            "current_tag": current,
            "latest_tag": latest,
            "update_available": str(bool(latest and latest != current)),
            "asset": str(asset.get("name", "")) if asset else "",
        }

    def update_engine(self) -> str:
        if self._update_state == "downloading":
            return "already running"
        self._update_state, self._update_message = "checking", "checking for newer audio.cpp"
        try:
            info = self.check_update()
            if info["update_available"] != "True":
                self._update_state, self._update_message = "ready", f"already current ({info['current_tag'] or 'unknown'})"
                return self._update_message
            self._update_state, self._update_message = "downloading", f"installing {info['latest_tag']}"
            was_running = self.is_running()
            if was_running:
                self.stop()
            tag = self._install_engine()
            if was_running:
                self.ensure_running()
            self._update_state, self._update_message = "ready", f"updated to {tag}"
            return self._update_message
        except Exception as exc:
            self._update_state, self._update_message = "error", str(exc)[:300]
            raise


# ============================================================ managed transcriber
class LocalTranscriber:
    """OpenAI-compatible transcriber pointed at the managed server, with wake-on-API."""

    def __init__(self, manager: LocalTranscriptionServer, model: str, language: str | None = None) -> None:
        from .transcription import Transcriber

        self._manager = manager
        self._inner = Transcriber(manager.base_url, "local", model, language)
        self.name = f"audio-cpp-local:{model}"

    def _wake(self) -> None:
        self._manager.ensure_ready()
        self._manager.touch()

    def transcribe(self, audio_path) -> list:
        self._wake()
        return self._inner.transcribe(audio_path)


# ================================================================ singleton
def init_transcription_server(settings) -> LocalTranscriptionServer:
    """Create the manager and ensure engine + model files exist (downloads!)."""
    global _MANAGER
    manager = LocalTranscriptionServer(settings)
    manager.ensure_engine()
    manager.ensure_model_file()
    _MANAGER = manager
    log.warning("Local transcription backend ready (engine + model files present).")
    return manager


def get_transcription_manager() -> LocalTranscriptionServer:
    if _MANAGER is None:
        raise RuntimeError(
            "Local transcription mode needs the app lifespan (init_transcription_server). "
            "Scripts without a server should set TRANSCRIPTION_MODE=cloud."
        )
    return _MANAGER


__all__ = [
    "LocalTranscriptionServer",
    "LocalTranscriber",
    "TranscriptionEngineStatus",
    "default_backend",
    "init_transcription_server",
    "get_transcription_manager",
    "newest_audio_release",
    "pick_audio_asset",
]
