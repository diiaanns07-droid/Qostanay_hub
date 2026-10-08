"""IdentityConfig (owner: A13). Defaults <- QORGAU_IDENTITY_<FIELD> environment variables.

Thresholds are taken from OpenCV, not tuned on our data:
* match_threshold = 0.363 (cosine) — OpenCV recommendation for SFace:
  opencv/opencv_zoo models/face_recognition_sface/sface.py (``self._threshold_cosine = 0.363``) and
  opencv/opencv samples/dnn/face_detect.py (``cosine_similarity_threshold = 0.363``).
* det_score_threshold = 0.9 — default ``--score_threshold`` of opencv samples/dnn/face_detect.py and of the
  YuNet demo in opencv_zoo; nms 0.3, top_k 5000 as there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class IdentityConfig:
    config_version: str = "identity-0.1.0"
    # cadence (session time, not wall clock)
    interval_ms: float = 1000.0  # monitoring: one analysis per second, never more often
    enroll_interval_ms: float = 500.0  # enrollment only: 5 samples fit into the first ~3 s of the exam
    # reference ("эталон"): median of >= enroll_min_samples single-good-face embeddings after RUNNING
    enroll_min_samples: int = 5
    enroll_max_samples: int = 15  # bounded memory while samples are rejected as inconsistent
    enroll_window_ms: float = 3000.0  # after this, observations carry "enrollment_delayed"
    # face detection (YuNet)
    det_score_threshold: float = 0.9
    det_nms_threshold: float = 0.3
    det_top_k: int = 5000
    min_face_px: float = 60.0  # smaller faces are not compared (unknown/face_too_small)
    # matching (SFace, cosine on L2-normalised 128-d features)
    match_threshold: float = 0.363

    def validate(self) -> "IdentityConfig":
        if self.interval_ms < 1000.0:
            raise ValueError("interval_ms must be >= 1000 (identity runs at most once per second)")
        if not 0 < self.enroll_interval_ms <= self.interval_ms:
            raise ValueError("enroll_interval_ms must be in (0, interval_ms]")
        if self.enroll_min_samples < 5 or self.enroll_max_samples < self.enroll_min_samples:
            raise ValueError("need 5 <= enroll_min_samples <= enroll_max_samples")
        if not 0 < self.det_score_threshold < 1 or not 0 < self.det_nms_threshold < 1 or self.det_top_k < 1:
            raise ValueError("invalid detector thresholds")
        if not -1 < self.match_threshold < 1:
            raise ValueError("match_threshold must be in (-1, 1)")
        if self.min_face_px < 1 or self.enroll_window_ms < 0:
            raise ValueError("invalid min_face_px/enroll_window_ms")
        return self

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None, **overrides: object) -> "IdentityConfig":
        """Raises ValueError on an invalid override."""
        env = os.environ if environ is None else environ
        base = cls()
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = env.get(f"QORGAU_IDENTITY_{f.name.upper()}")
            if raw is None:
                continue
            default = getattr(base, f.name)
            try:
                values[f.name] = int(raw) if isinstance(default, int) else float(raw) if isinstance(default, float) else raw.strip()
            except ValueError:
                raise ValueError(f"QORGAU_IDENTITY_{f.name.upper()}={raw!r} is not a valid {type(default).__name__}") from None
        return replace(base, **{**values, **overrides}).validate()  # type: ignore[arg-type]
