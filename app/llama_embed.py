"""
Managed local embedding server (llama.cpp) — the default embedding mode.

Why this exists: joint embedding models (e.g. EmbeddingGemma-2, which encodes
text/images/video/audio into one space) only deliver when the *engine* really
encodes bytes. LM Studio's embeddings endpoint is text-only, so this module
vendors llama-server into `data/llama/` and runs the embedding model there:

    EMBEDDING_MODE=local   (default) — engine + weights auto-download on first
                             boot, server starts lazily on first embedding
                             call (wake-on-API) and unloads after idle.
    EMBEDDING_MODE=cloud   — classic behaviour: EMBEDDING_* in .env win.

Layout under the local dir (default `./data/llama`, override with
`EMBEDDING_LOCAL_DIR`):

    engine/          extracted llama.cpp build + BUILD_TAG
    models/          embedding weights + mmproj projector
    logs/            llama-server.log

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
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MANAGER: LocalEmbeddingServer | None = None

_GITHUB_API = "https://api.github.com/repos/{repo}/releases?per_page=30"


# ================================================================== releases
def _github_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "noteslyt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _is_binary_asset(name: str) -> bool:
    lower = name.lower()
    return (
        lower.startswith("llama-")
        and "-bin-" in lower
        and lower.endswith((".zip", ".tar.gz", ".tgz"))
        and "source" not in lower
        and "sha256" not in lower
        and "checksums" not in lower
    )


def _platform_keywords() -> list[str]:
    """Asset-name fragments preferred on this machine, strongest first."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    cuda = shutil.which("nvidia-smi") is not None
    if system == "windows":
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
        keys = [f"win-cuda-12-x64", "win-cuda-x64", "cuda"] if cuda else []
        return keys + [f"win-cpu-{arch}", "cpu", "win-vulkan-x64", "vulkan"]
    if system == "linux":
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
        keys = ["ubuntu-cuda-x64", "cuda"] if cuda else []
        return keys + [f"ubuntu-{arch}", "vulkan", "cpu"]
    if system == "darwin":
        return ["macos-arm64", "arm64"] if machine in {"arm64", "aarch64"} else ["macos-x64", "x64"]
    return ["cpu"]


def pick_release_asset(release: dict[str, Any], keywords: list[str] | None = None) -> dict[str, Any] | None:
    """Best binary asset for this platform. Pure function — unit-testable."""
    keywords = [k.lower() for k in (keywords or _platform_keywords())]
    system = platform.system().lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        if not _is_binary_asset(name):
            continue
        lower = name.lower()
        if system == "windows" and "win" not in lower and "windows" not in lower:
            continue
        if system == "linux" and not any(x in lower for x in ("linux", "ubuntu")):
            continue
        if system == "darwin" and not any(x in lower for x in ("macos", "darwin", "ios")):
            continue
        score = sum(30 - 4 * i for i, kw in enumerate(keywords) if kw and kw in lower)
        if lower.endswith((".zip", ".tar.gz", ".tgz")):
            score += 2
        if any(x in lower for x in ("snapdragon", "android", "xcframework")):
            score -= 10
        if score > 0:
            scored.append((score, asset))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], str(x[1].get("name", ""))))
    return scored[0][1]


def newest_binary_release(repo: str) -> dict[str, Any]:
    """Newest incremental (bNNNN) release that ships binaries.

    Stable `vX.Y.Z` releases carry no binaries, so they are skipped unless
    nothing else exists. Raises with a readable message on network failure.
    """
    try:
        releases = _github_json(_GITHUB_API.format(repo=repo))
    except Exception as exc:
        raise RuntimeError(f"Could not list {repo} releases: {exc}") from exc
    if not isinstance(releases, list) or not releases:
        raise RuntimeError(f"No releases found for {repo}.")

    def sort_key(r: dict[str, Any]) -> tuple[int, str]:
        tag = str(r.get("tag_name", ""))
        if re.match(r"^v\d", tag):
            return (-2, "")
        m = re.search(r"b(\d+)", tag)
        return (int(m.group(1)) if m else -1, str(r.get("published_at", "")))

    ordered = sorted(releases, key=sort_key, reverse=True)
    for rel in ordered:
        if rel.get("draft"):
            continue
        if any(_is_binary_asset(str(a.get("name", ""))) for a in rel.get("assets", [])):
            return rel
    raise RuntimeError(f"No release of {repo} ships llama.cpp binaries.")


