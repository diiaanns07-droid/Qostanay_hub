"""Test helpers for A03 phone tests (import as proctor.phone.tests.helpers). No CV accuracy here."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from proctor.phone.detector import RawDetection
from proctor.phone.manifest import load_manifest, verify_model_file
from proctor.settings import Settings
from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import FramePacketMeta, SourceMode

SESSION = "s-phone-test"
WALL0 = datetime(2026, 10, 9, 9, 0, 0, tzinfo=timezone.utc)
FRAME_MS = 125.0  # 8 fps, settings.phone_max_fps


def make_image(width: int = 640, height: int = 480, value: int | None = None, seed: int = 0) -> np.ndarray:
    """Read-only BGR uint8 frame. Default: textured noise (usable quality); value=N: uniform frame."""
    if value is None:
        rng = np.random.default_rng(seed)
        base = rng.integers(60, 190, size=(height // 8 + 1, width // 8 + 1, 3), dtype=np.uint8)
        image = np.ascontiguousarray(np.repeat(np.repeat(base, 8, axis=0), 8, axis=1)[:height, :width])
    else:
        image = np.full((height, width, 3), value, dtype=np.uint8)
    image.flags.writeable = False
    return image


def make_frame(
    frame_id: int,
    t_ms: float | None = None,
    image: np.ndarray | None = None,
    *,
    session_id: str = SESSION,
    mode: SourceMode = SourceMode.REPLAY,
    capture_age_ms: float = 0.0,
    width: int | None = None,
    height: int | None = None,
) -> FramePacket:
    if image is None:
        image = make_image()
    t = frame_id * FRAME_MS if t_ms is None else t_ms
    h = height if height is not None else (image.shape[0] if getattr(image, "ndim", 0) >= 2 and image.shape[0] > 0 else 480)
    w = width if width is not None else (image.shape[1] if getattr(image, "ndim", 0) >= 2 and image.shape[1] > 0 else 640)
    meta = FramePacketMeta(
        session_id=session_id,
        frame_id=frame_id,
        t_session_ms=t,
        wall_time=WALL0 + timedelta(milliseconds=t),
        t_capture_mono_ns=max(0, time.monotonic_ns() - int(capture_age_ms * 1e6)),
        width=w,
        height=h,
        source_mode=mode,
        source_id="replay:phone-test" if mode == SourceMode.REPLAY else f"{mode.value}:test",
    )
    return FramePacket(meta=meta, image=image)


def box(cx: float, cy: float, w: float = 0.10, h: float = 0.18, conf: float = 0.6, cls: int = 67, name: str = "cell phone") -> RawDetection:
    """Fake phone box from center/size in normalized coordinates (clipped to [0,1])."""
    return RawDetection(
        max(0.0, cx - w / 2), max(0.0, cy - h / 2), min(1.0, cx + w / 2), min(1.0, cy + h / 2), conf, cls, name
    )


@dataclass
class FakeDetector:
    """Returns scripted boxes. ``script`` maps call index -> boxes, or is a callable(call_index)."""

    script: dict[int, list[RawDetection]] | Callable[[int], list[RawDetection]] | None = None
    fail_on: set[int] | None = None
    calls: int = 0

    def detect(self, image_bgr: np.ndarray):
        i = self.calls
        self.calls += 1
        if self.fail_on and i in self.fail_on:
            raise RuntimeError("fake inference failure")
        if callable(self.script):
            dets = self.script(i)
        else:
            dets = (self.script or {}).get(i, [])
        return list(dets), {"pre_ms": 0.1, "infer_ms": 1.0, "post_ms": 0.1}


def installed_model_dir() -> Path | None:
    models_dir = Settings().models_dir
    try:
        return models_dir if verify_model_file(models_dir, load_manifest()).ok else None
    except Exception:
        return None
