"""Frame sources (owner: A02): live camera and synthetic test frames.

A source is owned by exactly one capture thread for its whole life (open -> read* -> close),
so OpenCV objects are never touched from two threads. Sources return raw BGR frames plus
the monotonic time at which ``read()`` returned; the service turns them into FramePackets
(frame_id, session timeline, wall time).

* ``CameraSource`` — the ONLY place in the product that calls ``cv2.VideoCapture`` on a
  device. Device open, first-frame probe and failure classification (no camera / denied /
  busy) happen here; disconnects are reported as ``SourceDisconnected`` so the service can
  reconnect. The OpenCV object factory is injectable for tests (``opener``).
* ``SyntheticSource`` — deterministic generated frames for tests (content depends only on
  the frame index). Clearly labelled; never used for live or replay.

Mirroring: frames are delivered exactly as the device/file provides them and are assumed
UNMIRRORED (camera view, the student's right hand appears on the image's left). A driver
that mirrors in hardware cannot be detected automatically — see the live checklist.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import numpy as np

from proctor_contracts.interfaces import CaptureError
from proctor_contracts.v1 import ErrorCode, SourceMode

try:  # cv2 comes from the "cv" extra (opencv-contrib-python); synthetic works without it
    import cv2  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    cv2 = None

log = logging.getLogger("proctor.capture")

FactDict = dict[str, bool | int | float | str]


class SourceDisconnected(Exception):
    """A live device stopped delivering frames (unplugged, driver reset, taken over)."""


class SourceEnded(Exception):
    """A finite source (replay without loop) reached its end."""


@dataclass(slots=True)
class RawFrame:
    image: np.ndarray  # uint8 HxWx3 BGR, C-contiguous, freshly allocated (never reused)
    mono_ns: int  # time.monotonic_ns() when read() produced the frame (latency anchor)
    media_pts_ms: float | None = None  # replay: timestamp relative to the replay start
    media_index: int | None = None  # replay: stable frame index in the media (incl. loops)


class FrameSource(Protocol):
    mode: SourceMode
    source_id: str
    supports_reconnect: bool
    dropped: int  # frames discarded by the source (replay late-skip, undecodable)

    def open(self, stop: threading.Event, deadline: float) -> None:
        """Open; raise CaptureError. ``deadline`` is a time.monotonic() value."""
        ...

    def read(self) -> RawFrame | None:
        """Next frame; None = nothing now (check stop). Raises SourceDisconnected/SourceEnded."""
        ...

    def close(self) -> None: ...

    def describe(self) -> FactDict: ...


def normalize_bgr(image: Any) -> np.ndarray | None:
    """Return uint8 HxWx3 BGR C-contiguous, or None for an unusable frame."""
    if not isinstance(image, np.ndarray) or image.size == 0 or image.dtype != np.uint8:
        return None
    if image.ndim == 2:
        if cv2 is None:
            image = np.repeat(image[:, :, None], 3, axis=2)
        else:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        image = np.ascontiguousarray(image[:, :, :3]) if cv2 is None else cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    elif image.ndim == 3 and image.shape[2] == 1:
        image = np.repeat(image, 3, axis=2)
    elif not (image.ndim == 3 and image.shape[2] == 3):
        return None
    h, w = image.shape[:2]
    if not (0 < w <= 7680 and 0 < h <= 4320):
        return None
    if not image.flags.c_contiguous:
        image = np.ascontiguousarray(image)
    return image


def require_cv2(code: ErrorCode) -> Any:
    if cv2 is None:
        raise CaptureError(code, "OpenCV (cv2) is not installed: install the 'cv' extra", reason="opencv_missing")
    return cv2


# ---------------------------------------------------------------------------
# Live camera
# ---------------------------------------------------------------------------


class VideoCaptureLike(Protocol):
    def isOpened(self) -> bool: ...  # noqa: N802 - OpenCV naming

    def read(self) -> tuple[bool, Any]: ...

    def set(self, prop: int, value: float) -> bool: ...

    def get(self, prop: int) -> float: ...

    def release(self) -> None: ...

    def getBackendName(self) -> str: ...  # noqa: N802


Opener = Callable[[int, int], VideoCaptureLike | None]


def default_backends(platform: str = sys.platform) -> list[int]:
    """Backend preference. Windows: DirectShow opens fast and supports MJPG; MSMF next."""
    if cv2 is None:
        return []
    if platform == "win32":
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF]
    if platform.startswith("linux"):
        return [cv2.CAP_V4L2]
    if platform == "darwin":
        return [cv2.CAP_AVFOUNDATION]
    return [cv2.CAP_ANY]


def _default_opener(index: int, backend: int) -> VideoCaptureLike:
    return cv2.VideoCapture(index, backend)


def _backend_label(backend: int) -> str:
    if cv2 is None:
        return str(backend)
    names = {getattr(cv2, n): n[4:] for n in ("CAP_ANY", "CAP_DSHOW", "CAP_MSMF", "CAP_V4L2", "CAP_AVFOUNDATION") if hasattr(cv2, n)}
    return names.get(backend, str(backend))


def windows_camera_consent(winreg_module: Any = None, platform: str = sys.platform) -> str | None:
    r"""Read-only diagnosis of the Windows camera privacy switches ("Allow"/"Deny"/None).

    Only READS ...\CapabilityAccessManager\ConsentStore\webcam ``Value`` under HKLM ("camera access
    for this device") and HKCU ("let apps access your camera"), plus their ``NonPackaged`` subkey
    ("let desktop apps access your camera", which covers this Python backend). Never writes.
    Any error -> None (unknown). Called only after an open failure, to choose CAMERA_DENIED
    over CAMERA_UNAVAILABLE. ``winreg_module`` is injectable for tests.
    """
    if platform != "win32":
        return None
    try:
        winreg = winreg_module
        if winreg is None:
            import winreg  # type: ignore[no-redef]

        base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
        seen: list[str] = []
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (base, base + r"\NonPackaged"):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        value, _ = winreg.QueryValueEx(key, "Value")
                        if isinstance(value, str):
                            seen.append(value)
                except OSError:
                    continue
        if any(v.lower() == "deny" for v in seen):
            return "Deny"
        if seen:
            return "Allow"
    except Exception:
        return None
    return None


class CameraSource:
    """Live webcam via OpenCV. Single-thread owned (the capture thread)."""

    mode = SourceMode.LIVE
    supports_reconnect = True

    #: consecutive failed reads that make a frame loss a disconnect
    FAILS_TO_DISCONNECT = 3
    #: ... or this long without a successful read
    FAIL_WINDOW_S = 1.0
    #: DirectShow while another app holds the camera (measured: Windows 11, OpenCV 4.13, UVC webcam):
    #: isOpened() is True, read() blocks ~1000 ms, then returns ok=True with an all-black frame.
    #: A near-black frame that took this long is that placeholder, not a picture (a real camera,
    #: even with a closed privacy shutter, delivers frames every ~33 ms).
    BUSY_READ_S = 0.5
    BUSY_MAX_MEAN = 1.0
    #: placeholders in a row that prove the device is busy (other backends are then not tried)
    BUSY_PLACEHOLDERS = 2

    def __init__(
        self,
        index: int,
        width: int,
        height: int,
        fps: int,
        *,
        opener: Opener | None = None,
        backends: list[int] | None = None,
        probe_timeout_s: float = 3.0,
        platform: str = sys.platform,
        consent_probe: Callable[[], str | None] = windows_camera_consent,
        device_path_probe: Callable[[int], tuple[bool, bool] | None] | None = None,
    ):
        self.index = index
        self.width, self.height, self.fps = width, height, fps
        self.source_id = f"camera:{index}"
        self.dropped = 0
        self._opener = opener
        self._backends = backends
        self._probe_timeout_s = probe_timeout_s
        self._platform = platform
        self._consent_probe = consent_probe
        self._device_path_probe = device_path_probe or self._linux_device_probe
        self._cap: VideoCaptureLike | None = None
        self._first: RawFrame | None = None
        self._stop = threading.Event()
        self._backend_name = ""
        self._actual: FactDict = {}
        self._last_ok = 0.0
        self.opens = 0
        self.busy_placeholders = 0  # black "camera busy" frames seen (never delivered)

    # -------------------------------------------------------------- probing
    def _linux_device_probe(self, index: int) -> tuple[bool, bool] | None:
        """(exists, accessible) for /dev/video<index> on Linux; None elsewhere."""
        if not self._platform.startswith("linux"):
            return None
        path = f"/dev/video{index}"
        exists = os.path.exists(path)
        return exists, exists and os.access(path, os.R_OK | os.W_OK)

    def open(self, stop: threading.Event, deadline: float) -> None:
        self._stop = stop
        if self._opener is None:
            require_cv2(ErrorCode.CAMERA_UNAVAILABLE)
            self._opener = _default_opener
        backends = self._backends if self._backends is not None else default_backends(self._platform)
        if not backends:
            backends = [0]
        tried: list[str] = []
        opened_without_frames = False
        out_of_time = False
        for i, backend in enumerate(backends):
            if stop.is_set():
                break
            label = _backend_label(backend)
            if time.monotonic() >= deadline - 0.2:
                tried.append(f"{label}:no_time")
                out_of_time = True
                continue
            try:
                cap = self._opener(self.index, backend)
            except Exception as exc:  # OpenCV may raise on invalid index/backend
                tried.append(f"{label}:error")
                log.info("camera %d backend %s raised %s", self.index, label, type(exc).__name__)
                continue
            if cap is None or not cap.isOpened():
                tried.append(f"{label}:not_opened")
                self._safe_release(cap)
                continue
            self._configure(cap)
            # split what is left of the budget between this and the remaining backends, so a busy
            # camera (opens, no frames) on every backend is still classified before the deadline
            left = deadline - 0.2 - time.monotonic()
            if left <= 0:  # the device open itself used the whole budget: frames were never probed
                tried.append(f"{label}:open_too_slow")
                out_of_time = True
                self._safe_release(cap)
                continue
            budget = min(self._probe_timeout_s, left / (len(backends) - i))
            first, busy = self._probe_first_frame(cap, time.monotonic() + budget)
            if first is None:
                tried.append(f"{label}:{'busy_placeholder' if busy else 'no_frames'}")
                opened_without_frames = True
                self._safe_release(cap)
                if busy:  # the device itself is held by another app: other backends cannot get it
                    break
                continue
            self._cap = cap
            self._first = first
            self._last_ok = time.monotonic()
            try:
                self._backend_name = str(cap.getBackendName())
            except Exception:
                self._backend_name = label
            self._read_back(cap, first.image)
            self.opens += 1
            log.info("camera %d opened via %s: %s", self.index, self._backend_name, self._actual)
            return
        raise self._classify_failure(tried, opened_without_frames, out_of_time)

    def _configure(self, cap: VideoCaptureLike) -> None:
        if cv2 is None:
            return
        try:
            if self.width * self.height > 640 * 480 and self._platform == "win32":
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # USB bandwidth at >VGA
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
            cap.set(cv2.CAP_PROP_FPS, float(self.fps))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1.0)  # fewer stale driver frames (ignored by some backends)
        except Exception:
            log.info("camera %d: some properties could not be set", self.index)

    def _is_busy_placeholder(self, frame: np.ndarray, read_s: float) -> bool:
        if read_s < self.BUSY_READ_S or float(frame.mean()) >= self.BUSY_MAX_MEAN:
            return False
        self.busy_placeholders += 1
        return True

    def _probe_first_frame(self, cap: VideoCaptureLike, until: float) -> tuple[RawFrame | None, bool]:
        """(first frame, device proven busy)."""
        busy_in_row = 0
        while time.monotonic() < until and not self._stop.is_set():
            t_read = time.monotonic()
            try:
                ok, img = cap.read()
            except Exception:
                ok, img = False, None
            mono = time.monotonic_ns()
            frame = normalize_bgr(img) if ok else None
            if frame is not None and self._is_busy_placeholder(frame, time.monotonic() - t_read):
                busy_in_row += 1
                if busy_in_row >= self.BUSY_PLACEHOLDERS:
                    return None, True
                continue
            if frame is not None:
                return RawFrame(image=frame, mono_ns=mono), False
            busy_in_row = 0
            time.sleep(0.05)
        return None, busy_in_row > 0

    def _read_back(self, cap: VideoCaptureLike, image: np.ndarray) -> None:
        h, w = image.shape[:2]
        actual: FactDict = {"actual_width": int(w), "actual_height": int(h)}
        if cv2 is not None:
            try:
                fps = float(cap.get(cv2.CAP_PROP_FPS))
                if fps > 0:
                    actual["driver_fps"] = round(fps, 2)
            except Exception:
                pass
        self._actual = actual

    def _classify_failure(self, tried: list[str], opened_without_frames: bool, out_of_time: bool = False) -> CaptureError:
        details = {"camera_index": self.index, "backends_tried": ",".join(tried)[:200]}
        if opened_without_frames:
            return CaptureError(
                ErrorCode.CAMERA_BUSY,
                "Camera opened but delivered no frames (it may be used by another application)",
                retryable=True,
                reason="camera_no_frames",
                **details,
            )
        if out_of_time:
            return CaptureError(
                ErrorCode.CAMERA_UNAVAILABLE,
                "Opening the camera took longer than the time budget (slow driver or device busy)",
                retryable=True,
                reason="open_timeout",
                **details,
            )
        probe = self._device_path_probe(self.index)
        if probe is not None:
            exists, accessible = probe
            if not exists:
                return CaptureError(
                    ErrorCode.CAMERA_UNAVAILABLE, f"No camera device with index {self.index}", reason="camera_not_found", **details
                )
            if not accessible:
                return CaptureError(
                    ErrorCode.CAMERA_DENIED, "Access to the camera device is denied by the OS", reason="camera_permission_denied", **details
                )
        if self._platform == "win32" and self._consent_probe() == "Deny":
            return CaptureError(
                ErrorCode.CAMERA_DENIED,
                "Camera access is turned off in Windows privacy settings",
                reason="camera_privacy_denied",
                **details,
            )
        return CaptureError(
            ErrorCode.CAMERA_UNAVAILABLE,
            "Camera could not be opened (not connected, used exclusively by another app, or blocked)",
            retryable=True,
            reason="camera_open_failed",
            **details,
        )

    # ---------------------------------------------------------------- frames
    def read(self) -> RawFrame | None:
        if self._first is not None:
            frame, self._first = self._first, None
            return frame
        cap = self._cap
        if cap is None:
            raise SourceDisconnected("camera is not open")
        fails = 0
        while not self._stop.is_set():
            t_read = time.monotonic()
            try:
                ok, img = cap.read()
            except Exception:
                ok, img = False, None
            mono = time.monotonic_ns()
            frame = normalize_bgr(img) if ok else None
            if frame is not None and self._is_busy_placeholder(frame, time.monotonic() - t_read):
                frame = None  # black "busy" placeholder: a lost frame, never a picture for analyzers
            if frame is not None:
                self._last_ok = time.monotonic()
                return RawFrame(image=frame, mono_ns=mono)
            if ok:  # a frame came but is unusable (empty/odd shape, busy placeholder)
                self.dropped += 1
            fails += 1
            if fails >= self.FAILS_TO_DISCONNECT or time.monotonic() - self._last_ok > self.FAIL_WINDOW_S:
                raise SourceDisconnected(f"camera {self.index}: {fails} failed reads")
            self._stop.wait(0.02)
        return None

    def close(self) -> None:
        cap, self._cap = self._cap, None
        self._first = None
        self._safe_release(cap)

    @staticmethod
    def _safe_release(cap: VideoCaptureLike | None) -> None:
        if cap is None:
            return
        try:
            cap.release()
        except Exception:
            log.info("camera release raised", exc_info=True)

    def describe(self) -> FactDict:
        return {
            "camera_index": self.index,
            "backend": self._backend_name or "none",
            "requested": f"{self.width}x{self.height}@{self.fps}",
            **self._actual,
        }


# ---------------------------------------------------------------------------
# Synthetic
# ---------------------------------------------------------------------------


class SyntheticSource:
    """Deterministic generated frames (test mode). Content depends only on the frame index."""

    mode = SourceMode.SYNTHETIC
    supports_reconnect = False
    source_id = "synthetic:capture"

    def __init__(self, width: int, height: int, fps: float):
        self.width, self.height = width, height
        self.fps = max(0.1, float(fps))
        self.dropped = 0
        self._stop = threading.Event()
        self._base: np.ndarray | None = None
        self._index = 0
        self._next_due = 0.0

    def open(self, stop: threading.Event, deadline: float) -> None:
        self._stop = stop
        w, h = self.width, self.height
        ramp = np.linspace(40, 170, w, dtype=np.float32).astype(np.uint8)  # mean ~105: lighting check passes
        self._base = np.ascontiguousarray(np.broadcast_to(ramp[None, :, None], (h, w, 3)))
        self._index = 0
        self._next_due = time.monotonic()

    @staticmethod
    def render(base: np.ndarray, index: int) -> np.ndarray:
        img = base.copy()
        h, w = img.shape[:2]
        bw, bh = max(8, w // 10), max(8, h // 8)
        x = (index * 7) % max(w - bw, 1)
        y = h // 2 - bh // 2
        img[y : y + bh, x : x + bw] = (200, 200, 200)
        if cv2 is not None:
            cv2.putText(img, f"SYNTHETIC #{index}", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
        return img

    def read(self) -> RawFrame | None:
        assert self._base is not None, "open() first"
        delay = self._next_due - time.monotonic()
        if delay > 0 and self._stop.wait(delay):
            return None
        now = time.monotonic()
        # fixed schedule; after a long stall resync instead of bursting to catch up
        self._next_due = self._next_due + 1.0 / self.fps if now - self._next_due < 1.0 / self.fps else now + 1.0 / self.fps
        img = self.render(self._base, self._index)
        self._index += 1
        return RawFrame(image=img, mono_ns=time.monotonic_ns())

    def close(self) -> None:
        self._base = None

    def describe(self) -> FactDict:
        return {"requested": f"{self.width}x{self.height}@{self.fps:g}", "generator": "gradient_block_v1"}
