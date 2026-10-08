"""Evidence media files outside the source tree, addressed only by server-generated names (owner: A08).

Layout: <data_dir>/<media_dir_name>/<session token>/<file token>.<ext>
  * session token = 32 random hex chars stored in SQLite (never derived from the session_id,
    which may contain ':' or '..');
  * file token    = 32 random hex chars + extension;
  * every path is rebuilt from validated tokens and checked to stay inside the media root;
  * symlinks are refused; writes go to "<name>.tmp" + fsync + os.replace (no torn files).
Hashes are SHA-256 of the stored bytes: they detect accidental change, they are NOT a
protection against the machine owner.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import secrets
import shutil
from pathlib import Path

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
FILE_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|mp4|webm)$")
EXT_BY_TYPE = {"image/jpeg": "jpg", "video/mp4": "mp4", "video/webm": "webm"}
JPEG_SOI = b"\xff\xd8\xff"


class MediaPathError(Exception):
    """A token/file name failed validation or resolved outside the media root."""


class MediaVault:
    def __init__(self, root: Path):
        self.root = root

    # ------------------------------------------------------------------ paths
    @staticmethod
    def new_token() -> str:
        return secrets.token_hex(16)

    @staticmethod
    def new_file_name(media_type: str) -> str:
        return f"{secrets.token_hex(16)}.{EXT_BY_TYPE[media_type]}"

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root.resolve(strict=True)

    def session_dir(self, token: str) -> Path:
        if not TOKEN_RE.fullmatch(token or ""):
            raise MediaPathError("invalid media token")
        root = self.root.resolve()
        path = root / token
        if path.is_symlink():
            raise MediaPathError("media directory is a symlink")
        if path.resolve().parent != root:
            raise MediaPathError("media directory outside root")
        return path

    def file_path(self, token: str, file_name: str) -> Path:
        if not FILE_RE.fullmatch(file_name or ""):
            raise MediaPathError("invalid media file name")
        directory = self.session_dir(token)
        path = directory / file_name
        if path.is_symlink():
            raise MediaPathError("media file is a symlink")
        if path.resolve().parent != directory.resolve():
            raise MediaPathError("media file outside session directory")
        return path

    # ------------------------------------------------------------------ io
    def free_bytes(self) -> int:
        try:
            return shutil.disk_usage(self.ensure_root()).free
        except OSError:
            return 0

    def write(self, token: str, file_name: str, data: bytes) -> tuple[str, int]:
        """Atomically write data; returns (sha256, size). Raises OSError/MediaPathError."""
        directory = self.session_dir(token)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.file_path(token, file_name)
        tmp = path.with_name(path.name + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return hashlib.sha256(data).hexdigest(), len(data)

    def read(self, token: str, file_name: str, max_bytes: int) -> bytes | None:
        """Bytes of a stored file, or None when it is missing. Raises MediaPathError/OSError."""
        path = self.file_path(token, file_name)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise OSError(errno.EFBIG, "media file larger than allowed")
        return data

    def remove_file(self, token: str, file_name: str) -> bool:
        try:
            self.file_path(token, file_name).unlink()
            return True
        except FileNotFoundError:
            return True
        except (OSError, MediaPathError):
            return False

    def remove_session_dir(self, token: str) -> bool:
        """Remove the session directory and everything in it (incl. *.tmp). True when gone."""
        try:
            directory = self.session_dir(token)
        except MediaPathError:
            return False
        if not directory.exists():
            return True
        trash = directory.with_name(f".trash-{token}-{secrets.token_hex(4)}")
        try:
            os.replace(directory, trash)  # atomic: the session dir disappears at once
        except OSError:
            trash = directory
        shutil.rmtree(trash, ignore_errors=True)
        return not trash.exists() and not directory.exists()

    def list_session_tokens(self) -> list[str]:
        try:
            return [p.name for p in self.root.iterdir() if p.is_dir() and TOKEN_RE.fullmatch(p.name)]
        except OSError:
            return []

    def cleanup_debris(self) -> int:
        """Remove leftovers of interrupted writes/deletes (*.tmp files, .trash-* dirs)."""
        removed = 0
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return 0
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir() and entry.name.startswith(".trash-"):
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
            elif entry.is_dir() and TOKEN_RE.fullmatch(entry.name):
                for child in entry.iterdir():
                    if child.name.endswith(".tmp") and child.is_file() and not child.is_symlink():
                        try:
                            child.unlink()
                            removed += 1
                        except OSError:
                            pass
        return removed


def encode_jpeg(image, quality: int) -> bytes | None:  # image: numpy uint8 HxWx3 BGR (read-only is fine)
    """JPEG bytes via OpenCV (part of the CV runtime). None when no encoder is available."""
    try:
        import cv2  # type: ignore[import-not-found]
    except Exception:
        return None
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return None
    data = buf.tobytes()
    return data if data.startswith(JPEG_SOI) else None
