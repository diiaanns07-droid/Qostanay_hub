"""IdentityAnalyzer — proctor_contracts.interfaces.FrameAnalyzer (name="identity", owner: A13).

Control that the person at the computer is the same person as at the start of the exam. It is NOT biometric
identification (no database, no names) and NOT anti-spoofing.

* Frames come only from CaptureService (A02) via ``add_consumer``; this module never opens a camera.
* Reference ("эталон"): after the session enters RUNNING (``exam_started``), the median of >= 5
  L2-normalised SFace features from frames with exactly one good face (~3 s at 2 Hz). Until then
  ``enrolled=false, same_person=unknown``.
* Then once per second: cosine similarity to the reference; ``present`` if >= 0.363 (OpenCV SFace
  recommendation, see config.py), ``absent`` below; ``unknown`` with no face / several faces (A04 reports
  those) / face too small / model missing.
* Features and frames stay in memory, are never written or sent; ``end_session`` drops them. The only
  number that leaves this module is ``similarity`` in IdentityObservation.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import (
    Component,
    Health,
    HealthStatus,
    IdentityObservation,
    Observation,
    ObservationStatus,
    Producer,
    SignalState,
    SourceMode,
)

from .config import IdentityConfig
from .face import EngineLoadError, Face, FaceEngine, cosine
from .manifest import MANIFEST_PATH, IdentityManifest, load_manifest, read_verified

IDENTITY_MODULE_VERSION = "0.1.0"
MIN_FRAME_SIDE = 16

log = logging.getLogger("proctor.identity")


class IdentityAnalyzer:
    name = "identity"

    def __init__(
        self,
        settings: Any,
        config: IdentityConfig | None = None,
        *,
        manifest_path: Path = MANIFEST_PATH,
        engine: Any | None = None,
        config_error: str | None = None,
    ):
        """``engine`` injects a FaceEngine built on stubs (tests only); the real one is built in load()."""
        self.settings = settings
        self.config = config or IdentityConfig()
        self._manifest_path = manifest_path
        self._config_error = config_error
        self._injected = engine
        self._engine: Any | None = None
        self.identity_manifest: IdentityManifest | None = None
        self._model_id: str | None = None
        self._model_sha256: str | None = None
        self._lock = threading.Lock()
        self._health = Health(component=Component.IDENTITY, status=HealthStatus.STARTING, code="not_loaded", message="load() not called yet")
        self._load_details: dict[str, bool | int | float | str] = {}
        self._counters = {
            "processed": 0,
            "errors": 0,
            "skipped_interval": 0,
            "dropped_out_of_order": 0,
            "session_mismatch": 0,
            "enroll_samples": 0,
            "enroll_rejected": 0,
        }
        self._reset_session_state()

    # ------------------------------------------------------------------------------ lifecycle
    def load(self) -> Health:
        """Verify manifest + both local files (sha256) and build the OpenCV objects. Never downloads/raises."""
        if self._config_error:
            return self._set_health(HealthStatus.UNAVAILABLE, "config_invalid", self._config_error)
        if self._injected is not None:
            self._engine = self._injected
            return self._set_health(HealthStatus.OK, "model_loaded", "TEST face engine injected (not a model)", {"fake_engine": True})
        try:
            manifest = load_manifest(self._manifest_path)
        except Exception as exc:
            return self._set_health(HealthStatus.UNAVAILABLE, "manifest_invalid", f"identity manifest invalid: {type(exc).__name__}: {exc}")
        self.identity_manifest = manifest
        det, emb = manifest.by_role("face_detector"), manifest.by_role("face_embedder")
        base: dict[str, bool | int | float | str] = {
            "detector": det.model_id,
            "detector_license": det.license,
            "embedder": emb.model_id,
            "embedder_license": emb.license,
            "match_threshold": self.config.match_threshold,
            "config_version": self.config.config_version,
        }
        models_dir = Path(self.settings.models_dir)
        buffers: list[bytes] = []
        for entry in (det, emb):
            check, data = read_verified(models_dir, entry)
            if not check.ok or data is None:
                return self._set_health(HealthStatus.UNAVAILABLE, check.code, check.message, base)
            base[f"{entry.role}_sha256_prefix"] = (check.actual_sha256 or "")[:12]
            buffers.append(data)
            if entry.role == "face_embedder":
                self._model_sha256 = check.actual_sha256
        try:
            self._engine = FaceEngine.from_buffers(buffers[0], buffers[1], self.config)
        except EngineLoadError as exc:
            self._model_sha256 = None
            return self._set_health(HealthStatus.UNAVAILABLE, exc.code, exc.message, base)
        except Exception as exc:  # a model problem must not take the backend down
            log.exception("identity engine load failed")
            self._model_sha256 = None
            return self._set_health(HealthStatus.UNAVAILABLE, "model_invalid", f"{type(exc).__name__}: {exc}", base)
        self._model_id = emb.model_id
        return self._set_health(
            HealthStatus.OK,
            "model_loaded",
            f"{det.model_id} + {emb.model_id} loaded via OpenCV (local files, sha256 verified)",
            base,
        )

    def start_session(self, session_id: str, source_mode: SourceMode) -> None:
        with self._lock:
            self._reset_session_state()
            self._session_id = session_id
            self._source_mode = SourceMode(source_mode)

    def exam_started(self, t_session_ms: float) -> None:
        """Called by A01 when the session enters RUNNING (first time only; resume keeps the reference)."""
        with self._lock:
            if self._exam_t0 is None:
                self._exam_t0 = float(t_session_ms)

    def end_session(self) -> None:
        """Drops the reference and every collected feature (memory only, nothing was stored)."""
        with self._lock:
            self._reset_session_state()

    def close(self) -> None:
        self.end_session()
        self._engine = None
        self._set_health(HealthStatus.STOPPED, "closed", "identity analyzer closed")

    def health(self) -> Health:
        with self._lock:
            base = self._health
            if base.status not in (HealthStatus.OK, HealthStatus.DEGRADED):
                return base
            details = {**self._load_details, **self._counters, "enrolled": self._reference is not None}
        return Health(component=Component.IDENTITY, status=base.status, code=base.code, message=base.message, details=details)

    # -------------------------------------------------------------------------------- process
    def process(self, frame: FramePacket) -> Sequence[Observation]:
        meta = frame.meta
        cfg = self.config
        with self._lock:
            if self._session_id is None:
                self._reset_session_state()
                self._session_id, self._source_mode = meta.session_id, meta.source_mode
            if meta.session_id != self._session_id:
                self._counters["session_mismatch"] += 1
                return []
            if meta.frame_id <= self._last_frame_id or meta.t_session_ms < self._last_t:
                self._counters["dropped_out_of_order"] += 1
                return []
            enrolling = (
                self._engine is not None
                and self._reference is None
                and self._exam_t0 is not None
                and meta.t_session_ms >= self._exam_t0
            )
            interval = cfg.enroll_interval_ms if enrolling else cfg.interval_ms
            if self._last_analysis_t is not None and meta.t_session_ms - self._last_analysis_t < interval:
                self._counters["skipped_interval"] += 1
                return []
            self._last_frame_id, self._last_t = meta.frame_id, meta.t_session_ms
            self._last_analysis_t = meta.t_session_ms
            engine, exam_t0 = self._engine, self._exam_t0
        try:
            return [self._analyze(frame, engine, exam_t0)]
        except Exception as exc:  # never propagate: an unknown observation keeps the gap visible
            log.exception("identity analyzer failed on frame %s", meta.frame_id)
            with self._lock:
                self._counters["errors"] += 1
            return [self._observation(frame, ObservationStatus.ERROR, SignalState.UNKNOWN, ["analyzer_error", type(exc).__name__.lower()[:64]])]

    def _analyze(self, frame: FramePacket, engine: Any, exam_t0: float | None) -> IdentityObservation:
        cfg = self.config
        meta = frame.meta
        if engine is None:
            code = self._health.code if self._health.code in ("model_missing", "model_invalid", "config_invalid") else "model_unavailable"
            return self._observation(frame, ObservationStatus.ERROR, SignalState.UNKNOWN, [code])
        problem = _validate_image(frame.image)
        if problem is not None:
            return self._observation(frame, ObservationStatus.ERROR, SignalState.UNKNOWN, ["invalid_frame", problem])
        if exam_t0 is None or meta.t_session_ms < exam_t0:
            return self._observation(frame, ObservationStatus.UNKNOWN, SignalState.UNKNOWN, ["exam_not_started"])

        image: np.ndarray = frame.image
        faces = engine.detect(image)
        if not faces:
            return self._observation(frame, ObservationStatus.UNKNOWN, SignalState.UNKNOWN, ["no_face"])
        if len(faces) > 1:
            return self._observation(frame, ObservationStatus.UNKNOWN, SignalState.UNKNOWN, ["multiple_faces"], faces=len(faces))
        face: Face = faces[0]
        quality = float(np.clip(face.score, 0.0, 1.0))
        if min(face.w, face.h) < cfg.min_face_px:
            return self._observation(frame, ObservationStatus.UNKNOWN, SignalState.UNKNOWN, ["face_too_small"], quality=quality, faces=1)
        feature = engine.embed(image, face)

        with self._lock:
            if self._session_id != meta.session_id:  # session ended while the models ran: drop everything
                return self._observation(frame, ObservationStatus.UNKNOWN, SignalState.UNKNOWN, ["session_ended"], quality=quality, faces=1, enrolled=False)
            self._counters["processed"] += 1
            reasons: list[str] = []
            if self._reference is None:
                self._samples.append(feature)
                self._counters["enroll_samples"] += 1
                del self._samples[: -cfg.enroll_max_samples]
                if len(self._samples) >= cfg.enroll_min_samples:
                    self._try_enroll_locked()
                if self._reference is None:
                    reasons = ["enrolling"]
                    if meta.t_session_ms - exam_t0 > cfg.enroll_window_ms:
                        reasons.append("enrollment_delayed")
                    return self._observation(frame, ObservationStatus.UNKNOWN, SignalState.UNKNOWN, reasons, quality=quality, faces=1, enrolled=False)
                reasons = ["enrolled_now"]
            similarity = cosine(self._reference, feature)
        same = SignalState.PRESENT if similarity >= cfg.match_threshold else SignalState.ABSENT
        if same == SignalState.ABSENT:
            reasons.append("below_threshold")
        return self._observation(frame, ObservationStatus.OK, same, reasons, quality=quality, similarity=similarity, faces=1, enrolled=True)

    def _try_enroll_locked(self) -> None:
        """Median of the collected features; accepted only if every sample matches it (same OpenCV threshold),
        so a reference cannot be the blend of two people. Inconsistent samples are dropped."""
        samples = np.stack(self._samples)
        median = np.median(samples, axis=0)
        norm = float(np.linalg.norm(median))
        if norm <= 1e-12:
            self._samples.clear()
            return
        reference = (median / norm).astype(np.float32)
        sims = samples @ reference
        keep = sims >= self.config.match_threshold
        if bool(np.all(keep)):
            self._reference = reference
            self._samples.clear()
            return
        self._counters["enroll_rejected"] += int((~keep).sum())
        self._samples = [s for s, ok in zip(self._samples, keep) if ok]

    # -------------------------------------------------------------------------------- helpers
    def _observation(
        self,
        frame: FramePacket,
        status: ObservationStatus,
        same: SignalState,
        reasons: list[str],
        *,
        quality: float | None = None,
        similarity: float | None = None,
        faces: int | None = None,
        enrolled: bool | None = None,
    ) -> IdentityObservation:
        """``enrolled`` must be passed by callers that already hold the lock (the lock is not re-entrant)."""
        meta = frame.meta
        flags: list[str] = []
        if faces is not None and faces > 1:
            flags.append("multiple_faces")
        if meta.source_mode == SourceMode.SYNTHETIC:
            flags.append("synthetic")
        if enrolled is None:
            with self._lock:
                enrolled = self._reference is not None and self._session_id == meta.session_id
        return IdentityObservation(
            observation_id=f"identity-{meta.frame_id}",
            session_id=meta.session_id,
            frame_id=meta.frame_id,
            t_session_ms=meta.t_session_ms,
            wall_time=meta.wall_time,
            source_mode=meta.source_mode,
            producer=self._producer(),
            status=status,
            quality=round(quality, 4) if quality is not None else None,
            quality_flags=flags,
            latency_ms=max(0.0, (time.monotonic_ns() - meta.t_capture_mono_ns) / 1e6),
            same_person=same,
            similarity=round(similarity, 4) if similarity is not None else None,
            enrolled=enrolled,
            reasons=reasons[:16],
        )

    def _producer(self) -> Producer:
        if self._injected is not None:
            model_id = "fake-face-engine"
        else:
            model_id = self._model_id if self._model_sha256 else None
        return Producer(
            module="identity",
            version=IDENTITY_MODULE_VERSION,
            model_id=model_id,
            model_sha256=self._model_sha256,
            config_version=self.config.config_version,
        )

    def _set_health(self, status: HealthStatus, code: str, message: str, details: dict | None = None) -> Health:
        health = Health(component=Component.IDENTITY, status=status, code=code, message=message[:500], details=dict(details or {}))
        with self._lock:
            self._health = health
            self._load_details = dict(details or {})
        if status not in (HealthStatus.OK, HealthStatus.STOPPED):
            log.warning("identity analyzer %s: %s", code, message)
        return health

    def _reset_session_state(self) -> None:
        self._session_id: str | None = None
        self._source_mode: SourceMode | None = None
        self._exam_t0: float | None = None
        self._last_frame_id = -1
        self._last_t = -1.0
        self._last_analysis_t: float | None = None
        self._samples: list[np.ndarray] = []
        self._reference: np.ndarray | None = None
        for key in self._counters:
            self._counters[key] = 0


def _validate_image(image: Any) -> str | None:
    if not isinstance(image, np.ndarray):
        return "image_not_ndarray"
    if image.dtype != np.uint8:
        return "image_dtype_not_uint8"
    if image.ndim != 3 or image.shape[2] != 3:
        return "image_not_hxwx3"
    if image.shape[0] < MIN_FRAME_SIDE or image.shape[1] < MIN_FRAME_SIDE:
        return "image_too_small"
    return None
