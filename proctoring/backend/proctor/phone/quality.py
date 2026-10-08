"""Cheap input-quality estimate for one frame (owner: A03).

Measured on a small gray thumbnail: mean brightness, standard deviation (contrast) and variance of the
Laplacian (sharpness). quality = the weakest of three [0,1] scores. These are heuristics for flagging
"the detector may miss things here", NOT a measured detector-recall model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import PhoneConfig


@dataclass(frozen=True)
class FrameQuality:
    quality: float  # [0,1]
    usable: bool  # False -> nothing can be determined from this frame (signals become "unknown")
    flags: tuple[str, ...]
    mean: float
    std: float
    laplacian_var: float | None


def _ramp(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 1.0 if value >= hi else 0.0
    return float(min(1.0, max(0.0, (value - lo) / (hi - lo))))


def assess(image_bgr: np.ndarray, cfg: PhoneConfig, cv2: Any | None) -> FrameQuality:
    h, w = image_bgr.shape[:2]
    step = max(1, w // max(16, cfg.quality_thumb_width))
    thumb = image_bgr[::step, ::step]  # strided view: no copy of the shared frame
    gray = thumb.astype(np.float32) @ np.array([0.114, 0.587, 0.299], dtype=np.float32)  # BGR weights
    mean, std = float(gray.mean()), float(gray.std())
    lap_var: float | None = None
    if cv2 is not None and min(gray.shape) >= 3:
        lap_var = float(cv2.Laplacian(gray, cv2.CV_32F).var())

    flags: list[str] = []
    usable = True
    if mean < cfg.unusable_mean_low:
        flags.append("low_light")
        usable = False
    elif mean < cfg.dark_mean:
        flags.append("low_light")
    if mean > cfg.bright_mean:
        flags.append("overexposed")
    if std < cfg.unusable_std:
        flags.append("blank_frame")
        usable = False
    elif std < cfg.low_contrast_std:
        flags.append("low_contrast")
    if lap_var is not None and usable and lap_var < cfg.blur_laplacian_var:
        flags.append("blur")

    brightness = min(_ramp(mean, cfg.unusable_mean_low, cfg.dark_mean + 15.0), 1.0 - _ramp(mean, cfg.bright_mean - 15.0, 254.0))
    contrast = _ramp(std, cfg.unusable_std, cfg.low_contrast_std + 8.0)
    sharpness = 1.0 if lap_var is None else _ramp(lap_var, 0.0, cfg.blur_laplacian_var * 4.0)
    quality = 0.0 if not usable else round(min(brightness, contrast, sharpness), 3)
    return FrameQuality(quality, usable, tuple(flags), round(mean, 1), round(std, 1), None if lap_var is None else round(lap_var, 1))
