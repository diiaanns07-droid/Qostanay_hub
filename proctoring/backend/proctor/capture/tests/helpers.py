"""Test helpers for A02 capture tests: fake camera devices, test clips, replay manifests.

Nothing here touches a real camera. FakeDevice emulates the OpenCV VideoCapture behaviours
that matter for the capture state machine: not opened, opened-without-frames (busy), frame
loss (disconnect), a read() that hangs (driver stall), odd pixel formats.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np


def wait_until(pred: Callable[[], bool], timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


class FakeDevice:
    """State of one emulated camera; ``opener`` hands out FakeVideoCapture handles."""

    def __init__(self, *, fps: float = 30.0, width: int = 640, height: int = 480):
        self.fps = fps
        self.width, self.height = width, height
        self.connected = True  # False: isOpened() false for new handles, read() fails
        self.delivers = True  # False: opens but read() returns (False, None) -> busy
        self.mode = "bgr"  # "bgr" | "gray" | "bgra" | "empty" | "busy_black"
        # "busy_black" = DirectShow while another app holds the camera (measured on Windows 11):
        # isOpened() True, read() blocks ~1 s and returns ok=True with an all-black frame
        self.busy_read_s = 0.6
        self.open_delay_s = 0.0
        self.unblocked = threading.Event()
        self.unblocked.set()  # clear() -> read() hangs until set()
        self.lock = threading.Lock()
        self.opens = 0
        self.releases = 0
        self.reads = 0
        self.props: dict[int, float] = {}
        self.backends_seen: list[int] = []
        self.fail_backends: set[int] = set()
        self._n = 0

    @property
    def active_handles(self) -> int:
        with self.lock:
            return self.opens - self.releases

    def frame(self) -> np.ndarray:
        with self.lock:
            n = self._n
            self._n += 1
        img = np.full((self.height, self.width, 3), (n * 3) % 200 + 30, np.uint8)
        if self.mode == "gray":
            return img[:, :, 0].copy()
        if self.mode == "bgra":
            return np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
        if self.mode == "empty":
            return np.zeros((0, 0, 3), np.uint8)
        return img

    def opener(self, index: int, backend: int) -> "FakeVideoCapture":
        self.backends_seen.append(backend)
        if self.open_delay_s:
            time.sleep(self.open_delay_s)
        return FakeVideoCapture(self, opened=self.connected and backend not in self.fail_backends)


class FakeVideoCapture:
    def __init__(self, device: FakeDevice, opened: bool):
        self.d = device
        self._opened = opened
        self._released = False
        if opened:
            with device.lock:
                device.opens += 1

    def isOpened(self) -> bool:  # noqa: N802
        return self._opened and not self._released

    def read(self) -> tuple[bool, Any]:
        d = self.d
        d.unblocked.wait()
        time.sleep(1.0 / d.fps)
        with d.lock:
            d.reads += 1
        if not self._opened or self._released or not d.connected or not d.delivers:
            return False, None
        if d.mode == "busy_black":
            time.sleep(d.busy_read_s)
            return True, np.zeros((d.height, d.width, 3), np.uint8)
        return True, d.frame()

    def set(self, prop: int, value: float) -> bool:
        self.d.props[prop] = value
        return True

    def get(self, prop: int) -> float:
        return float(self.d.props.get(prop, 0.0))

    def getBackendName(self) -> str:  # noqa: N802
        return "FAKE"

    def release(self) -> None:
        if self._opened and not self._released:
            with self.d.lock:
                self.d.releases += 1
        self._released = True


def camera_kwargs(device: FakeDevice, **extra: Any) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "opener": device.opener,
        "backends": [101],
        "platform": "test",
        "probe_timeout_s": 1.0,
        "device_path_probe": lambda i: None,
        "consent_probe": lambda: None,
    }
    kw.update(extra)
    return kw


def frame_pattern(index: int, width: int, height: int) -> np.ndarray:
    """Deterministic test frame: index encoded in a bright bar (survives JPEG/MJPG)."""
    img = np.full((height, width, 3), 60, np.uint8)
    x = (index * 9) % max(1, width - 20)
    img[:, x : x + 20] = 220
    img[: height // 8, : width // 4] = (0, 0, 255)  # red block top-left (mirroring checks)
    return img


def write_video(path: Path, n: int = 50, fps: float = 25.0, width: int = 320, height: int = 240, fourcc: str = "MJPG") -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, (width, height))
    assert writer.isOpened(), "cv2.VideoWriter could not open (FFmpeg missing?)"
    for i in range(n):
        writer.write(frame_pattern(i, width, height))
    writer.release()
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(replay_dir: Path, replay_id: str, media: dict[str, Any], **fields: Any) -> Path:
    doc = {"format": "qorgau.replay.v1", "replay_id": replay_id, "media": media, **fields}
    path = replay_dir / f"{replay_id}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path
