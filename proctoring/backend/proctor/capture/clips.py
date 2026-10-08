"""Incident clips for class mode (owner: A02; consumer: C2 student uplink, qorgau.class.v1 §5).

A rolling buffer keeps the last ``ring_s`` seconds of frames, downscaled to fit 640×360 (aspect kept:
a 4:3 camera gives 480×360) and stored as JPEG, so memory stays small and bounded:

    raw 640×360 BGR = 691 KB/frame -> 10 s × 15 fps = 104 MB (NOT what is stored)
    JPEG q70 at 480–640×360 ≈ 20–45 KB/frame -> 150 frames ≈ 3–7 MB; hard cap ``max_bytes`` (24 MB),
    the oldest frames are evicted first; at most ``max_frames`` frames regardless of size.

``ClipBuffer.export(t_center, before_s, after_s)`` waits until frames up to ``t_center + after_s``
arrived (or the timeout / end of capture), writes an MJPG ``.avi`` (``video/x-msvideo``) not larger
than ``max_file_bytes`` (8 MB, protocol limit) into a temp directory OUTSIDE the repository and
returns its path. Errors are explicit ``ClipError`` with a ``code``:

* ``no_frames``      — no buffered frame inside the requested window (camera off, window too old);
* ``invalid_argument`` — bad times;
* ``disk_full``      — less than ``min_free_bytes`` free in the output directory;
* ``disk_error``     — the file could not be written/renamed (OSError);
* ``encoder_failed`` — cv2.VideoWriter could not open / OpenCV missing;
* ``too_large``      — still above the size limit after the fallbacks (lower quality, half fps).

Timeline: ``t_center`` is SESSION time (``Incident.t_start_ms`` / ``FramePacket.t_session_ms``).
The buffer only reaches back ``ring_s`` seconds: call ``export_clip`` WHEN the incident opens and keep
the file until the teacher requests it (``request_clip``), not at request time.
Clips may show people: they stay in the temp directory, are uploaded only on request and must never
be committed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .sources import cv2

CLIP_MAX_W, CLIP_MAX_H = 640, 360
CLIP_RING_S = 10.0
CLIP_FPS = 15.0
CLIP_JPEG_QUALITY = 70
CLIP_MAX_BYTES = 24 * 1024 * 1024  # memory cap of the buffer (JPEG bytes)
CLIP_MAX_FRAMES = 600
CLIP_MAX_FILE_BYTES = 8 * 1024 * 1024  # qorgau.class.v1 §5
CLIP_MIN_FREE_BYTES = 64 * 1024 * 1024
CLIP_WAIT_GRACE_S = 3.0  # export waits after_s + this at most (wall clock)


class ClipError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ClipFrame:
    t_ms: float  # session time
    frame_id: int
    jpeg: bytes
    width: int
    height: int


@dataclass(frozen=True)
class ClipResult:
    path: Path
    frames: int
    t_first_ms: float
    t_last_ms: float
    bytes: int
    width: int
    height: int
    fps: float
    partial: bool  # the window was not fully covered (buffer too short, capture stopped, timeout)
    content_type: str = "video/x-msvideo"


def default_clip_dir() -> Path:
    return Path(tempfile.gettempdir()) / "qorgau-clips"


def fit_size(w: int, h: int, max_w: int = CLIP_MAX_W, max_h: int = CLIP_MAX_H) -> tuple[int, int]:
    """Largest even size inside max_w×max_h with the same aspect (never upscales)."""
    scale = min(1.0, max_w / w, max_h / h)
    return max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)


class ClipBuffer:
    """Thread-safe rolling JPEG buffer. ``add()`` is called by the capture's internal consumer."""

    def __init__(
        self,
        *,
        ring_s: float = CLIP_RING_S,
        jpeg_quality: int = CLIP_JPEG_QUALITY,
        max_bytes: int = CLIP_MAX_BYTES,
        max_frames: int = CLIP_MAX_FRAMES,
    ):
        if ring_s <= 0 or max_bytes <= 0 or max_frames <= 0:
            raise ValueError("ring_s, max_bytes and max_frames must be positive")
        self.ring_s = float(ring_s)
        self.jpeg_quality = int(min(95, max(10, jpeg_quality)))
        self.max_bytes = int(max_bytes)
        self.max_frames = int(max_frames)
        self._frames: deque[ClipFrame] = deque()
        self._bytes = 0
        self._cond = threading.Condition()
        self._closed = False
        self.evicted_by_size = 0

    # ------------------------------------------------------------------ feed
    def add(self, image: np.ndarray, t_ms: float, frame_id: int) -> None:
        if cv2 is None:
            return
        h, w = image.shape[:2]
        tw, th = fit_size(w, h)
        small = image if (tw, th) == (w, h) else cv2.resize(image, (tw, th), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        self.add_jpeg(ClipFrame(float(t_ms), int(frame_id), buf.tobytes(), tw, th))

    def add_jpeg(self, frame: ClipFrame) -> None:
        with self._cond:
            if self._frames and frame.t_ms <= self._frames[-1].t_ms:
                return  # never reorder the timeline
            self._frames.append(frame)
            self._bytes += len(frame.jpeg)
            horizon = frame.t_ms - self.ring_s * 1000.0
            while self._frames and self._frames[0].t_ms < horizon:
                self._bytes -= len(self._frames.popleft().jpeg)
            while self._frames and (self._bytes > self.max_bytes or len(self._frames) > self.max_frames):
                self._bytes -= len(self._frames.popleft().jpeg)
                self.evicted_by_size += 1
            self._cond.notify_all()

    def close(self) -> None:
        """Capture stopped: no more frames; waiting exports return with what they have."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    # ------------------------------------------------------------------ inspect
    def stats(self) -> dict[str, Any]:
        with self._cond:
            return {
                "frames": len(self._frames),
                "bytes": self._bytes,
                "max_bytes": self.max_bytes,
                "t_first_ms": self._frames[0].t_ms if self._frames else None,
                "t_last_ms": self._frames[-1].t_ms if self._frames else None,
                "evicted_by_size": self.evicted_by_size,
            }

    def window(self, t0: float, t1: float) -> list[ClipFrame]:
        with self._cond:
            return [f for f in self._frames if t0 <= f.t_ms <= t1]

    # ------------------------------------------------------------------ export
    def wait_until(self, t_ms: float, timeout_s: float) -> bool:
        """True when a frame at or after ``t_ms`` is buffered; False on timeout/close."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        with self._cond:
            while not (self._frames and self._frames[-1].t_ms >= t_ms):
                left = deadline - time.monotonic()
                if self._closed or left <= 0:
                    return False
                self._cond.wait(min(left, 0.25))
            return True

    def export(
        self,
        t_center_ms: float,
        before_s: float = 5.0,
        after_s: float = 5.0,
        *,
        out_dir: Path | None = None,
        name: str = "clip",
        wait: bool = True,
        max_file_bytes: int = CLIP_MAX_FILE_BYTES,
        min_free_bytes: int = CLIP_MIN_FREE_BYTES,
    ) -> ClipResult:
        if not all(np.isfinite(v) for v in (t_center_ms, before_s, after_s)) or before_s < 0 or after_s < 0 or t_center_ms < 0:
            raise ClipError("invalid_argument", "t_center_ms, before_s and after_s must be finite and >= 0")
        if before_s + after_s > self.ring_s + 1e-6:
            raise ClipError("invalid_argument", f"before_s + after_s must be <= the buffer length {self.ring_s:g} s")
        t0, t1 = t_center_ms - before_s * 1000.0, t_center_ms + after_s * 1000.0
        complete = self.wait_until(t1, after_s + CLIP_WAIT_GRACE_S) if wait else bool(self._frames and self._frames[-1].t_ms >= t1)
        frames = self.window(t0, t1)
        if not frames:
            raise ClipError("no_frames", "no buffered frames in the requested window (camera off or window older than the buffer)")
        partial = (not complete) or frames[0].t_ms - t0 > 1000.0
        return write_clip(frames, partial, out_dir or default_clip_dir(), name, max_file_bytes, min_free_bytes)


def _fps_of(frames: list[ClipFrame]) -> float:
    if len(frames) < 2:
        return CLIP_FPS
    span = (frames[-1].t_ms - frames[0].t_ms) / 1000.0
    return float(min(60.0, max(1.0, (len(frames) - 1) / span))) if span > 0 else CLIP_FPS


def _write_avi(frames: list[ClipFrame], path: Path, fps: float, quality: int, scale: float = 1.0) -> tuple[int, int]:
    w, h = fit_size(frames[0].width, frames[0].height, int(frames[0].width * scale), int(frames[0].height * scale))
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = None
    if hasattr(cv2, "CAP_OPENCV_MJPEG"):  # OpenCV's own MJPEG/AVI writer: honours the quality property
        writer = cv2.VideoWriter(str(path), cv2.CAP_OPENCV_MJPEG, fourcc, fps, (w, h))
    if writer is None or not writer.isOpened():
        writer = cv2.VideoWriter(str(path), fourcc, fps, (w, h))
    if not writer.isOpened():
        raise ClipError("encoder_failed", "cv2.VideoWriter could not open an MJPG .avi")
    try:
        if hasattr(cv2, "VIDEOWRITER_PROP_QUALITY"):
            writer.set(cv2.VIDEOWRITER_PROP_QUALITY, float(quality))
        for f in frames:
            img = cv2.imdecode(np.frombuffer(f.jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            if (img.shape[1], img.shape[0]) != (w, h):  # resolution changed after a reconnect
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            writer.write(img)
    finally:
        writer.release()
    return w, h


def write_clip(
    frames: list[ClipFrame],
    partial: bool,
    out_dir: Path,
    name: str = "clip",
    max_file_bytes: int = CLIP_MAX_FILE_BYTES,
    min_free_bytes: int = CLIP_MIN_FREE_BYTES,
) -> ClipResult:
    if cv2 is None:
        raise ClipError("encoder_failed", "OpenCV (cv2) is not installed")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:64] or "clip"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(out_dir).free
    except OSError as exc:
        raise ClipError("disk_error", f"clip directory is not usable: {type(exc).__name__}") from None
    if free < min_free_bytes:
        raise ClipError("disk_full", f"only {free // 2**20} MB free in the clip directory")
    final = out_dir / f"{safe}-{uuid.uuid4().hex[:8]}.avi"
    part = final.with_suffix(".part.avi")
    fps = _fps_of(frames)
    # fallbacks keep the protocol limit: lower JPEG quality, every 2nd frame at half fps, half size
    attempts = [(frames, fps, 75, 1.0), (frames, fps, 50, 1.0), (frames[::2], fps / 2.0, 50, 1.0), (frames[::2], fps / 2.0, 40, 0.5)]
    try:
        for seq, rate, quality, scale in attempts:
            w, h = _write_avi(seq, part, rate, quality, scale)
            size = part.stat().st_size
            if size <= max_file_bytes:
                os.replace(part, final)
                return ClipResult(final, len(seq), seq[0].t_ms, seq[-1].t_ms, size, w, h, round(rate, 2), partial)
        raise ClipError("too_large", f"clip is {size // 2**20} MB after all fallbacks (limit {max_file_bytes // 2**20} MB)")
    except OSError as exc:
        raise ClipError("disk_error", f"writing the clip failed: {type(exc).__name__}") from None
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
