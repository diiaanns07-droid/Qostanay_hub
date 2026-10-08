"""AttentionAnalyzer (A04): faces, presence, primary face, head pose, approximate gaze, calibration.

One AttentionObservation per analyzed frame. Semantics for consumers (A05/A07/A08) are in
handoffs/A04/INTERFACE.md; the short version:
  * face_count = faces the model reported in THIS frame (None = could not be determined,
    e.g. a dark/blurred frame); 0 is reported only when the frame itself was usable;
  * primary_face_present: True / False (no face) / None (faces visible but which one is the
    student is ambiguous or uncertain right now);
  * head_pose: filtered head angles relative to the line of sight to the camera;
  * head_direction: from head pose only; gaze.direction: head pose + approximate eye rotation,
    classified against calibrated screen edges (or generic edges, flag "uncalibrated");
  * unknown is unknown: low quality, extreme roll, ambiguous primary -> Direction.UNKNOWN.
Episode durations/rules are A05's job; this module only debounces single-frame flicker.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Sequence

import cv2
import numpy as np

from proctor_contracts.interfaces import FramePacket, InvalidStateError, ProctorError
from proctor_contracts.v1 import (
    AttentionObservation,
    BBox,
    CalibrationState,
    CalibrationTarget,
    Component,
    Direction,
    ErrorCode,
    FaceBox,
    GazeEstimate,
    GazeMethod,
    HeadPose,
    Health,
    HealthStatus,
    Observation,
    ObservationStatus,
    Producer,
    SourceMode,
)

from .calibration import CalibrationController, CalibrationSample
from .config import MODULE_VERSION, AttentionConfig
from .gaze import Debouncer, DirectionModel, EyeRotation, OneEuro, eye_rotation, gaze_levels, head_levels, pick_direction
from .geometry import eye_features, face_bbox, head_pose_from_matrix, outside_ratio
from .landmarker import BackendUnavailable, FaceBackend, MediaPipeFaceBackend, load_manifest, resolve_model
from .quality import face_quality, face_stats, frame_stats, no_face_trust
from .tracking import PrimaryFaceTracker

log = logging.getLogger("proctor.attention")

BackendFactory = Callable[[AttentionConfig], FaceBackend]
MAX_REASONS = 16


def _clip_deg(v: float) -> float:
    return float(round(min(180.0, max(-180.0, v)), 2))


class MediaPipeAttentionAnalyzer:
    """Implements proctor_contracts.interfaces.AttentionAnalyzer."""

    name = "attention"

    def __init__(self, settings, cfg: AttentionConfig | None = None, backend_factory: BackendFactory | None = None):
        self.settings = settings
        self.cfg = cfg or AttentionConfig()
        self._backend_factory = backend_factory
        self._backend: FaceBackend | None = None
        self._lock = threading.RLock()  # serializes process/start_session/end_session/close
        self._calib = CalibrationController(self.cfg)
        self._generic_model = DirectionModel.generic(self.cfg)
        self._health = Health(component=Component.ATTENTION, status=HealthStatus.STARTING, code="not_loaded")
        self._model_id: str | None = None
        self._model_sha: str | None = None
        self._session_id: str | None = None
        self._mode: SourceMode | None = None
        self._frames = 0
        self._errors = 0
        self._recent_errors: deque[bool] = deque(maxlen=60)  # last inference outcomes (True = failed)
        self._infer_ms_ema: float | None = None
        self._reset_signal_state()

    # ------------------------------------------------------------------ lifecycle
    def load(self) -> Health:
        with self._lock:
            try:
                if self._backend_factory is not None:
                    backend = self._backend_factory(self.cfg)
                else:
                    manifest = load_manifest()
                    path = resolve_model(self.settings.models_dir, manifest)
                    backend = MediaPipeFaceBackend(path, manifest, self.cfg)
            except BackendUnavailable as exc:
                self._health = Health(component=Component.ATTENTION, status=HealthStatus.UNAVAILABLE, code=exc.code, message=exc.message[:500])
                return self._health
            except Exception as exc:  # never take the backend down
                log.exception("attention load failed")
                self._health = Health(
                    component=Component.ATTENTION, status=HealthStatus.UNAVAILABLE, code="init_error",
                    message=f"{type(exc).__name__}: {exc}"[:500],
                )
                return self._health
            self._backend = backend
            self._model_id = getattr(backend, "model_id", None)
            self._model_sha = getattr(backend, "model_sha256", None)
            self._health = self._ok_health()
            return self._health

    def _ok_health(self) -> Health:
        return Health(
            component=Component.ATTENTION,
            status=HealthStatus.OK,
            code="ok",
            message=f"FaceLandmarker (MediaPipe Tasks) num_faces={self.cfg.num_faces}; gaze is approximate",
            details=self._details(),
        )

    def _details(self) -> dict:
        d: dict = {
            "num_faces": self.cfg.num_faces,
            "config_version": self.cfg.config_version,
            "frames_processed": self._frames,
            "errors": self._errors,
            "smoothing": "module_one_euro",
        }
        if self._model_id:
            d["model_id"] = self._model_id
        if self._infer_ms_ema is not None:
            d["inference_ms_ema"] = round(self._infer_ms_ema, 1)
        return d

    def health(self) -> Health:
        with self._lock:
            if self._backend is None:
                return self._health
            recent = self._recent_errors
            if len(recent) >= 10 and sum(recent) > 0.2 * len(recent):
                return Health(
                    component=Component.ATTENTION, status=HealthStatus.DEGRADED, code="inference_errors",
                    message=f"{sum(recent)} of the last {len(recent)} frames failed in the face model",
                    details=self._details(),
                )
            return self._ok_health()

    def start_session(self, session_id: str, source_mode: SourceMode) -> None:
        with self._lock:
            self._session_id = session_id
            self._mode = source_mode
            self._calib.reset()
            self._reset_signal_state()
            self._frames = self._errors = 0
            self._recent_errors.clear()
            self._reset_backend()

    def end_session(self) -> None:
        """Idempotent. Drops calibration (personal data), tracks, filters and model state."""
        with self._lock:
            self._calib.reset()
            self._reset_signal_state()
            if self._session_id is not None:
                self._reset_backend()
            self._session_id = None
            self._mode = None

    def close(self) -> None:
        with self._lock:
            self.end_session()
            backend, self._backend = self._backend, None
            if backend is not None:
                backend.close()
            self._health = Health(component=Component.ATTENTION, status=HealthStatus.STOPPED, code="closed")

    def _reset_backend(self) -> None:
        if self._backend is None:
            return
        try:
            self._backend.reset()
        except BackendUnavailable as exc:
            self._backend = None
            self._health = Health(component=Component.ATTENTION, status=HealthStatus.ERROR, code=exc.code, message=exc.message[:500])

    def _reset_signal_state(self) -> None:
        c = self.cfg
        self._tracker = PrimaryFaceTracker(c.track_gate_center, c.track_min_iou, c.track_lost_ms, c.primary_ambiguity_ratio)
        self._filters = {k: OneEuro(c.filter_min_cutoff_hz, c.filter_beta, c.filter_d_cutoff_hz)
                         for k in ("yaw", "pitch", "roll", "gyaw", "gpitch")}
        self._head_deb = Debouncer(c.debounce_ms, c.filter_reset_gap_ms)
        self._gaze_deb = Debouncer(c.debounce_ms, c.filter_reset_gap_ms)
        self._last_ts = -1
        self._last_face_t: float | None = None
        self._last_eye: EyeRotation | None = None
        self._last_eye_t = 0.0
        self._last_dir: tuple[Direction, float] | None = None
        self._active_model: DirectionModel | None = None

    def _reset_filters(self) -> None:
        for f in self._filters.values():
            f.reset()
        self._head_deb.reset()
        self._gaze_deb.reset()
        self._last_eye = None

    # ------------------------------------------------------------------ calibration
    def _require_session(self) -> None:
        if self._session_id is None:
            raise InvalidStateError(ErrorCode.INVALID_STATE, "attention analyzer has no active session")
        if self._backend is None:
            raise ProctorError(ErrorCode.MODEL_MISSING, "face model is not available", retryable=False)

    def calibration_start(self) -> CalibrationState:
        self._require_session()
        return self._calib.start()

    def calibration_target(self, target: CalibrationTarget) -> CalibrationState:
        self._require_session()
        return self._calib.select(CalibrationTarget(target))

    def calibration_state(self) -> CalibrationState:
        return self._calib.state()

    def calibration_finish(self) -> CalibrationState:
        self._require_session()
        return self._calib.finish()

    def calibration_cancel(self) -> CalibrationState:
        return self._calib.cancel()

    def calibration_skip(self, reason: str) -> CalibrationState:
        return self._calib.skip(reason)

    # ------------------------------------------------------------------ analysis
    def process(self, frame: FramePacket) -> Sequence[Observation]:
        with self._lock:
            if self._session_id is None or frame.meta.session_id != self._session_id:
                return []
            return [self._analyze(frame)]

    def _producer(self) -> Producer:
        return Producer(
            module="attention",
            version=MODULE_VERSION,
            model_id=self._model_id,
            model_sha256=self._model_sha,
            config_version=self.cfg.config_version,
        )

    def _obs(self, frame: FramePacket, **fields) -> AttentionObservation:
        meta = frame.meta
        reasons = list(dict.fromkeys(fields.pop("reasons", [])))[:MAX_REASONS]
        flags = list(dict.fromkeys(fields.pop("quality_flags", [])))[:MAX_REASONS]
        return AttentionObservation(
            observation_id=f"att-{meta.frame_id}-{meta.session_id}"[:128],
            session_id=meta.session_id,
            frame_id=meta.frame_id,
            t_session_ms=meta.t_session_ms,
            wall_time=meta.wall_time,
            source_mode=meta.source_mode,
            producer=self._producer(),
            latency_ms=max(0.0, (time.monotonic_ns() - meta.t_capture_mono_ns) / 1e6),
            reasons=reasons,
            quality_flags=flags,
            **fields,
        )

    def _analyze(self, frame: FramePacket) -> AttentionObservation:
        cfg = self.cfg
        t = float(frame.meta.t_session_ms)
        if self._backend is None:
            return self._obs(frame, status=ObservationStatus.ERROR, face_count=None, primary_face_present=None,
                             reasons=["face_model_unavailable"])
        image = frame.image
        h, w = image.shape[:2]
        model = self._calib.model() or self._generic_model
        if model is not self._active_model:
            self._active_model = model
            self._tracker.set_anchor(model.anchor_bbox)
            self._head_deb.reset()
            self._gaze_deb.reset()
        flags = [] if model.calibrated else ["uncalibrated"]

        stats = frame_stats(image)
        src = image
        if w > cfg.max_input_width:
            scale = cfg.max_input_width / w
            src = cv2.resize(image, (cfg.max_input_width, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
        rgb = np.ascontiguousarray(cv2.cvtColor(src, cv2.COLOR_BGR2RGB))
        ts = max(int(t), self._last_ts + 1)
        self._last_ts = ts
        t0 = time.perf_counter()
        try:
            faces = list(self._backend.detect(rgb, ts))
        except Exception as exc:
            self._errors += 1
            self._recent_errors.append(True)
            if self._errors <= 3 or self._errors % 100 == 0:  # no log flood at 15 fps
                log.warning("face inference failed on frame %s (%d so far): %s", frame.meta.frame_id, self._errors, exc)
            self._calib.offer(t, None, "inference_error")
            return self._obs(frame, status=ObservationStatus.ERROR, face_count=None, primary_face_present=None,
                             quality_flags=flags, reasons=["inference_error"])
        dt_ms = (time.perf_counter() - t0) * 1000.0
        self._infer_ms_ema = dt_ms if self._infer_ms_ema is None else 0.9 * self._infer_ms_ema + 0.1 * dt_ms
        self._frames += 1
        self._recent_errors.append(False)

        boxes = [face_bbox(f.landmarks) for f in faces]
        decision = self._tracker.update(boxes, t, aspect=w / max(h, 1))
        count = len(faces)
        reasons: list[str] = []
        if count >= 2:
            reasons.append("multiple_faces")
        if count >= cfg.num_faces:
            reasons.append("face_count_at_limit")
        face_boxes = [
            FaceBox(bbox=BBox(x_min=b[0], y_min=b[1], x_max=b[2], y_max=b[3]), confidence=None, is_primary=(i == decision.index))
            for i, b in enumerate(boxes)
        ]

        # ---- no face -------------------------------------------------------------------
        if count == 0:
            untrusted = no_face_trust(stats, cfg)
            if untrusted:
                self._calib.offer(t, None, "low_light" if "low_light" in untrusted else "poor_image")
                code = "no_face_low_light" if "low_light" in untrusted else "no_face_poor_image"
                return self._obs(frame, status=ObservationStatus.UNKNOWN, face_count=None, primary_face_present=None,
                                 quality=round(min(1.0, stats.luma / (4 * cfg.frame_dark_mean)), 3) if "low_light" in untrusted else 0.2,
                                 quality_flags=flags + untrusted, reasons=[code])
            self._calib.offer(t, None, "no_face")
            reasons.append("no_face_detected")
            if self._last_dir is not None and t - self._last_dir[1] <= cfg.face_lost_context_ms:
                d = self._last_dir[0]
                if d == Direction.DOWN:
                    reasons.append("face_lost_after_down")
                elif d in (Direction.LEFT, Direction.RIGHT):
                    reasons.append("face_lost_after_side")
                elif d == Direction.UP:
                    reasons.append("face_lost_after_up")
            return self._obs(frame, status=ObservationStatus.OK, face_count=0, faces=[], primary_face_present=False,
                             quality=round(min(1.0, stats.luma / (2 * cfg.frame_dark_mean)), 3), quality_flags=flags,
                             reasons=reasons)

        # ---- faces but no primary decision -----------------------------------------------
        if decision.index is None:
            reasons += decision.reasons
            self._calib.offer(t, None, "multiple_faces" if count >= 2 else "primary_uncertain")
            return self._obs(frame, status=ObservationStatus.DEGRADED, face_count=count, faces=face_boxes,
                             primary_face_present=None, quality_flags=flags, reasons=reasons)

        # ---- primary face ------------------------------------------------------------------
        reasons += decision.reasons
        face = faces[decision.index]
        bbox = boxes[decision.index]
        if decision.switched or (self._last_face_t is not None and t - self._last_face_t > cfg.filter_reset_gap_ms):
            self._reset_filters()
        self._last_face_t = t
        pose = head_pose_from_matrix(face.matrix)
        fstats = face_stats(image, bbox)
        outside = outside_ratio(face.landmarks)
        if pose is None:
            q, qflags = face_quality(fstats, outside, 0.0, False, cfg)
            self._calib.offer(t, None, "pose_unavailable")
            return self._obs(frame, status=ObservationStatus.DEGRADED, face_count=count, faces=face_boxes,
                             primary_face_present=True, quality=round(q, 3), quality_flags=flags + qflags,
                             reasons=reasons + ["pose_unavailable"])
        eyes = eye_features(face.landmarks, w, h, closed_openness=cfg.eyes_closed_openness,
                            blendshapes=face.blendshapes, blink_threshold=cfg.blink_blendshape)
        extreme = abs(pose.yaw) > cfg.extreme_yaw_deg or abs(pose.pitch) > cfg.extreme_pitch_deg
        q, qflags = face_quality(fstats, outside, min(eyes.width_right_px, eyes.width_left_px), extreme, cfg)
        flags += qflags

        rot = eye_rotation(eyes, pose.yaw, pose.pitch, model.rest, cfg)
        raw_rot = rot
        if rot.yaw is None and rot.pitch is None:
            if self._last_eye is not None and t - self._last_eye_t <= cfg.blink_hold_ms:
                rot = self._last_eye
                reasons.append("blink_hold")
            else:
                reasons.extend(rot.reasons or ("iris_unreliable",))
        else:
            self._last_eye, self._last_eye_t = rot, t
        method = GazeMethod.FUSED if (rot.yaw is not None or rot.pitch is not None) else GazeMethod.HEAD_POSE_ONLY
        f = self._filters
        yaw, pitch, roll = f["yaw"](pose.yaw, t), f["pitch"](pose.pitch, t), f["roll"](pose.roll, t)
        gyaw = f["gyaw"](pose.yaw + (rot.yaw or 0.0), t)
        gpitch = f["gpitch"](pose.pitch + (rot.pitch or 0.0), t)

        usable = q >= cfg.min_quality_for_direction and abs(pose.roll) <= cfg.max_abs_roll_deg
        if not usable:
            reasons.append("low_face_quality" if q < cfg.min_quality_for_direction else "extreme_roll")
            head_dir = gaze_dir = Direction.UNKNOWN
        else:
            hl = head_levels(model, yaw, pitch, cfg)
            head_exit = 1.0 - cfg.head_hysteresis_deg / max(cfg.head_yaw_threshold_deg, cfg.head_down_threshold_deg)
            head_dir = self._head_deb.update(pick_direction(hl, self._head_deb.current, head_exit), t)
            gl = gaze_levels(model, gyaw, gpitch, cfg)
            gaze_dir = self._gaze_deb.update(pick_direction(gl, self._gaze_deb.current, 1.0 - cfg.hysteresis), t)
            edge = "calibrated" if model.calibrated else "generic"
            if gaze_dir not in (Direction.CENTER, Direction.UNKNOWN):
                reasons.append(f"gaze_beyond_{edge}_{gaze_dir.value}_edge")
            if head_dir not in (Direction.CENTER, Direction.UNKNOWN):
                reasons.append(f"head_turned_{head_dir.value}")
            if pose.pitch < -cfg.extreme_pitch_deg:
                reasons.append("head_pitch_extreme_down")
            last = gaze_dir if gaze_dir != Direction.CENTER else head_dir
            self._last_dir = (last, t)

        # calibration samples come from the raw (unfiltered, not held) values of single-face frames
        if self._calib.collecting:
            rejection = None
            if count != 1:
                rejection = "multiple_faces"
            elif q < cfg.calibration_min_sample_quality:
                rejection = "low_light" if "low_light" in qflags else "low_quality"
            elif abs(pose.roll) > cfg.max_abs_roll_deg or extreme:
                rejection = "extreme_pose"
            elif raw_rot.yaw is None and raw_rot.pitch is None:
                rejection = "eyes_closed"
            sample = None if rejection else CalibrationSample(t, pose.yaw, pose.pitch, eyes, q, bbox)
            self._calib.offer(t, sample, rejection)

        confidence = q * (0.75 if method == GazeMethod.FUSED else 0.55) * (1.0 if model.calibrated else 0.7)
        degraded = bool([x for x in flags if x != "uncalibrated"]) or gaze_dir == Direction.UNKNOWN
        return self._obs(
            frame,
            status=ObservationStatus.DEGRADED if degraded else ObservationStatus.OK,
            quality=round(q, 3),
            quality_flags=flags,
            face_count=count,
            faces=face_boxes,
            primary_face_present=True,
            head_pose=HeadPose(yaw_deg=_clip_deg(yaw), pitch_deg=_clip_deg(pitch), roll_deg=_clip_deg(roll)),
            head_direction=head_dir,
            gaze=GazeEstimate(
                direction=gaze_dir,
                yaw_deg=_clip_deg(gyaw),
                pitch_deg=_clip_deg(gpitch),
                confidence=round(min(1.0, max(0.0, confidence)), 3),
                method=method,
                calibrated=model.calibrated,
            ),
            calibration_id=model.calibration_id if model.calibrated else None,
            reasons=reasons,
        )
