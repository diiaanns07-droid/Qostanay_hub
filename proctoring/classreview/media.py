"""Clip validation, codec detection, HTTP Range parsing and file storage (T03).

Nothing here trusts the client: the declared Content-Type must match the container found in the bytes,
the MP4 box structure must be complete (no truncated upload), and the stored file name is random and
server-generated. Hashes (SHA-256) detect accidental change; they do not prove authenticity.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path

FILE_RE = re.compile(r"^[0-9a-f]{32}\.(mp4|avi|jpg)$")
#: codecs a current Chromium/Edge plays inside MP4 (H.264, VP9, AV1). HEVC/MPEG-4 Part 2 are not reliable.
BROWSER_CODECS = {"avc1", "avc3", "vp09", "av01"}
CODEC_LABEL_RU = {
    "avc1": "H.264",
    "avc3": "H.264",
    "vp09": "VP9",
    "av01": "AV1",
    "hvc1": "H.265/HEVC",
    "hev1": "H.265/HEVC",
    "mp4v": "MPEG-4 Part 2",
}


class InvalidMedia(Exception):
    def __init__(self, code: str, message_ru: str):
        super().__init__(message_ru)
        self.code = code
        self.message_ru = message_ru


@dataclass(frozen=True)
class MediaInfo:
    container: str  # "mp4" | "avi"
    codec: str | None  # fourcc of the first video track, e.g. "avc1"
    duration_s: float | None
    faststart: bool | None  # MP4: moov before mdat (plays before the whole file is fetched)

    @property
    def browser_playable(self) -> bool:
        return self.container == "mp4" and self.codec in BROWSER_CODECS


# --------------------------------------------------------------------------- MP4 (ISO BMFF)


def _boxes(data: bytes, start: int, end: int) -> list[tuple[str, int, int]]:
    """(type, payload_start, box_end) for each box in [start, end). Raises InvalidMedia on a broken layout."""
    out: list[tuple[str, int, int]] = []
    pos = start
    while pos < end:
        if end - pos < 8:
            raise InvalidMedia("truncated_mp4", "Файл MP4 обрезан: неполный заголовок блока.")
        size, raw_type = struct.unpack(">I4s", data[pos : pos + 8])
        header = 8
        if size == 1:
            if end - pos < 16:
                raise InvalidMedia("truncated_mp4", "Файл MP4 обрезан: неполный 64-битный размер блока.")
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise InvalidMedia("truncated_mp4", "Файл MP4 повреждён или загружен не полностью (размер блока выходит за конец файла).")
        try:
            box_type = raw_type.decode("ascii")
        except UnicodeDecodeError:
            raise InvalidMedia("not_mp4", "Файл не является MP4: неверный тип блока.") from None
        if not re.fullmatch(r"[ -~]{4}", box_type):
            raise InvalidMedia("not_mp4", "Файл не является MP4: неверный тип блока.")
        out.append((box_type, pos + header, pos + size))
        pos += size
    return out


def _child(data: bytes, start: int, end: int, name: str) -> tuple[int, int] | None:
    for box_type, p, e in _boxes(data, start, end):
        if box_type == name:
            return p, e
    return None


def inspect_mp4(data: bytes) -> MediaInfo:
    if len(data) < 16 or data[4:8] != b"ftyp":
        raise InvalidMedia("not_mp4", "Файл не является MP4 (нет блока ftyp в начале).")
    top = _boxes(data, 0, len(data))
    types = [t for t, _, _ in top]
    if "moov" not in types:
        raise InvalidMedia("mp4_without_index", "В MP4 нет блока moov: запись не завершена, видео не откроется.")
    if "mdat" not in types and "moof" not in types:
        raise InvalidMedia("mp4_without_media", "В MP4 нет видеоданных (mdat).")
    moov_p, moov_e = next((p, e) for t, p, e in top if t == "moov")
    duration = None
    mvhd = _child(data, moov_p, moov_e, "mvhd")
    if mvhd is not None:
        p, e = mvhd
        version = data[p]
        try:
            if version == 1:
                timescale, dur = struct.unpack(">IQ", data[p + 20 : p + 32])
            else:
                timescale, dur = struct.unpack(">II", data[p + 12 : p + 20])
            if timescale:
                duration = round(dur / timescale, 3)
        except struct.error:
            duration = None
    codec = None
    for box_type, tp, te in _boxes(data, moov_p, moov_e):
        if box_type != "trak":
            continue
        mdia = _child(data, tp, te, "mdia")
        if mdia is None:
            continue
        hdlr = _child(data, *mdia, "hdlr")
        if hdlr is None or data[hdlr[0] + 8 : hdlr[0] + 12] != b"vide":
            continue
        minf = _child(data, *mdia, "minf")
        stbl = _child(data, *minf, "stbl") if minf else None
        stsd = _child(data, *stbl, "stsd") if stbl else None
        if stsd is not None and stsd[1] - stsd[0] >= 16:
            entry = data[stsd[0] + 12 : stsd[0] + 16]
            try:
                codec = entry.decode("ascii")
            except UnicodeDecodeError:
                codec = None
        break
    first_media = next((i for i, t in enumerate(types) if t in ("mdat", "moof")), len(types))
    return MediaInfo("mp4", codec, duration, types.index("moov") < first_media)


# --------------------------------------------------------------------------- AVI (RIFF)


def inspect_avi(data: bytes) -> MediaInfo:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"AVI ":
        raise InvalidMedia("not_avi", "Файл не является AVI (нет заголовка RIFF/AVI).")
    riff_size = struct.unpack("<I", data[4:8])[0]
    if riff_size + 8 > len(data):
        raise InvalidMedia("truncated_avi", "Файл AVI загружен не полностью.")
    codec = None
    i = data.find(b"strh")
    while i != -1 and i + 16 <= len(data):
        # 'strh' <u32 size> <fccType> <fccHandler>
        if data[i + 8 : i + 12] == b"vids":
            codec = data[i + 12 : i + 16].decode("ascii", "replace").strip("\x00 ") or None
            break
        i = data.find(b"strh", i + 4)
    return MediaInfo("avi", codec, None, None)


def inspect(media_type: str, data: bytes) -> MediaInfo:
    """Validate the bytes against the declared media type."""
    if not data:
        raise InvalidMedia("empty", "Пустой файл.")
    if media_type == "video/mp4":
        return inspect_mp4(data)
    if media_type == "video/x-msvideo":
        return inspect_avi(data)
    raise InvalidMedia("unsupported_type", "Допустимы только video/mp4 и video/x-msvideo.")


def base_media_type(header: str | None) -> str:
    return (header or "").split(";", 1)[0].strip().lower()


# --------------------------------------------------------------------------- HTTP Range


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Single byte range -> (start, end) inclusive; None = send the whole file (no/unsupported header).
    Multiple ranges are answered with the whole file (allowed by RFC 9110)."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header)
    if not m:
        return None  # malformed or multi-range: ignore the header
    first, last = m.group(1), m.group(2)
    if first == "" and last == "":
        return None
    if size == 0:
        raise RangeNotSatisfiable()
    if first == "":
        length = int(last)
        if length == 0:
            raise RangeNotSatisfiable()
        return max(0, size - length), size - 1
    start = int(first)
    end = size - 1 if last == "" else min(int(last), size - 1)
    if start >= size or (last != "" and int(last) < start):
        raise RangeNotSatisfiable()
    return start, end


def iter_file(path: Path, start: int, end: int, chunk: int = 256 * 1024):
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            block = fh.read(min(chunk, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


# --------------------------------------------------------------------------- storage


class MediaStore:
    """Files under <root>/<random>.<ext>; names never come from the client."""

    def __init__(self, root: Path):
        self.root = root

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, file_name: str) -> Path:
        if not FILE_RE.fullmatch(file_name or ""):
            raise InvalidMedia("bad_file_name", "Некорректное имя файла в хранилище.")
        root = self.root.resolve()
        p = root / file_name
        if p.is_symlink() or p.resolve().parent != root:
            raise InvalidMedia("bad_file_name", "Файл вне хранилища.")
        return p

    def write(self, ext: str, data: bytes) -> tuple[str, str]:
        """Atomic write (tmp + fsync + replace). Returns (file_name, sha256)."""
        self.ensure()
        name = f"{secrets.token_hex(16)}.{ext}"
        final = self.path(name)
        tmp = final.with_name(name + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, final)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return name, hashlib.sha256(data).hexdigest()

    def remove(self, file_name: str) -> None:
        try:
            self.path(file_name).unlink()
        except (FileNotFoundError, InvalidMedia):
            pass

    def cleanup_tmp(self) -> int:
        n = 0
        if not self.root.is_dir():
            return 0
        for p in self.root.iterdir():
            if p.name.endswith(".tmp") and p.is_file() and not p.is_symlink():
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        return n
