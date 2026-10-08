"""PhoneAnalyzer — proctor_contracts.interfaces.FrameAnalyzer (name="phone", owner: A03).

Pure frame consumer: frames come from CaptureService (A02); this module never opens a camera or a video
file. One PhoneObservation per processed frame (observation_id "phone-<frame_id>", stable on
re-delivery). Duplicate/out-of-order frames are ignored (counted). Episodes/incidents belong to A05.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import (
    BBox,
    Component,
    Health,
    HealthStatus,
    ModelManifest,
    Observation,
    ObservationStatus,
    PhoneDetection,
    PhoneObservation,
    PhoneSignal,
    PhoneSignalName,
    Producer,
    SignalState,
    SourceMode,
)

from . import signals as sig
from .config import PhoneConfig
from .detector import DetectorLoadError, RawDetection, YoloOnnxDetector
from .manifest import MANIFEST_PATH, load_manifest, verify_model_file
from .quality import FrameQuality, assess
from .tracker import PhoneTracker

PHONE_MODULE_VERSION = "0.1.0"
MIN_FRAME_SIDE = 16
_STATS_WINDOW = 200
_ERROR_WINDOW = 50

log = logging.getLogger("proctor.phone")


def _percentile(values: deque[float] | list[float], q: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values, dtype=np.float64), q)), 2)


class PhoneAnalyzer:
    name = "phone"

    def __init__(
        self,
        settings: Any,
        config: PhoneConfig | None = None,
        *,
        manifest_path: Path = MANIFEST_PATH,
        detector: Any | None = None,
        config_error: str | None = None,
    ):
        """``detector`` injects a fake (tests only); the real path builds YoloOnnxDetector in load()."""
        self.settings = settings
        self.config = config or PhoneConfig()
        self._manifest_path = manifest_path
        self._config_error = config_error
        self._injected = detector
        self._detector: Any | None = None
        self._cv2: Any | None = None
        self.manifest: ModelManifest | None = None
        self._model_sha256: str | None = None
        self._lock = threading.Lock()
        self._health = Health(component=Component.PHONE, status=HealthStatus.STARTING, code="not_loaded", message="load() not called yet")
        self._load_details: dict[str, bool | int | float | str] = {}
        self._tracker = PhoneTracker(self.config)
        self._session_id: str | None = None
        self._source_mode: SourceMode | None = None
        self._last_frame_id = -1
        self._last_t = -1.0
        self._last_infer_mono = 0.0
        self._counters = {"processed": 0, "errors": 0, "invalid_frames": 0, "dropped_out_of_order": 0, "skipped_interval": 0, "session_mismatch": 0}
        self._infer_ms: deque[float] = deque(maxlen=_STATS_WINDOW)
        self._total_ms: deque[float] = deque(maxlen=_STATS_WINDOW)
        self._recent_errors: deque[bool] = deque(maxlen=_ERROR_WINDOW)

    # ------------------------------------------------------------------------------ lifecycle
    def load(self) -> Health:
        """Verify manifest + local weights (sha256) and create the ORT session. Never downloads/raises."""
        if self._config_error:
            return self._set_health(HealthStatus.UNAVAILABLE, "config_invalid", self._config_error[:500])
        try:
            import cv2  # noqa: PLC0415

            self._cv2 = cv2
        except Exception:
            self._cv2 = None
        if self._injected is not None:  # test double: no model file involved
            self._detector = self._injected
            return self._set_health(HealthStatus.OK, "model_loaded", "TEST detector injected (not a model)", {"fake_detector": True})
        try:
            manifest = load_manifest(self._manifest_path)
        except Exception as exc:
            return self._set_health(HealthStatus.UNAVAILABLE, "manifest_invalid", f"phone model manifest invalid: {type(exc).__name__}: {exc}"[:500])
        self.manifest = manifest
        base = {"model_id": manifest.model_id, "model_version": manifest.version, "license": manifest.license}
        check = verify_model_file(Path(self.settings.models_dir), manifest)
        if not check.ok:
            return self._set_health(HealthStatus.UNAVAILABLE, check.code, check.message, base)
        detector = YoloOnnxDetector(check.path, manifest, self.config)  # type: ignore[arg-type]
        try:
            info = detector.load()
        except DetectorLoadError as exc:
            return self._set_health(HealthStatus.UNAVAILABLE, exc.code, exc.message, base)
        except Exception as exc:  # never let a model problem take the backend down
            log.exception("phone detector load failed")
            return self._set_health(HealthStatus.UNAVAILABLE, "model_invalid", f"{type(exc).__name__}: {exc}"[:500], base)
        self._detector = detector
        self._model_sha256 = check.actual_sha256
        details: dict[str, bool | int | float | str] = {
            **base,
            "sha256_prefix": (check.actual_sha256 or "")[:12],
            "provider": info.provider,
            "intra_op_threads": info.intra_op_threads,
            "input_size": info.input_size,
            "rect": info.rect,
            "phone_class": ",".join(f"{i}:{n}" for i, n in info.phone_classes.items()),
            "conf_threshold": self.config.conf_threshold,
            "config_version": self.config.config_version,
        }
        if info.warmup_ms is not None:
            details["warmup_ms"] = round(info.warmup_ms, 1)
        return self._set_health(
            HealthStatus.OK,
            "model_loaded",
            f"{manifest.model_id} {manifest.version} loaded on {info.provider} (local file, sha256 verified)",
            details,
        )

    def start_session(self, session_id: str, source_mode: SourceMode) -> None:
        with self._lock:
            self._reset_session_state()
            self._session_id = session_id
            self._source_mode = SourceMode(source_mode)

    def end_session(self) -> None:
        with self._lock:
            self._reset_session_state()

    def close(self) -> None:
        self.end_session()
        detector, self._detector = self._detector, None
        if detector is not None and hasattr(detector, "close"):
            try:
                detector.close()
            except Exception:  # pragma: no cover - defensive
                log.exception("phone detector close failed")
        self._set_health(HealthStatus.STOPPED, "closed", "phone analyzer closed")

    def health(self) -> Health:
        with self._lock:
            base = self._health
            if base.status not in (HealthStatus.OK, HealthStatus.DEGRADED):
                return base
            if not self._counters["processed"] and not self._recent_errors:
                return base
            details = {**self._load_details, **self._stats_unlocked()}
            error_rate = sum(self._recent_errors) / len(self._recent_errors) if self._recent_errors else 0.0
        if error_rate >= 0.2:
            return Health(
                component=Component.PHONE,
                status=HealthStatus.DEGRADED,
                code="inference_errors",
                message=f"{error_rate:.0%} of the last {len(self._recent_errors)} frames failed in the phone analyzer",
                details=details,
            )
        return Health(component=Component.PHONE, status=base.status, code=base.code, message=base.message, details=details)

    def runtime_stats(self) -> dict[str, float | int | None]:
        """Measured phone-analyzer timings for A02/A01 metrics (thread-safe)."""
        with self._lock:
            return self._stats_unlocked()

    # -------------------------------------------------------------------------------- process
    def process(self, frame: FramePacket) -> Sequence[Observation]:
        t_start = time.monotonic_ns()
        meta = frame.meta
        with self._lock:
            if self._session_id is None:  # defensive: A01 always calls start_session() first
                self._reset_session_state()
                self._session_id, self._source_mode = meta.session_id, meta.source_mode
            if meta.session_id != self._session_id:
                self._counters["session_mismatch"] += 1
                return []
            if meta.frame_id <= self._last_frame_id or meta.t_session_ms < self._last_t:
                self._counters["dropped_out_of_order"] += 1
                return []
            if self.config.min_interval_ms > 0 and self._last_infer_mono and (t_start - self._last_infer_mono) / 1e6 < self.config.min_interval_ms:
                self._counters["skipped_interval"] += 1
                return []
            self._last_frame_id, self._last_t = meta.frame_id, meta.t_session_ms
            self._last_infer_mono = t_start
        try:
            return [self._analyze(frame, t_start)]
        except Exception as exc:  # never propagate: an error observation keeps the gap visible
            log.exception("phone analyzer failed on frame %s", meta.frame_id)
            with self._lock:
                self._counters["errors"] += 1
                self._recent_errors.append(True)
            return [self._error_observation(frame, t_start, "inference_error", f"{type(exc).__name__}")]

    def _analyze(self, frame: FramePacket, t_start: int) -> PhoneObservation:
        cfg = self.config
        meta = frame.meta
        problem = self._validate_image(frame)
        if problem is not None:
            with self._lock:
                self._counters["invalid_frames"] += 1
                self._recent_errors.append(True)
            return self._error_observation(frame, t_start, "invalid_frame", problem)
        if self._detector is None:
            with self._lock:
                self._recent_errors.append(True)
            return self._error_observation(frame, t_start, "model_unavailable", self._health.code)

        image: np.ndarray = frame.image
        fq: FrameQuality = assess(image, cfg, self._cv2)
        flags = list(fq.flags)
        age_ms = (t_start - meta.t_capture_mono_ns) / 1e6
        if age_ms > cfg.stale_ms:
            flags.append("stale")
        h, w = image.shape[:2]
        if (w, h) != (meta.width, meta.height):
            flags.append("size_mismatch")
        if meta.source_mode == SourceMode.SYNTHETIC:
            flags.append("synthetic")

        detections, timings = self._detector.detect(image)
        detections = list(detections)[: cfg.max_detections]
        with self._lock:
            t = meta.t_session_ms
            assigned = self._tracker.update(detections, t)
            for tr in self._tracker.tracks:
                sig.update_raise(tr, t, cfg)
            ctx = sig.FrameContext(t=t, usable=fq.usable, detections=detections, assigned=assigned, tracks=list(self._tracker.tracks))
            signals = [sig.phone_visible(ctx, cfg), sig.phone_raised(ctx, cfg), sig.possible_screen_capture(ctx, cfg)]
            phone_dets = [self._to_contract(det, tr, t) for det, tr in zip(detections, assigned)]
        if any(d.area < cfg.small_object_area for d in detections):
            flags.append("small_object")
        if any(min(d.x_min, d.y_min) <= cfg.edge_margin or max(d.x_max, d.y_max) >= 1 - cfg.edge_margin for d in detections):
            flags.append("edge_of_frame")

        if not fq.usable and not detections:
            status = ObservationStatus.UNKNOWN
        elif any(f != "synthetic" for f in flags):
            status = ObservationStatus.DEGRADED
        else:
            status = ObservationStatus.OK
        done = time.monotonic_ns()
        with self._lock:
            self._counters["processed"] += 1
            self._recent_errors.append(False)
            if "infer_ms" in timings:
                self._infer_ms.append(float(timings["infer_ms"]))
            self._total_ms.append((done - t_start) / 1e6)
        return PhoneObservation(
            observation_id=f"phone-{meta.frame_id}",
            session_id=meta.session_id,
            frame_id=meta.frame_id,
            t_session_ms=meta.t_session_ms,
            wall_time=meta.wall_time,
            source_mode=meta.source_mode,
            producer=self._producer(),
            status=status,
            quality=fq.quality,
            quality_flags=_dedup(flags)[:16],
            latency_ms=max(0.0, (done - meta.t_capture_mono_ns) / 1e6),
            detections=phone_dets,
            signals=signals,
        )

    # -------------------------------------------------------------------------------- helpers
    def _to_contract(self, det: RawDetection, tr: Any, t: float) -> PhoneDetection:
        return PhoneDetection(
            bbox=BBox(x_min=det.x_min, y_min=det.y_min, x_max=det.x_max, y_max=det.y_max),
            confidence=round(det.confidence, 4),
            class_name=det.class_name[:64],
            class_index=det.class_index,
            track_id=tr.track_id if tr is not None else None,
            track_quality=tr.quality(self.config) if tr is not None else None,
            track_age_ms=round(tr.age_ms(t), 1) if tr is not None else None,
        )

    def _validate_image(self, frame: FramePacket) -> str | None:
        image = frame.image
        if not isinstance(image, np.ndarray):
            return "image_not_ndarray"
        if image.dtype != np.uint8:
            return "image_dtype_not_uint8"
        if image.ndim != 3 or image.shape[2] != 3:
            return "image_not_hxwx3"
        if image.shape[0] < MIN_FRAME_SIDE or image.shape[1] < MIN_FRAME_SIDE:
            return "image_too_small"
        return None

    def _producer(self) -> Producer:
        manifest = self.manifest
        return Producer(
            module="phone",
            version=PHONE_MODULE_VERSION,
            model_id=manifest.model_id if manifest is not None and self._model_sha256 else ("fake-detector" if self._injected is not None else None),
            model_sha256=self._model_sha256,
            config_version=self.config.config_version,
        )

    def _error_observation(self, frame: FramePacket, t_start: int, reason: str, detail: str) -> PhoneObservation:
        meta = frame.meta
        flag = reason if reason in ("invalid_frame", "model_unavailable") else "analyzer_error"
        return PhoneObservation(
            observation_id=f"phone-{meta.frame_id}",
            session_id=meta.session_id,
            frame_id=meta.frame_id,
            t_session_ms=meta.t_session_ms,
            wall_time=meta.wall_time,
            source_mode=meta.source_mode,
            producer=self._producer(),
            status=ObservationStatus.ERROR,
            quality=None,
            quality_flags=[flag],
            latency_ms=max(0.0, (time.monotonic_ns() - meta.t_capture_mono_ns) / 1e6),
            detections=[],
            signals=[
                PhoneSignal(name=n, state=SignalState.UNKNOWN, reason=reason, facts={"detail": detail[:64]})
                for n in (PhoneSignalName.PHONE_VISIBLE, PhoneSignalName.PHONE_RAISED, PhoneSignalName.POSSIBLE_SCREEN_CAPTURE)
            ],
        )

    def _set_health(self, status: HealthStatus, code: str, message: str, details: dict | None = None) -> Health:
        health = Health(component=Component.PHONE, status=status, code=code, message=message[:500], details=dict(details or {}))
        with self._lock:
            self._health = health
            self._load_details = dict(details or {})
        if status not in (HealthStatus.OK, HealthStatus.STOPPED):
            log.warning("phone analyzer %s: %s", code, message)
        return health

    def _reset_session_state(self) -> None:
        self._tracker.reset()
        self._session_id = None
        self._source_mode = None
        self._last_frame_id = -1
        self._last_t = -1.0
        self._last_infer_mono = 0.0
        for key in self._counters:
            self._counters[key] = 0
        self._infer_ms.clear()
        self._total_ms.clear()
        self._recent_errors.clear()

    def _stats_unlocked(self) -> dict[str, float | int | None]:
        stats: dict[str, float | int | None] = dict(self._counters)
        stats.update(
            infer_ms_p50=_percentile(self._infer_ms, 50),
            infer_ms_p95=_percentile(self._infer_ms, 95),
            process_ms_p50=_percentile(self._total_ms, 50),
            process_ms_p95=_percentile(self._total_ms, 95),
            live_tracks=len(self._tracker.tracks),
            tracks_created=self._tracker.created,
        )
        return {k: v for k, v in stats.items() if v is not None}


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]
