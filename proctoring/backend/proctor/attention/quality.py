"""Measurable image quality signals (heuristics with documented thresholds, not learned).

* luma: mean of the BT.601 gray image, 0..255;
* sharpness: variance of the Laplacian of a fixed-size gray crop (higher = sharper);
* face size: bbox height in pixels; partial: share of landmarks outside the frame.
Thresholds live in AttentionConfig and were set on public test images, not exam recordings.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .config import AttentionConfig


@dataclass(frozen=True)
class FrameStats:
    luma: float
    sharpness: float


def frame_stats(image_bgr: np.ndarray) -> FrameStats:
    h, w = image_bgr.shape[:2]
    scale = 160.0 / max(w, 1)
    small = cv2.resize(image_bgr, (160, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return FrameStats(float(gray.mean()), float(cv2.Laplacian(gray, cv2.CV_32F).var()))


@dataclass(frozen=True)
class FaceStats:
    luma: float
    sharpness: float
    height_px: float


def face_stats(image_bgr: np.ndarray, bbox: tuple[float, float, float, float]) -> FaceStats:
    h, w = image_bgr.shape[:2]
    x0, y0 = int(bbox[0] * w), int(bbox[1] * h)
    x1, y1 = int(np.ceil(bbox[2] * w)), int(np.ceil(bbox[3] * h))
    height = float(max(0, y1 - y0))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return FaceStats(0.0, 0.0, height)
    crop = image_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    target_h = 112
    gray = cv2.resize(gray, (max(4, int(round(gray.shape[1] * target_h / gray.shape[0]))), target_h), interpolation=cv2.INTER_AREA)
    return FaceStats(float(gray.mean()), float(cv2.Laplacian(gray, cv2.CV_32F).var()), height)


def no_face_trust(stats: FrameStats, cfg: AttentionConfig) -> list[str]:
    """Flags that make "no face in this frame" untrustworthy (then face_count = None)."""
    flags = []
    if stats.luma < cfg.frame_dark_mean:
        flags.append("low_light")
    if stats.sharpness < cfg.frame_blur_laplacian_var:
        flags.append("blur")
    return flags


def face_quality(
    face: FaceStats, outside: float, eye_width_px: float, extreme_pose: bool, cfg: AttentionConfig
) -> tuple[float, list[str]]:
    """Quality score in [0, 1] (product of soft factors) + flags with a measurable basis."""
    flags: list[str] = []
    q = 1.0
    if face.luma < cfg.face_dark_mean:
        flags.append("low_light")
        q *= max(0.15, face.luma / cfg.face_dark_mean)
    elif face.luma > cfg.face_bright_mean:
        flags.append("overexposed")
        q *= 0.6
    if face.sharpness < cfg.blur_laplacian_var:
        flags.append("blur")
        q *= max(0.3, face.sharpness / cfg.blur_laplacian_var)
    if face.height_px < cfg.small_face_px:
        flags.append("small_face")
        q *= max(0.3, face.height_px / cfg.small_face_px)
    if outside > cfg.partial_face_ratio:
        flags.append("partial_face")
        q *= max(0.2, 1.0 - outside)
    if eye_width_px < cfg.min_eye_width_px:
        flags.append("small_eyes")
        q *= 0.8
    if extreme_pose:
        flags.append("extreme_pose")
        q *= 0.7
    return float(min(1.0, max(0.0, q))), flags
