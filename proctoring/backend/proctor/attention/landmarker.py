"""Face backends: MediaPipe Tasks FaceLandmarker (real) behind a small protocol.

Only the pinned Tasks API is used (mediapipe==0.10.35 ships no legacy mp.solutions):
mediapipe.tasks.python.vision.FaceLandmarker in VIDEO running mode with a LOCAL
face_landmarker.task (settings.models_dir/attention/). Nothing is downloaded at runtime.

Facts measured on 2026-10-08 (Linux x86_64 container, CPU, mediapipe 0.10.35):
  * num_faces=1 cannot report a second face; num_faces=2/4 reported a second face on the very
    frame it appeared;
  * built-in landmark smoothing is active only with num_faces=1 (inter-frame jitter on a static
    noisy scene: 0.014 px vs 0.305 px with num_faces 2/4), so this module filters its own
    signals (gaze.OneEuro);
  * the result has no per-face detection score: FaceBox.confidence stays None;
  * on Linux the C library needs libGLESv2.so.2/libEGL.so.1 (apt: libgles2 libegl1) even on CPU.
"""

from __future__ import annotations

import hashlib
import json
import logging
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

import numpy as np

from proctor_contracts.v1 import ModelManifest

from .config import AttentionConfig

log = logging.getLogger("proctor.attention")

MANIFEST_PATH = Path(__file__).resolve().parent / "models.manifest.json"


@dataclass(frozen=True)
class RawFace:
    landmarks: np.ndarray  # (N, 3) float32, normalized x, y (+ relative z); N = 478 with iris
    matrix: np.ndarray | None  # 4x4 facial transformation matrix (camera space, cm)
    blendshapes: Mapping[str, float] | None


class FaceBackend(Protocol):
    model_id: str | None
    model_sha256: str | None

    def detect(self, rgb: np.ndarray, timestamp_ms: int) -> Sequence[RawFace]:
        """rgb: HxWx3 uint8 RGB, C-contiguous. timestamp_ms strictly increasing per reset()."""
        ...

    def reset(self) -> None:
        """Fresh tracking state (new session); timestamps may restart afterwards."""
        ...

    def close(self) -> None: ...


class BackendUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def load_manifest(path: Path = MANIFEST_PATH) -> ModelManifest:
    return ModelManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_model(models_dir: Path, manifest: ModelManifest) -> Path:
    """Path of the verified local model file; raises BackendUnavailable otherwise."""
    root = Path(models_dir).resolve()
    path = (root / manifest.file).resolve()
    if root not in path.parents:
        raise BackendUnavailable("model_invalid", "model path escapes models_dir")
    if not path.is_file():
        raise BackendUnavailable(
            "model_missing",
            f"{manifest.file} not found under models_dir; prepare it with "
            "`python -m proctor.attention.model_tool fetch` (needs network once) or copy it",
        )
    size = path.stat().st_size
    if size != manifest.size_bytes:
        raise BackendUnavailable("model_invalid", f"model size {size} != manifest {manifest.size_bytes}")
    digest = sha256_file(path)
    if digest != manifest.sha256:
        raise BackendUnavailable("model_invalid", "model sha256 does not match the manifest")
    return path


def _close_quietly(landmarker) -> None:
    try:
        landmarker.close()
    except Exception:  # pragma: no cover - close must never raise
        log.debug("FaceLandmarker.close failed", exc_info=True)


class MediaPipeFaceBackend:
    """Tasks FaceLandmarker, VIDEO mode, num_faces from config. Not thread-safe: the analyzer
    serializes detect/reset/close under its own lock."""

    def __init__(self, model_path: Path, manifest: ModelManifest, cfg: AttentionConfig):
        self._path = model_path
        self._cfg = cfg
        self.model_id: str | None = manifest.model_id
        self.model_sha256: str | None = manifest.sha256
        try:
            import mediapipe as mp  # noqa: F401
            from mediapipe.tasks.python import BaseOptions, vision
        except Exception as exc:  # pragma: no cover - depends on the installed wheel
            raise BackendUnavailable("runtime_unavailable", f"mediapipe import failed: {type(exc).__name__}: {exc}") from exc
        self._mp = mp
        self._vision = vision
        self._base_options = BaseOptions
        self._landmarker = None
        self._finalizer: weakref.finalize | None = None
        self._create()

    def _create(self) -> None:
        vision = self._vision
        options = vision.FaceLandmarkerOptions(
            base_options=self._base_options(model_asset_path=str(self._path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=self._cfg.num_faces,
            min_face_detection_confidence=self._cfg.min_face_detection_confidence,
            min_face_presence_confidence=self._cfg.min_face_presence_confidence,
            min_tracking_confidence=self._cfg.min_tracking_confidence,
            output_face_blendshapes=self._cfg.output_blendshapes,
            output_facial_transformation_matrixes=True,
        )
        try:
            self._landmarker = vision.FaceLandmarker.create_from_options(options)
            # close explicitly (also at interpreter exit) instead of relying on its __del__
            self._finalizer = weakref.finalize(self, _close_quietly, self._landmarker)
        except OSError as exc:  # e.g. libGLESv2.so.2 missing on Linux
            raise BackendUnavailable("runtime_unavailable", f"mediapipe runtime library: {exc}") from exc
        except Exception as exc:
            raise BackendUnavailable("model_invalid", f"FaceLandmarker init failed: {type(exc).__name__}: {exc}") from exc

    def detect(self, rgb: np.ndarray, timestamp_ms: int) -> list[RawFace]:
        if self._landmarker is None:
            raise BackendUnavailable("runtime_unavailable", "landmarker closed")
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        res = self._landmarker.detect_for_video(image, int(timestamp_ms))
        faces = []
        for i, pts in enumerate(res.face_landmarks or []):
            lm = np.array([(p.x, p.y, p.z) for p in pts], dtype=np.float32)
            matrix = None
            if res.facial_transformation_matrixes and i < len(res.facial_transformation_matrixes):
                matrix = np.asarray(res.facial_transformation_matrixes[i], dtype=np.float64).reshape(4, 4)
            bs = None
            if res.face_blendshapes and i < len(res.face_blendshapes):
                bs = {c.category_name: float(c.score) for c in res.face_blendshapes[i]}
            faces.append(RawFace(lm, matrix, bs))
        return faces

    def reset(self) -> None:
        self.close()
        self._create()

    def close(self) -> None:
        fin, self._finalizer, self._landmarker = self._finalizer, None, None
        if fin is not None:
            fin()  # runs _close_quietly once
