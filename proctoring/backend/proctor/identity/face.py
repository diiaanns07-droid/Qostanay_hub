"""YuNet + SFace through OpenCV (owner: A13): face boxes and an L2-normalised 128-d feature, in memory only.

``FaceEngine`` wraps two objects with the cv2 API (``cv2.FaceDetectorYN`` / ``cv2.FaceRecognizerSF``); tests
inject stubs with the same methods. Nothing here writes to disk or keeps a frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import IdentityConfig


class EngineLoadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Face:
    """One YuNet detection in pixels: box, 5 landmarks, score. ``row`` is the raw (15,) float32 row that
    FaceRecognizerSF.alignCrop expects."""

    x: float
    y: float
    w: float
    h: float
    score: float
    row: np.ndarray


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(v))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("degenerate face feature")
    return v / norm


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two L2-normalised features; equals FaceRecognizerSF.match(..., FR_COSINE)."""
    return float(np.clip(np.dot(a, b), -1.0, 1.0))


class FaceEngine:
    def __init__(self, detector: Any, recognizer: Any):
        self._detector = detector
        self._recognizer = recognizer
        self._input_size: tuple[int, int] | None = None

    @classmethod
    def from_buffers(cls, detector_onnx: bytes, embedder_onnx: bytes, config: IdentityConfig) -> "FaceEngine":
        try:
            import cv2  # noqa: PLC0415
        except Exception as exc:  # pragma: no cover - cv2 is a locked dependency
            raise EngineLoadError("opencv_unavailable", f"cv2 import failed: {type(exc).__name__}") from exc
        if not hasattr(cv2, "FaceDetectorYN") or not hasattr(cv2, "FaceRecognizerSF"):
            raise EngineLoadError("opencv_unavailable", f"cv2 {cv2.__version__} has no FaceDetectorYN/FaceRecognizerSF")
        empty = np.empty(0, dtype=np.uint8)
        try:
            detector = cv2.FaceDetectorYN.create(
                "onnx",
                np.frombuffer(detector_onnx, dtype=np.uint8),
                empty,
                (320, 320),
                config.det_score_threshold,
                config.det_nms_threshold,
                config.det_top_k,
            )
            recognizer = cv2.FaceRecognizerSF.create("onnx", np.frombuffer(embedder_onnx, dtype=np.uint8), empty)
        except cv2.error as exc:
            raise EngineLoadError("model_invalid", f"OpenCV could not load the identity models: {str(exc)[:200]}") from exc
        engine = cls(detector, recognizer)
        engine.detect(np.zeros((240, 320, 3), dtype=np.uint8))  # warm-up on a blank image (no faces)
        return engine

    def detect(self, image: np.ndarray) -> list[Face]:
        h, w = image.shape[:2]
        if self._input_size != (w, h):
            self._detector.setInputSize((w, h))
            self._input_size = (w, h)
        _, rows = self._detector.detect(image)
        if rows is None:
            return []
        faces = []
        for row in np.asarray(rows, dtype=np.float32).reshape(-1, 15):
            faces.append(Face(float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[14]), row.copy()))
        return faces

    def embed(self, image: np.ndarray, face: Face) -> np.ndarray:
        aligned = self._recognizer.alignCrop(image, face.row)
        feature = self._recognizer.feature(aligned)
        return l2_normalize(np.array(feature, dtype=np.float32, copy=True))