# ================================================================== download
def download_file(url: str, dest: Path, label: str = "") -> None:
    """Stream-download with MB progress on the log line. Atomic via rename."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".dl-{dest.name}-{os.getpid()}"
    req = urllib.request.Request(url, headers={"User-Agent": "noteslyt/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    log.info("%s %.1f/%.1f MB", label or dest.name, done / 1e6, total / 1e6)
    except urllib.error.HTTPError as exc:
        if tmp.exists():
            tmp.unlink()
        if exc.code in (401, 403):
            raise RuntimeError(
                f"Download rejected (HTTP {exc.code}): {url}. "
                "The file may need a Hugging Face license acceptance — download it "
                "once manually and point EMBEDDING_LOCAL_MODEL_PATH at it."
            ) from exc
        raise RuntimeError(f"Download failed (HTTP {exc.code}): {url}") from exc
    except Exception as exc:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"Download failed: {url} ({exc})") from exc
    tmp.rename(dest)


def extract_archive(archive: Path, destination: Path) -> None:
    """Extract zip/tar.gz, refusing entries that escape `destination`."""
    destination.mkdir(parents=True, exist_ok=True)
    dest_root = destination.resolve()

    def safe_target(name: str) -> Path:
        target = (destination / name).resolve()
        if dest_root != target and dest_root not in target.parents:
            raise RuntimeError(f"Archive entry escapes install dir: {name}")
        return target

    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                safe_target(member.filename)
            zf.extractall(destination)
    elif archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                safe_target(member.name)
            tf.extractall(destination)
    else:
        raise RuntimeError(f"Unsupported archive: {archive.name}")
    # Collapse a single top-level directory (release zips nest one deep).
    items = list(destination.iterdir())
    while len(items) == 1 and items[0].is_dir():
        inner = items[0]
        for child in inner.iterdir():
            shutil.move(str(child), str(destination / child.name))
        inner.rmdir()
        items = list(destination.iterdir())


# ==================================================================== manager
@dataclass
class EngineStatus:
    installed: bool = False
    build_tag: str = ""
    binary: str = ""
    running: bool = False
    url: str = ""
    model_present: bool = False
    model_path: str = ""
    mmproj_present: bool = False
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
            "mmproj_present": self.mmproj_present,
            "update_state": self.update_state,
            "update_message": self.update_message,
        }


class LocalEmbeddingServer:
    """Downloads, runs, sleeps and updates a vendored llama-server for embeddings."""

    def __init__(self, settings) -> None:
        self.settings = settings
        # The child process runs inside this directory. Resolve paths in the
        # parent's cwd before launch so relative paths aren't prefixed twice.
        self.root = Path(settings.embedding_local_dir).expanduser().resolve()
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
        return f"http://{s.embedding_local_host}:{s.embedding_local_port}/v1"

    def binary(self) -> Path | None:
        names = ["llama-server.exe", "server.exe"] if os.name == "nt" else ["llama-server", "server"]
        for root in [self.engine_dir]:
            if root.exists():
                for name in names:
                    hits = sorted(root.rglob(name))
                    if hits:
                        return hits[0].resolve()
        found = shutil.which("llama-server")
        return Path(found).resolve() if found else None

    def build_tag(self) -> str:
        tag_file = self.engine_dir / "BUILD_TAG"
        return tag_file.read_text(encoding="utf-8").strip() if tag_file.exists() else ""

    def model_files(self) -> tuple[Path, Path]:
        """Resolve (weights, mmproj), honouring explicit user paths first."""
        s = self.settings
        weights = Path(s.embedding_local_model_path).expanduser().resolve() if s.embedding_local_model_path else None
        mmproj = Path(s.embedding_local_mmproj_path).expanduser().resolve() if s.embedding_local_mmproj_path else None
        if weights is None:
            weights = self.models_dir / s.embedding_local_model_file
        if mmproj is None:
            mmproj = self.models_dir / s.embedding_local_mmproj_file
        for label, path, override in (("weights", weights, bool(s.embedding_local_model_path)),
                                      ("projector", mmproj, bool(s.embedding_local_mmproj_path))):
            if override and not path.exists():
                raise RuntimeError(
                    f"Embedding {label} override does not exist: {path}. "
                    "Fix the path or unset the override to use the managed download."
                )
        return weights, mmproj

    # ---------------------------------------------------------------- ensure
    def ensure_engine(self) -> Path:
        """Return a working llama-server binary, downloading it on first boot."""
        exe = self.binary()
        if exe:
            return exe
        log.warning("llama.cpp engine not found under %s — downloading a build.", self.engine_dir)
        self._update_state, self._update_message = "downloading", "fetching llama.cpp build"
        try:
            tag = self._install_engine()
        finally:
            self._update_state, self._update_message = "idle", ""
        exe = self.binary()
        if not exe:
            raise RuntimeError("Engine download finished but no llama-server binary was found.")
        log.warning("llama.cpp engine ready (%s).", tag)
        return exe

    def _install_engine(self) -> str:
        s = self.settings
        release = newest_binary_release(s.llama_github_repo)
        asset = pick_release_asset(release)
        if not asset:
            raise RuntimeError(
                f"Release {release.get('tag_name')} ships no binary for this platform. "
                "Install llama.cpp manually and point EMBEDDING_LOCAL_DIR at it."
            )
        name = str(asset["name"])
        url = asset["browser_download_url"]
        tmp_dir = self.root / "downloads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        archive = tmp_dir / name
        log.warning("Downloading llama.cpp %s (%s)…", release.get("tag_name"), name)
        download_file(url, archive, f"llama.cpp {release.get('tag_name')}")
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

    def ensure_model_files(self) -> tuple[Path, Path]:
        """Return (weights, mmproj), downloading the defaults on first boot."""
        s = self.settings
        weights, mmproj = self.model_files()
        missing = [p for p in (weights, mmproj) if not p.exists()]
        if not missing:
            return weights, mmproj
        base = f"https://huggingface.co/{s.embedding_local_repo}/resolve/main"
        for path in missing:
            url = f"{base}/{path.name}"
            log.warning("Downloading embedding model file %s (%.0f MB expected)…", path.name, 0)
            download_file(url, path, path.name)
            log.warning("Saved %s (%.1f MB).", path.name, path.stat().st_size / 1e6)
        return weights, mmproj

    def ensure_ready(self) -> str:
        """Full boot path: engine + model files + running server. Returns base URL."""
        self.ensure_engine()
        self.ensure_model_files()
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
            req = urllib.request.Request(f"{self.base_url[:-3]}/health",
                                         headers={"User-Agent": "noteslyt/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return True  # process alive; health endpoint may still be warming up

    def _server_command(self, weights: Path, mmproj: Path) -> list[str]:
        exe = self.binary()
        if not exe:
            raise RuntimeError("llama-server binary missing.")
        s = self.settings
        return [
            str(exe),
            "--model", str(weights),
            "--mmproj", str(mmproj),
            "--embedding",
            "--pooling", "mean",
            "--ctx-size", str(s.embedding_local_ctx),
            "--ubatch-size", str(s.embedding_local_ubatch_size),
            "--host", s.embedding_local_host,
            "--port", str(s.embedding_local_port),
        ]

    def ensure_running(self, timeout_s: int = 420) -> None:
        """Start the server unless already up (wake-on-API), then wait for health."""
        with self._lock:
            if self.is_running():
                return
            weights, mmproj = self.ensure_model_files()
            cmd = self._server_command(weights, mmproj)
            log.warning("Starting local embedding server (%s)…", weights.name)
            log.info("llama-server launch: %s", " ".join(cmd))
            log_file = (self.logs_dir / "llama-server.log").open("a", encoding="utf-8")
            try:
                kwargs: dict[str, Any] = {"stdout": log_file, "stderr": subprocess.STDOUT}
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._proc = subprocess.Popen(cmd, cwd=str(self.root), **kwargs)
            finally:
                log_file.close()
            self._start_idle_thread()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                raise RuntimeError("Local embedding server exited during startup — see logs/llama-server.log.")
            try:
                req = urllib.request.Request(
                    f"{self.base_url[:-3]}/health", headers={"User-Agent": "noteslyt/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    # /health is 200 while still loading — only "ok" means the
                    # model (and mtmd encoders) are actually ready. Returning
                    # earlier surfaces as 500s on the first embedding calls.
                    body = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
                    if resp.status == 200 and body.get("status") == "ok":
                        log.warning("Local embedding server ready at %s.", self.base_url)
                        self._last_used = time.monotonic()
                        return
            except Exception:
                pass
            time.sleep(2.0)
        raise RuntimeError(f"Local embedding server did not become healthy within {timeout_s}s.")

    def touch(self) -> None:
        """Mark activity (resets the idle-unload clock). Call before each batch."""
        self._last_used = time.monotonic()
        self._start_idle_thread()

    def _start_idle_thread(self) -> None:
        if self._idle_thread is not None or self.settings.embedding_idle_unload_s <= 0:
            return
        # Check often enough that short timeouts (and tests) behave sanely.
        interval = min(30.0, max(1.0, self.settings.embedding_idle_unload_s / 2.0))

        def watch() -> None:
            while True:
                time.sleep(interval)
                idle_for = time.monotonic() - self._last_used
                if self._proc is not None and idle_for >= self.settings.embedding_idle_unload_s:
                    log.warning("Embedding server idle %.0fs — unloading to free memory.", idle_for)
                    self.stop()

        thread = threading.Thread(target=watch, name="llama-idle-unload", daemon=True)
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
        log.warning("Local embedding server stopped.")

    # ---------------------------------------------------------------- status
    def status(self) -> EngineStatus:
        weights, mmproj = self.model_files()
        st = EngineStatus(
            installed=self.binary() is not None,
            build_tag=self.build_tag(),
            binary=str(self.binary() or ""),
            running=self.is_running(),
            url=self.base_url,
            model_present=weights.exists(),
            model_path=str(weights),
            mmproj_present=mmproj.exists(),
            update_state=self._update_state,
            update_message=self._update_message,
        )
        return st

    # ---------------------------------------------------------------- update
    def check_update(self) -> dict[str, str]:
        """Compare installed build tag against the newest binary release."""
        release = newest_binary_release(self.settings.llama_github_repo)
        latest = str(release.get("tag_name", ""))
        current = self.build_tag()
        asset = pick_release_asset(release)
        return {
            "current_tag": current,
            "latest_tag": latest,
            "update_available": str(bool(latest and latest != current)),
            "asset": str(asset.get("name", "")) if asset else "",
        }

    def update_engine(self) -> str:
        """Download the newest build and swap it in, restarting a live server."""
        if self._update_state == "downloading":
            return "already running"
        self._update_state, self._update_message = "checking", "checking for newer llama.cpp"
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


# ============================================================ managed embedder
class ManagedEmbedder:
    """OpenAI-compatible embedder pointed at the managed server, with wake-on-API."""

    def __init__(self, manager: LocalEmbeddingServer, model: str, batch_size: int = 64,
                 max_input_chars: int = 1500) -> None:
        from .llm import OpenAICompatibleEmbedder

        self._manager = manager
        self._inner = OpenAICompatibleEmbedder(manager.base_url, "local", model, batch_size,
                                               max_input_chars)
        self.name = f"llama-local:{model}"

    def _wake(self) -> None:
        self._manager.ensure_ready()
        self._manager.touch()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._wake()
        return self._inner.embed(texts)

    def embed_image(self, image: bytes, mime: str) -> list[float]:
        self._wake()
        return self._inner.embed_image(image, mime)

    def embed_audio(self, audio: bytes, mime: str) -> list[float]:
        self._wake()
        return self._inner.embed_audio(audio, mime)

    def embed_video(self, video: bytes, mime: str) -> list[float]:
        self._wake()
        return self._inner.embed_video(video, mime)


# ================================================================ singleton
def init_embedding_server(settings) -> LocalEmbeddingServer:
    """Create the manager and ensure engine + model files exist (downloads!)."""
    global _MANAGER
    manager = LocalEmbeddingServer(settings)
    manager.ensure_engine()
    manager.ensure_model_files()
    _MANAGER = manager
    log.warning("Local embedding backend ready (engine + model files present).")
    return manager


def get_embedding_manager() -> LocalEmbeddingServer:
    if _MANAGER is None:
        raise RuntimeError(
            "Local embedding mode needs the app lifespan (init_embedding_server). "
            "Scripts without a server should set EMBEDDING_MODE=cloud."
        )
    return _MANAGER


__all__ = [
    "LocalEmbeddingServer",
    "ManagedEmbedder",
    "EngineStatus",
    "init_embedding_server",
    "get_embedding_manager",
    "newest_binary_release",
    "pick_release_asset",
    "download_file",
    "extract_archive",
]
