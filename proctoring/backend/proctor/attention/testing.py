"""Test doubles for A04 (no MediaPipe, no model, no face images).

synthetic_face() renders a coarse 3D head (key MediaPipe indices + an oval) with a KNOWN head
pose and eye rotation into normalized landmarks, a matching facial transformation matrix and
ARKit-style eye blendshapes. It exercises the geometry/tracking/calibration/gaze code with
ground truth; it says nothing about how well the real model measures real people.
FakeFaceBackend plays such faces back. Usable by A01/A09 integration tests as well.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

import numpy as np

from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import FramePacketMeta, SourceMode

from . import geometry as g
from .landmarker import RawFace

VFOV_DEG = 63.0  # MediaPipe geometry pipeline default vertical field of view
FACE_HEIGHT_CM = 18.0
EYEBALL_RADIUS_CM = 1.2


def _template() -> np.ndarray:
    """478 points in face coordinates (cm): x = image right for a frontal face (the subject's
    LEFT), y up, z towards the camera."""
    pts = np.zeros((g.NUM_WITH_IRIS, 3), dtype=np.float64)
    theta = np.linspace(0.0, 2.0 * math.pi, g.NUM_MESH, endpoint=False)
    pts[: g.NUM_MESH, 0] = 7.0 * np.cos(theta)
    pts[: g.NUM_MESH, 1] = -1.5 + 9.0 * np.sin(theta)
    pts[: g.NUM_MESH, 2] = -1.0
    fixed = {
        g.NOSE_TIP: (0.0, -2.5, 2.5),
        g.FOREHEAD: (0.0, 7.5, 0.0),
        g.CHIN: (0.0, -10.5, 0.0),
        g.CHEEK_RIGHT: (-7.0, -1.5, -2.0),
        g.CHEEK_LEFT: (7.0, -1.5, -2.0),
        # corners slightly below the iris center and the inner corner more medial, which
        # reproduces the rest offsets measured on real faces (h ~ -/+0.06, v ~ -0.07)
        g.R_EYE_OUTER: (-4.6, -0.25, 0.0),
        g.R_EYE_INNER: (-1.2, -0.25, 0.2),
        g.L_EYE_INNER: (1.2, -0.25, 0.2),
        g.L_EYE_OUTER: (4.6, -0.25, 0.0),
    }
    for i, p in fixed.items():
        pts[i] = p
    return pts


_TEMPLATE = _template()


def _eye_dir(eye_yaw: float, eye_pitch: float) -> np.ndarray:
    y, p = math.radians(eye_yaw), math.radians(eye_pitch)
    return np.array([-math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p)])


def synthetic_face(
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    *,
    eye_yaw: float = 0.0,
    eye_pitch: float = 0.0,
    center: tuple[float, float] = (0.5, 0.45),
    face_height: float = 0.5,
    width: int = 640,
    height: int = 480,
    blink: bool = False,
    blendshapes: bool = True,
    with_matrix: bool = True,
) -> RawFace:
    """A face whose head pose (ray frame, subject-centric) and eye rotation are known."""
    pts = _TEMPLATE.copy()
    d = _eye_dir(eye_yaw, eye_pitch)
    for cx, iris, ring, upper, lower in (
        (-3.1, g.R_IRIS, range(469, 473), g.R_EYE_UPPER, g.R_EYE_LOWER),
        (3.1, g.L_IRIS, range(474, 478), g.L_EYE_UPPER, g.L_EYE_LOWER),
    ):
        ball = np.array([cx, 0.0, -0.18])  # iris ~0.27 eye widths in front of the corners
        c = ball + EYEBALL_RADIUS_CM * d
        pts[iris] = c
        for k, (dx, dy) in zip(ring, ((0.55, 0), (0, 0.55), (-0.55, 0), (0, -0.55))):
            pts[k] = c + np.array([dx, dy, 0.0])
        lid_down = max(0.0, -eye_pitch) / 60.0  # lids follow the eye down a little
        gap = 0.04 if blink else 0.55
        pts[upper] = (cx, gap - 0.25 - lid_down, 0.9)
        pts[lower] = (cx, -gap - 0.25, 0.9)

    f_px = (height / 2.0) / math.tan(math.radians(VFOV_DEG / 2.0))
    z = FACE_HEIGHT_CM * f_px / (face_height * height)
    tx = (center[0] - 0.5) * width * z / f_px
    ty = -(center[1] - 0.5) * height * z / f_px
    m = g.matrix_from_pose(yaw, pitch, roll, (tx, ty, -z))
    cam = (m[:3, :3] @ pts.T).T + m[:3, 3]
    depth = -cam[:, 2]
    u = width / 2.0 + f_px * cam[:, 0] / depth
    v = height / 2.0 - f_px * cam[:, 1] / depth
    # MediaPipe z: depth relative to the face, same scale as x (normalized by width), + = farther
    lm = np.stack([u / width, v / height, (depth - z) * (f_px / z) / width], axis=1).astype(np.float32)

    bs = None
    if blendshapes:
        k = 30.0
        bs = {
            "eyeBlinkLeft": 0.9 if blink else 0.05,
            "eyeBlinkRight": 0.9 if blink else 0.05,
            "eyeLookDownLeft": 0.15 + max(0.0, -eye_pitch) / k,
            "eyeLookDownRight": 0.15 + max(0.0, -eye_pitch) / k,
            "eyeLookUpLeft": max(0.0, eye_pitch) / k,
            "eyeLookUpRight": max(0.0, eye_pitch) / k,
            "eyeLookOutRight": max(0.0, eye_yaw) / k,
            "eyeLookInLeft": max(0.0, eye_yaw) / k,
            "eyeLookOutLeft": max(0.0, -eye_yaw) / k,
            "eyeLookInRight": max(0.0, -eye_yaw) / k,
        }
        bs = {name: float(min(1.0, val)) for name, val in bs.items()}
    return RawFace(lm, m if with_matrix else None, bs)


def mirrored(face: RawFace) -> RawFace:
    """What the model reports for the horizontally mirrored image of this face."""
    bs = None
    if face.blendshapes is not None:
        swap = {"Left": "Right", "Right": "Left"}
        bs = {}
        for name, val in face.blendshapes.items():
            for a, b in swap.items():
                if name.endswith(a):
                    name = name[: -len(a)] + b
                    break
            bs[name] = val
    m = None if face.matrix is None else g.mirror_matrix(face.matrix)
    return RawFace(g.mirror_landmarks(face.landmarks), m, bs)


class FakeFaceBackend:
    """FaceBackend that returns scripted faces. script(timestamp_ms) -> faces, or set_faces()."""

    model_id = "fake-face-backend"
    model_sha256 = None

    def __init__(self, script: Callable[[int], Sequence[RawFace]] | None = None):
        self.script = script
        self.faces: list[RawFace] = []
        self.fail_next = 0
        self.resets = 0
        self.closed = False
        self.calls: list[int] = []

    def set_faces(self, faces: Sequence[RawFace]) -> None:
        self.faces = list(faces)

    def detect(self, rgb: np.ndarray, timestamp_ms: int) -> list[RawFace]:
        assert rgb.dtype == np.uint8 and rgb.ndim == 3 and rgb.flags.c_contiguous
        if self.calls and timestamp_ms <= self.calls[-1]:
            raise ValueError("timestamps must be strictly increasing (MediaPipe VIDEO mode)")
        self.calls.append(timestamp_ms)
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("scripted inference failure")
        return list(self.script(timestamp_ms)) if self.script is not None else list(self.faces)

    def reset(self) -> None:
        self.resets += 1
        self.calls = []

    def close(self) -> None:
        self.closed = True


def textured_image(width: int = 640, height: int = 480, luma: float = 128.0, seed: int = 0, noise: float = 25.0) -> np.ndarray:
    """Read-only BGR frame with texture (so blur/low-light heuristics see a usable image)."""
    rng = np.random.default_rng(seed)
    img = np.clip(rng.normal(luma, noise, (height, width, 3)), 0, 255).astype(np.uint8)
    img.flags.writeable = False
    return img


def make_frame(
    session_id: str,
    frame_id: int,
    t_ms: float,
    image: np.ndarray | None = None,
    *,
    source_mode: SourceMode = SourceMode.REPLAY,
    origin: datetime = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc),
) -> FramePacket:
    img = textured_image() if image is None else image
    if img.flags.writeable:
        img = img.copy()
        img.flags.writeable = False
    h, w = img.shape[:2]
    meta = FramePacketMeta(
        session_id=session_id,
        frame_id=frame_id,
        t_session_ms=t_ms,
        wall_time=origin + timedelta(milliseconds=t_ms),
        t_capture_mono_ns=time.monotonic_ns(),
        width=w,
        height=h,
        source_mode=source_mode,
        source_id="replay:a04-test" if source_mode == SourceMode.REPLAY else f"{source_mode.value}:a04-test",
    )
    return FramePacket(meta=meta, image=img)
