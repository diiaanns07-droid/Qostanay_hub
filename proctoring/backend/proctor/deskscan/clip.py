"""Desk-scan clip (owner: A15): A02 ``export_clip_result`` (MJPG .avi) -> WebM/VP8 bytes.

The contract allows evidence media types image/jpeg, video/mp4, video/webm only, and the report is
shown in Chromium, so the A02 AVI is re-encoded to WebM (VP8, OpenCV's FFmpeg). The temporary AVI
and WebM files live in the OS temp directory (outside the repository) and are deleted here.
Called only when the session retains media.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("proctor.deskscan")

MAX_CLIP_BYTES = 1_900_000  # below A08 EvidenceConfig.max_snapshot_bytes (2 MB read limit)
MAX_WIDTH = 480


def _remove(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning("could not remove temporary clip %s", path.name)


def _transcode(cv2: Any, src: Path, dst: Path, step: int, max_width: int) -> bool:
    cap = cv2.VideoCapture(str(src))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 10.0)
        writer = None
        i = 0
        while True:
            ok, img = cap.read()
            if not ok:
                break
            i += 1
            if (i - 1) % step:
                continue
            h, w = img.shape[:2]
            if w > max_width:
                img = cv2.resize(img, (max_width, max(2, int(h * max_width / w) // 2 * 2)), interpolation=cv2.INTER_AREA)
            if writer is None:
                writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"VP80"), max(1.0, fps / step), (img.shape[1], img.shape[0]))
                if not writer.isOpened():
                    return False
            writer.write(img)
        if writer is None:
            return False
        writer.release()
        return dst.exists() and dst.stat().st_size > 0
    finally:
        cap.release()


def write_desk_scan_clip(capture: Any, t_end_ms: float, before_s: float, name: str) -> tuple[bytes, str, float] | None:
    """Returns (webm bytes, "video/webm", clip start session ms) or None when no clip could be made."""
    export = getattr(capture, "export_clip_result", None)
    if export is None:
        return None  # e.g. the synthetic bootstrap capture has no clip buffer
    try:
        import cv2  # noqa: PLC0415
    except Exception:
        return None
    avi: Path | None = None
    out: Path | None = None
    try:
        res = export(float(t_end_ms), float(before_s), 0.0, name=f"deskscan-{name}")
        avi = Path(res.path)
        fd, tmp = tempfile.mkstemp(prefix="qorgau-deskscan-", suffix=".webm")
        os.close(fd)
        out = Path(tmp)
        for step, width in ((1, MAX_WIDTH), (2, MAX_WIDTH), (2, 320), (4, 320)):
            if _transcode(cv2, avi, out, step, width) and out.stat().st_size <= MAX_CLIP_BYTES:
                return out.read_bytes(), "video/webm", float(res.t_first_ms)
        log.warning("desk scan clip too large after fallbacks; not kept")
        return None
    except Exception as exc:
        log.warning("desk scan clip not created: %s", exc)
        return None
    finally:
        _remove(avi)
        _remove(out)
