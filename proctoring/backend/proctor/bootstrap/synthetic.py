"""BOOTSTRAP-ONLY synthetic pipeline parts (owner: A01).

Everything here is clearly marked SYNTHETIC: source_mode="synthetic", producer.module starts
with "bootstrap.". It exists so the API/stream/UI can be exercised before A02-A08 deliver.
It is NOT computer vision, NOT a capture implementation and is NEVER used for live/replay.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Sequence

import numpy as np

from proctor_contracts.interfaces import FramePacket, FrameCallback, HealthListener, SessionClock
from proctor_contracts.v1 import (
    BBox,
    CalibrationPhase,
    CalibrationState,
    CalibrationTarget,
    CalibrationTargetState,
    CalibrationTargetStatus,
    Component,
    ConsumerMetrics,
    Direction,
    ErrorCode,
    FaceBox,
    FramePacketMeta,
    GazeEstimate,
    GazeMethod,
    HeadPose,
    Health,
    HealthStatus,
    AttentionObservation,
    Observation,
    ObservationStatus,
    PhoneDetection,
    PhoneObservation,
    PhoneSignal,
    PhoneSignalName,
    PreviewFrameMeta,
    Producer,
    RuntimeMetrics,
    SignalState,
    SourceConfig,
    SourceMode,
    utc_now,
)
from proctor_contracts.interfaces import CaptureError, InvalidStateError

try:  # cv2 is part of the "cv" extra; synthetic preview degrades gracefully without it
    import cv2  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    cv2 = None

BOOTSTRAP_VERSION = "bootstrap-0"
# Scripted timeline (session ms modulo the cycle): phone visible 3.0-7.0 s, gaze down 10-14 s.
SCRIPT_CYCLE_MS = 20_000.0
PHONE_WINDOW_MS = (3_000.0, 7_000.0)
GAZE_DOWN_WINDOW_MS = (10_000.0, 14_000.0)


def _in_window(t_ms: float, window: tuple[float, float]) -> bool:
    phase = t_ms % SCRIPT_CYCLE_MS
    return window[0] <= phase < window[1]


class _Consumer:
    def __init__(self, name: str, callback: FrameCallback, max_fps: float | None):
        self.name = name
        self.callback = callback
        self.min_interval = 1.0 / max_fps if max_fps else 0.0
        self.cond = threading.Condition()
        self.pending: FramePacket | None = None
        self.stop = False
        self.processed = 0
        self.skipped = 0
        self.errors = 0
        self.thread: threading.Thread | None = None

    def offer(self, packet: FramePacket) -> None:
        with self.cond:
            if self.pending is not None:
                self.skipped += 1
            self.pending = packet
            self.cond.notify()

    def run(self) -> None:
        last = 0.0
        while True:
            with self.cond:
                while self.pending is None and not self.stop:
                    self.cond.wait(0.5)
                if self.stop:
                    return
                packet, self.pending = self.pending, None
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            try:
                self.callback(packet)
                self.processed += 1
            except Exception:
                self.errors += 1


class SyntheticCaptureService:
    """Implements proctor_contracts.interfaces.CaptureService for source.mode == synthetic only."""

    def __init__(self, fps: float = 15.0, preview_fps: float = 15.0, ring_seconds: float = 3.0):
        self._fps = fps
        self._preview_interval = 1.0 / max(preview_fps, 1.0)
        self._lock = threading.Lock()
        self._consumers: dict[str, _Consumer] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ring: deque[FramePacket] = deque(maxlen=max(1, int(fps * ring_seconds)))
        self._preview: tuple[PreviewFrameMeta, bytes] | None = None
        self._listener: HealthListener | None = None
        self._state = HealthStatus.STOPPED
        self._session_id: str | None = None
        self._frames = 0
        self._opened_at = 0.0

    # --- CaptureService ---
    def open(self, session_id: str, source: SourceConfig, clock: SessionClock) -> None:
        if source.mode != SourceMode.SYNTHETIC:
            raise CaptureError(
                ErrorCode.MODULE_NOT_INTEGRATED,
                "Bootstrap capture supports only the synthetic source; live/replay need proctor.capture (A02)",
            )
        with self._lock:
            if self._thread is not None:
                raise InvalidStateError(ErrorCode.SESSION_ACTIVE, "capture already open")
            self._stop.clear()
            self._session_id = session_id
            self._frames = 0
            self._ring.clear()
            self._preview = None
            self._opened_at = time.monotonic()
            self._thread = threading.Thread(
                target=self._run, args=(session_id, source, clock), name="bootstrap-synthetic-capture", daemon=True
            )
            for consumer in self._consumers.values():
                self._start_consumer(consumer)
            self._thread.start()
        self._set_state(HealthStatus.OK, "synthetic_running")

    def close(self, timeout_s: float = 3.0) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
            consumers = list(self._consumers.values())
        self._stop.set()
        if thread is not None:
            thread.join(timeout_s)
        for consumer in consumers:
            with consumer.cond:
                consumer.stop = True
                consumer.cond.notify()
            if consumer.thread is not None:
                consumer.thread.join(timeout_s)
                consumer.thread = None
        self._ring.clear()
        if thread is not None:
            self._set_state(HealthStatus.STOPPED, "closed")

    def add_consumer(self, name: str, callback: FrameCallback, *, max_fps: float | None = None) -> None:
        with self._lock:
            if name in self._consumers:
                raise ValueError(f"consumer {name!r} already registered")
            consumer = _Consumer(name, callback, max_fps)
            self._consumers[name] = consumer
            if self._thread is not None:
                self._start_consumer(consumer)

    def remove_consumer(self, name: str) -> None:
        with self._lock:
            consumer = self._consumers.pop(name, None)
        if consumer is not None:
            with consumer.cond:
                consumer.stop = True
                consumer.cond.notify()
            if consumer.thread is not None:
                consumer.thread.join(3.0)

    def latest_preview(self) -> tuple[PreviewFrameMeta, bytes] | None:
        return self._preview

    def get_frame(self, frame_id: int) -> FramePacket | None:
        for packet in list(self._ring):
            if packet.frame_id == frame_id:
                return packet
        return None

    def set_health_listener(self, listener: HealthListener | None) -> None:
        self._listener = listener

    def health(self) -> Health:
        return Health(
            component=Component.CAPTURE,
            status=self._state,
            code="synthetic_running" if self._state == HealthStatus.OK else "synthetic_stopped",
            message="SYNTHETIC frames from proctor.bootstrap (not a camera)",
            details={"preview": cv2 is not None},
        )

    def metrics(self) -> RuntimeMetrics:
        elapsed = max(time.monotonic() - self._opened_at, 1e-6) if self._thread else 1.0
        consumers = [
            ConsumerMetrics(
                name=c.name,
                processed_fps=round(c.processed / elapsed, 2) if self._thread else 0.0,
                frames_processed=c.processed,
                frames_skipped=c.skipped,
                errors=c.errors,
            )
            for c in list(self._consumers.values())
        ]
        return RuntimeMetrics(
            session_id=self._session_id,
            window_s=elapsed,
            capture_fps=round(self._frames / elapsed, 2) if self._thread else 0.0,
            frames_captured=self._frames,
            frames_dropped=0,
            consumers=consumers,
        )

    # --- internals ---
    def _start_consumer(self, consumer: _Consumer) -> None:
        consumer.stop = False
        consumer.pending = None
        consumer.thread = threading.Thread(target=consumer.run, name=f"consumer-{consumer.name}", daemon=True)
        consumer.thread.start()

    def _set_state(self, status: HealthStatus, code: str) -> None:
        self._state = status
        listener = self._listener
        if listener is not None:
            try:
                listener(self.health())
            except Exception:
                pass

    def _run(self, session_id: str, source: SourceConfig, clock: SessionClock) -> None:
        interval = 1.0 / self._fps
        next_t = time.monotonic()
        last_preview = 0.0
        frame_id = 0
        w, h = source.width, source.height
        base = np.linspace(40, 90, w, dtype=np.uint8)[None, :, None].repeat(h, axis=0).repeat(3, axis=2)
        while not self._stop.is_set():
            mono = time.monotonic_ns()
            t_ms = clock.mono_to_session_ms(mono)
            img = base.copy()
            x = int((frame_id * 7) % max(w - 60, 1))
            img[h // 2 - 30 : h // 2 + 30, x : x + 60] = (200, 200, 200)
            if cv2 is not None:
                cv2.putText(img, f"SYNTHETIC #{frame_id}", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
            img.flags.writeable = False
            meta = FramePacketMeta(
                session_id=session_id,
                frame_id=frame_id,
                t_session_ms=t_ms,
                wall_time=clock.wall_at(t_ms),
                t_capture_mono_ns=mono,
                width=w,
                height=h,
                source_mode=SourceMode.SYNTHETIC,
                source_id="synthetic:bootstrap",
            )
            packet = FramePacket(meta=meta, image=img)
            self._ring.append(packet)
            self._frames += 1
            for consumer in list(self._consumers.values()):
                consumer.offer(packet)
            now = time.monotonic()
            if cv2 is not None and now - last_preview >= self._preview_interval:
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ok:
                    data = buf.tobytes()
                    self._preview = (
                        PreviewFrameMeta(
                            session_id=session_id,
                            frame_id=frame_id,
                            t_session_ms=t_ms,
                            wall_time=meta.wall_time,
                            width=w,
                            height=h,
                            source_mode=SourceMode.SYNTHETIC,
                            byte_length=len(data),
                        ),
                        data,
                    )
                    last_preview = now
            frame_id += 1
            next_t += interval
            delay = next_t - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_t = time.monotonic()


def _producer(module: str) -> Producer:
    return Producer(module=f"bootstrap.{module}", version=BOOTSTRAP_VERSION, config_version="script-v1")


def _obs_common(frame: FramePacket, prefix: str) -> dict:
    meta = frame.meta
    return dict(
        observation_id=f"{prefix}-{meta.frame_id}",
        session_id=meta.session_id,
        frame_id=meta.frame_id,
        t_session_ms=meta.t_session_ms,
        wall_time=meta.wall_time,
        source_mode=meta.source_mode,
        quality=1.0,
        quality_flags=["synthetic"],
        latency_ms=max(0.0, (time.monotonic_ns() - meta.t_capture_mono_ns) / 1e6),
    )


class ScriptedPhoneAnalyzer:
    """FrameAnalyzer emitting SCRIPTED phone observations on synthetic frames (no CV)."""

    name = "phone"

    def load(self) -> Health:
        return self.health()

    def start_session(self, session_id: str, source_mode: SourceMode) -> None:
        if source_mode != SourceMode.SYNTHETIC:
            raise ValueError("ScriptedPhoneAnalyzer is synthetic-only")

    def process(self, frame: FramePacket) -> Sequence[Observation]:
        visible = _in_window(frame.t_session_ms, PHONE_WINDOW_MS)
        detections = (
            [
                PhoneDetection(
                    bbox=BBox(x_min=0.40, y_min=0.55, x_max=0.50, y_max=0.80),
                    confidence=0.9,
                    class_name="cell phone (scripted)",
                    class_index=0,
                    track_id="syn-track-1",
                    track_quality=1.0,
                )
            ]
            if visible
            else []
        )
        signals = [
            PhoneSignal(
                name=PhoneSignalName.PHONE_VISIBLE,
                state=SignalState.PRESENT if visible else SignalState.ABSENT,
                confidence=0.9 if visible else None,
                track_id="syn-track-1" if visible else None,
                reason="scripted_synthetic",
            ),
            PhoneSignal(
                name=PhoneSignalName.POSSIBLE_SCREEN_CAPTURE,
                state=SignalState.INSUFFICIENT_EVIDENCE,
                reason="scripted_synthetic",
            ),
        ]
        return [
            PhoneObservation(
                **_obs_common(frame, "syn-phone"),
                producer=_producer("phone"),
                status=ObservationStatus.OK,
                detections=detections,
                signals=signals,
            )
        ]

    def end_session(self) -> None:
        pass

    def health(self) -> Health:
        return Health(
            component=Component.PHONE,
            status=HealthStatus.DEGRADED,
            code="synthetic_script",
            message="SCRIPTED synthetic phone observations (bootstrap), not a detector",
        )

    def close(self) -> None:
        pass


class ScriptedAttentionAnalyzer:
    """AttentionAnalyzer emitting SCRIPTED attention observations; calibration counts frames only."""

    name = "attention"
    REQUIRED_SAMPLES = 10

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cal = self._fresh_cal(CalibrationPhase.NOT_STARTED)

    def _fresh_cal(self, phase: CalibrationPhase) -> CalibrationState:
        return CalibrationState(
            calibration_id="syn-cal" if phase != CalibrationPhase.NOT_STARTED else None,
            phase=phase,
            targets=[
                CalibrationTargetStatus(target=t, state=CalibrationTargetState.PENDING, required_samples=self.REQUIRED_SAMPLES)
                for t in CalibrationTarget
            ],
            message_code="synthetic_calibration" if phase != CalibrationPhase.NOT_STARTED else None,
            updated_at=utc_now(),
        )

    def load(self) -> Health:
        return self.health()

    def start_session(self, session_id: str, source_mode: SourceMode) -> None:
        if source_mode != SourceMode.SYNTHETIC:
            raise ValueError("ScriptedAttentionAnalyzer is synthetic-only")
        with self._lock:
            self._cal = self._fresh_cal(CalibrationPhase.NOT_STARTED)

    def process(self, frame: FramePacket) -> Sequence[Observation]:
        self._collect_sample()
        down = _in_window(frame.t_session_ms, GAZE_DOWN_WINDOW_MS)
        direction = Direction.DOWN if down else Direction.CENTER
        with self._lock:
            calibrated = self._cal.phase == CalibrationPhase.COMPLETED
            cal_id = self._cal.calibration_id if calibrated else None
        return [
            AttentionObservation(
                **_obs_common(frame, "syn-att"),
                producer=_producer("attention"),
                status=ObservationStatus.OK,
                face_count=1,
                faces=[FaceBox(bbox=BBox(x_min=0.35, y_min=0.15, x_max=0.62, y_max=0.55), confidence=1.0, is_primary=True)],
                primary_face_present=True,
                head_pose=HeadPose(yaw_deg=0.0, pitch_deg=-25.0 if down else 0.0, roll_deg=0.0),
                head_direction=direction,
                gaze=GazeEstimate(direction=direction, method=GazeMethod.HEAD_POSE_ONLY, calibrated=calibrated),
                calibration_id=cal_id,
                reasons=["scripted_synthetic"],
            )
        ]

    def _collect_sample(self) -> None:
        with self._lock:
            cal = self._cal
            if cal.phase != CalibrationPhase.COLLECTING or cal.current_target is None:
                return
            targets = []
            for t in cal.targets:
                if t.target == cal.current_target and t.state == CalibrationTargetState.COLLECTING:
                    samples = t.samples + 1
                    state = CalibrationTargetState.OK if samples >= t.required_samples else t.state
                    t = t.model_copy(update={"samples": samples, "state": state, "quality": 1.0})
                targets.append(t)
            self._cal = cal.model_copy(update={"targets": targets, "updated_at": utc_now()})

    def calibration_start(self) -> CalibrationState:
        with self._lock:
            self._cal = self._fresh_cal(CalibrationPhase.COLLECTING)
            return self._cal

    def calibration_target(self, target: CalibrationTarget) -> CalibrationState:
        with self._lock:
            if self._cal.phase != CalibrationPhase.COLLECTING:
                raise InvalidStateError(ErrorCode.INVALID_STATE, "calibration is not collecting")
            targets = [
                t.model_copy(update={"state": CalibrationTargetState.COLLECTING, "samples": 0})
                if t.target == target
                else t
                for t in self._cal.targets
            ]
            self._cal = self._cal.model_copy(update={"targets": targets, "current_target": target, "updated_at": utc_now()})
            return self._cal

    def calibration_state(self) -> CalibrationState:
        with self._lock:
            return self._cal

    def calibration_finish(self) -> CalibrationState:
        with self._lock:
            ok = all(t.state == CalibrationTargetState.OK for t in self._cal.targets)
            self._cal = self._cal.model_copy(
                update={
                    "phase": CalibrationPhase.COMPLETED if ok else CalibrationPhase.FAILED,
                    "current_target": None,
                    "message_code": "synthetic_calibration" if ok else "targets_incomplete",
                    "updated_at": utc_now(),
                }
            )
            return self._cal

    def calibration_cancel(self) -> CalibrationState:
        with self._lock:
            self._cal = self._fresh_cal(CalibrationPhase.CANCELLED)
            return self._cal

    def calibration_skip(self, reason: str) -> CalibrationState:
        with self._lock:
            self._cal = self._cal.model_copy(
                update={"phase": CalibrationPhase.SKIPPED, "current_target": None, "message_code": "skipped_by_operator", "updated_at": utc_now()}
            )
            return self._cal

    def end_session(self) -> None:
        with self._lock:
            self._cal = self._fresh_cal(CalibrationPhase.NOT_STARTED)

    def health(self) -> Health:
        return Health(
            component=Component.ATTENTION,
            status=HealthStatus.DEGRADED,
            code="synthetic_script",
            message="SCRIPTED synthetic attention observations (bootstrap), not face analysis",
        )

    def close(self) -> None:
        pass
