"""
Local disk file storage for multimodal sources.

Every uploaded file lives on the machine running the app, under
`DATA_DIR/uploads/<project_id>/<source_id>.<ext>`. We are not deploying to a
hosted environment for this submission, so there is no object-storage backend and
no CDN in front of it — one code path, no branching.

Two things worth calling out, both learned the hard way:

  * Never derive the on-disk name from the uploaded filename. The earlier version
    hardcoded `<id>.pdf`, which silently mislabelled every video, audio clip and
    spreadsheet. The real extension is preserved so the browser and any downstream
    media tool see the correct type.
  * Files are namespaced per project, so two sources with the same filename in
    different projects cannot collide.

Original filenames are kept in the `sources` table rather than on disk. On-disk
names are derived from the source id, which means a hostile upload name
(`../../etc/passwd`, `CON`, absurd lengths) can never escape the upload root or
produce an invalid Windows path.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Extension -> format id. The format id is what `sources.source_type` stores and
# what the parser registry dispatches on, so it has to distinguish .pdf from
# .docx — both are "documents" to a human but need different parsers.
#
# Media containers collapse to one id per family (image/audio/video) because a
# single parser handles each.
SOURCE_FORMATS = {
    # documents
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc",
    ".odt": "odt",
    ".rtf": "rtf",
    ".epub": "epub",
    ".pptx": "pptx",
    ".ppt": "ppt",
    # spreadsheets
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".csv": "csv",
    ".tsv": "tsv",
    # plain text
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    # images (one parser for all)
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".bmp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".svg": "image",
    # audio
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".ogg": "audio",
    ".opus": "audio",
    ".flac": "audio",
    # video
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
    ".mkv": "video",
    ".avi": "video",
    ".m4v": "video",
    ".mpg": "video",
    ".mpeg": "video",
}

# Fallback for a file with no or unknown extension.
DEFAULT_SOURCE_TYPE = "other"

# Viewer / cap classes, derived from the format. Two vocabularies, so they are
# named separately: tests a *format* against these? use media_kind().
VIDEO_TYPES = {"video"}
AUDIO_TYPES = {"audio"}
IMAGE_TYPES = {"image"}
SPREADSHEET_TYPES = {"xlsx", "xls", "csv", "tsv"}
TEXT_TYPES = {"md", "txt", "rtf"}
DOCUMENT_TYPES = {"pdf", "docx", "doc", "odt", "epub", "pptx", "ppt", "other"}

# Kept as extension sets for callers that still think in extensions.
VIDEO_EXTENSIONS = {e for e, f in SOURCE_FORMATS.items() if f == "video"}
AUDIO_EXTENSIONS = {e for e, f in SOURCE_FORMATS.items() if f == "audio"}
IMAGE_EXTENSIONS = {e for e, f in SOURCE_FORMATS.items() if f == "image"}

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def source_type_of(filename: str) -> str:
    """
    Map a filename to the format id stored in `sources.source_type`.

    This is the dispatch key for the parser registry, so it must be the format
    (`pdf`, `docx`, `pptx`), not a coarse kind.
    """
    return SOURCE_FORMATS.get(extension_of(filename), DEFAULT_SOURCE_TYPE)


def media_kind(source_type: str) -> str:
    """
    Viewer/cap class for a stored format id.

    `video` is returned for audio too: both play from a local path via a media
    element, so the viewer does not need a fourth branch.
    """
    if source_type in VIDEO_TYPES or source_type in AUDIO_TYPES:
        return "video"
    if source_type in IMAGE_TYPES:
        return "image"
    if source_type in SPREADSHEET_TYPES:
        return "spreadsheet"
    return "document"


def content_type_for(filename: str) -> str:
    """MIME type for streaming. A small explicit table beats a guess from the
    OS registry, which differs between machines."""
    table = {
        ".pdf": "application/pdf",
        ".md": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".rtf": "application/rtf",
        ".csv": "text/csv; charset=utf-8",
        ".tsv": "text/tab-separated-values; charset=utf-8",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".svg": "image/svg+xml",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
    }
    return table.get(extension_of(filename), "application/octet-stream")


def safe_display_name(filename: str) -> str:
    """Sanitize a user-supplied filename for display. The DB keeps the original
    too; this is only what we echo back into HTTP headers."""
    cleaned = _UNSAFE_CHARS.sub("_", Path(filename).name).strip("._") or "file"
    return cleaned[:180]


@dataclass
class StoredFile:
    path: Path
    size_bytes: int
    filename: str


class FileStorage:
    """Local disk storage rooted at `uploads_dir`."""

    def __init__(self, uploads_dir: Path):
        self._root = Path(uploads_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, project_id: str, source_id: str, filename: str) -> Path:
        ext = extension_of(filename)
        if not ext or len(ext) > 12:
            ext = ".bin"
        return self._root / str(project_id) / f"{source_id}{ext.lower()}"

    # ------------------------------------------------------------------ write
    def save(self, project_id: str, source_id: str, filename: str, data: bytes) -> StoredFile:
        """
        Write bytes to disk and return where they landed.

        Written to a `.part` file then renamed, so an interrupted upload can
        never leave a truncated file that later looks like a valid source.
        """
        path = self._path_for(project_id, source_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return StoredFile(path=path, size_bytes=len(data), filename=Path(filename).name)

    def open_for_read(self, project_id: str, source_id: str, filename: str) -> Path | None:
        """Resolve the on-disk path for a source, or None if absent.

        The stored row's filename is used for the extension lookup, so a renamed
        original still resolves.
        """
        path = self._path_for(project_id, source_id, filename)
        return path if path.is_file() else None

    def save_stream(self, project_id: str, source_id: str, filename: str, stream, limit_bytes: int) -> StoredFile:
        """Copy an upload in bounded blocks; never retain the whole file in memory."""
        path = self._path_for(project_id, source_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        size = 0
        try:
            with tmp.open("wb") as out:
                while block := stream.read(min(1024 * 1024, limit_bytes - size + 1)):
                    size += len(block)
                    if size > limit_bytes:
                        raise ValueError("File exceeds the upload size limit.")
                    out.write(block)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return StoredFile(path=path, size_bytes=size, filename=Path(filename).name)

    def copy_in(self, project_id: str, source_id: str, filename: str, src: Path) -> StoredFile:
        """Move/stream a file already on disk (large uploads) into storage."""
        path = self._path_for(project_id, source_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, path)
        return StoredFile(path=path, size_bytes=path.stat().st_size, filename=Path(filename).name)

    # ------------------------------------------------------------------ delete
    def delete(self, project_id: str, source_id: str) -> None:
        """Best-effort: remove the source file and its now-empty project dir."""
        try:
            project_dir = self._root / str(project_id)
            if not project_dir.is_dir():
                return
            for leftover in project_dir.glob(f"{source_id}.*"):
                leftover.unlink(missing_ok=True)
            if not any(project_dir.iterdir()):
                shutil.rmtree(project_dir, ignore_errors=True)
        except OSError as exc:
            # A missing file must never fail the API call that is cleaning up.
            log.warning("storage: could not delete %s/%s: %s", project_id, source_id, exc)

    def delete_project(self, project_id: str) -> None:
        """Remove every uploaded file for a project.

        The database cascade deletes the rows; this removes the bytes, so
        deleting a project cannot leak disk. Best-effort — a locked file must
        not fail the API call that is cleaning up.
        """
        try:
            shutil.rmtree(self._root / str(project_id), ignore_errors=True)
        except OSError as exc:
            log.warning("storage: could not remove project dir %s: %s", project_id, exc)

    def project_dir(self, project_id: str) -> Path:
        return self._root / str(project_id)

    def disk_usage_bytes(self) -> int:
        total = 0
        for path in self._root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return total
