"""Test doubles for A13 identity tests. They check enrollment/matching logic and contracts, NOT CV accuracy.

A frame "shows" people through two pixels: image[0, 0, 0] = person id (1..), image[0, 1, 0] = face count.
StubDetector mimics cv2.FaceDetectorYN (setInputSize/detect -> (n, rows (n,15))). StubRecognizer mimics
cv2.FaceRecognizerSF (alignCrop/feature): each person has a fixed random 128-d direction + small noise, so
two different people are near-orthogonal (cosine ~ 0) and the same person is ~0.99.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import numpy as np

from proctor.identity.face import FaceEngine
from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import FramePacketMeta, SourceMode

SESSION = "s-identity-test"
WALL0 = datetime(2026, 10, 9, 9, 0, 0, tzinfo=timezone.utc)


def make_image(person: int = 1, faces: int = 1, width: int = 640, height: int = 480) -> np.ndarray:
    image = np.full((height, width, 3), 90, dtype=np.uint8)
    image[0, 0, 0] = person
    image[0, 1, 0] = faces
    image.flags.writeable = False
    return image


def make_frame(frame_id: int, t_ms: float, image: np.ndarray | None = None, *, session_id: str = SESSION,
               mode: SourceMode = SourceMode.REPLAY) -> FramePacket:
    image = make_image() if image is None else image
    meta = FramePacketMeta(
        session_id=session_id,
        frame_id=frame_id,
        t_session_ms=t_ms,
        wall_time=WALL0 + timedelta(milliseconds=t_ms),
        t_capture_mono_ns=time.monotonic_ns(),
        width=image.shape[1],
        height=image.shape[0],
        source_mode=mode,
        source_id="replay:identity-test" if mode == SourceMode.REPLAY else f"{mode.value}:test",
    )
    return FramePacket(meta=meta, image=image)


class StubDetector:
    def __init__(self, face_px: float = 160.0, score: float = 0.95):
        self.face_px = face_px
        self.score = score
        self.input_sizes: list[tuple[int, int]] = []
        self.calls = 0

    def setInputSize(self, size):  # noqa: N802 - cv2 API name
        self.input_sizes.append(tuple(size))

    def detect(self, image):
        self.calls += 1
        n = int(image[0, 1, 0])
        if n == 0:
            return 1, None
        rows = []
        for i in range(n):
            x, y, s = 100.0 + 200 * i, 120.0, self.face_px
            rows.append([x, y, s, s, x + 0.3 * s, y + 0.4 * s, x + 0.7 * s, y + 0.4 * s, x + 0.5 * s, y + 0.6 * s,
                         x + 0.35 * s, y + 0.8 * s, x + 0.65 * s, y + 0.8 * s, self.score])
        return 1, np.asarray(rows, dtype=np.float32)


class StubRecognizer:
    """FaceRecognizerSF stand-in. ``noise`` is relative to the unit identity vector."""

    def __init__(self, noise: float = 0.05, seed: int = 7):
        self._rng = np.random.default_rng(seed)
        self._noise = noise
        self._identity: dict[int, np.ndarray] = {}
        self.feature_calls = 0

    def identity(self, person: int) -> np.ndarray:
        if person not in self._identity:
            v = np.random.default_rng(1000 + person).standard_normal(128).astype(np.float32)
            self._identity[person] = v / np.linalg.norm(v)
        return self._identity[person]

    def alignCrop(self, image, face_row):  # noqa: N802 - cv2 API name
        assert face_row.shape == (15,)
        crop = np.zeros((112, 112, 3), dtype=np.uint8)
        crop[0, 0, 0] = image[0, 0, 0]
        return crop

    def feature(self, aligned):
        self.feature_calls += 1
        base = self.identity(int(aligned[0, 0, 0]))
        noisy = base + self._noise * self._rng.standard_normal(128).astype(np.float32) / np.sqrt(128)
        return (3.7 * noisy).reshape(1, 128).astype(np.float32)  # SFace features are not normalised


def stub_engine(**kw) -> tuple[FaceEngine, StubDetector, StubRecognizer]:
    det = StubDetector(**{k: v for k, v in kw.items() if k in ("face_px", "score")})
    rec = StubRecognizer(**{k: v for k, v in kw.items() if k in ("noise", "seed")})
    return FaceEngine(det, rec), det, rec
