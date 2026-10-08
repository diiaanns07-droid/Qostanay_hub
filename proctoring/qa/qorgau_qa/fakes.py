"""QA-ONLY fault-injection doubles for the five module factories (owner: A09).

    QA_FAKES='{"capture":"ok","phone":"model_missing",...}' \
    python -m qorgau_qa.fakes -- -m proctor serve --token-stdin --port 0

Registers `proctor.capture|phone|attention|fusion|evidence` in `sys.modules` BEFORE the real backend
starts, so A01's ModuleRegistry discovers them through the agreed public factories exactly as it will
discover A02–A08. No backend file is modified. Unspecified factories keep normal discovery.
Always replaces `proctor.audio.monitor.AudioMonitor` too: LIVE wire fixtures must never open
a real microphone or load an audio model. This remains a labelled QA double, not an A14 test.

Every record these doubles produce is labelled `producer.module = "qa.fake_*"` and every Health
message starts with "QA FAULT-INJECTION DOUBLE". They exist to test the composition root's handling of
module FAILURES (camera missing, weights missing, storage down, slow/broken analyzers). They are not
module implementations and say nothing about CV quality.

Behaviours (value of each key; "name:arg" passes an argument):
  capture   ok | open_fails:<ERRORCODE> | no_frames | disconnect_after:<s> | factory_raises
  phone     ok | model_missing | load_raises | slow:<s> | factory_raises
  attention ok | model_missing
  fusion    ok | consume_raises | finish_raises
  evidence  ok | open_unavailable | open_raises | record_raises
"""

from __future__ import annotations

import json
import os
import runpy
import sys
import threading
import time
import types
from collections import deque
from typing import Any, Sequence

import numpy as np

from proctor_contracts.interfaces import CaptureError, FramePacket, ModelError, SessionClock, StorageError
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
    Explanation,
    FaceBox,
    FramePacketMeta,
    GazeEstimate,
    GazeMethod,
    Health,
    HealthObservation,
    HealthStatus,
    AttentionObservation,
    Incident,
    IncidentCategory,
    IncidentChange,
    IncidentChangeType,
    IncidentEndReason,
    IncidentRule,
    IncidentState,
    ObservationStatus,
    PhoneDetection,
    PhoneObservation,
    PhoneSignal,
    PhoneSignalName,
    PreviewFrameMeta,
    Producer,
    ReviewPriority,
    RuntimeMetrics,
    SignalState,
    SourceConfig,
    utc_now,
)

LABEL = "QA FAULT-INJECTION DOUBLE"
VERSION = "qa-0"
PHONE_CYCLE_MS = 8_000.0
PHONE_WINDOW_MS = (1_000.0, 4_000.0)  # phone "visible" in this part of every cycle (session time)


def _spec() -> dict[str, str]:
    return json.loads(os.environ.get("QA_FAKES", "{}"))


def _split(value: str) -> tuple[str, str | None]:
    name, _, arg = value.partition(":")
    return name, (arg or None)


def _health(component: Component, status: HealthStatus, code: str, extra: str = "") -> Health:
    return Health(component=component, status=status, code=code, message=f"{LABEL}: {extra or code}"[:500])


# --------------------------------------------------------------------------- capture


class _Consumer:
    def __init__(self, name: str, callback: Any, max_fps: float | None):
        self.name, self.callback = name, callback
        self.min_interval = 1.0 / max_fps if max_fps else 0.0
        self.cond = threading.Condition()
        self.pending: FramePacket | None = None
        self.stop = False
        self.processed = self.skipped = self.errors = 0
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
                    self.cond.wait(0.2)
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


class FakeCapture:
    def __init__(self, behaviour: str, arg: str | None, fps: float = 15.0):
        self.behaviour, self.arg, self.fps = behaviour, arg, fps
        self._lock = threading.Lock()
        self._consumers: dict[str, _Consumer] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ring: deque[FramePacket] = deque(maxlen=45)
        self._preview: tuple[PreviewFrameMeta, bytes] | None = None
        self._listener: Any = None
        self._frames = 0
        self._status = HealthStatus.STOPPED
        self._jpeg = self._make_jpeg()

    @staticmethod
    def _make_jpeg() -> bytes:
        try:
            import cv2

            ok, buf = cv2.imencode(".jpg", np.full((240, 320, 3), 90, np.uint8))
            return buf.tobytes() if ok else b""
        except Exception:
            return b""

    def open(self, session_id: str, source: SourceConfig, clock: SessionClock) -> None:
        if self.behaviour == "open_fails":
            code = ErrorCode(self.arg or "CAMERA_UNAVAILABLE")
            raise CaptureError(code, f"{LABEL}: simulated {code.value}", retryable=True)
        with self._lock:
            self._stop.clear()
            self._frames = 0
            self._ring.clear()
            self._preview = None
            self._thread = threading.Thread(target=self._run, args=(session_id, source, clock), daemon=True)
            for c in self._consumers.values():
                self._start(c)
            self._thread.start()
        self._status = HealthStatus.OK

    def _start(self, c: _Consumer) -> None:
        c.stop = False
        c.thread = threading.Thread(target=c.run, daemon=True)
        c.thread.start()

    def _run(self, session_id: str, source: SourceConfig, clock: SessionClock) -> None:
        if self.behaviour == "no_frames":
            self._stop.wait()
            return
        disconnect_at = time.monotonic() + float(self.arg) if self.behaviour == "disconnect_after" else None
        img = np.full((240, 320, 3), 90, np.uint8)
        img.flags.writeable = False
        fid = 0
        while not self._stop.is_set():
            if disconnect_at is not None and time.monotonic() >= disconnect_at:
                self._status = HealthStatus.UNAVAILABLE
                if self._listener is not None:
                    self._listener(_health(Component.CAPTURE, HealthStatus.UNAVAILABLE, "camera_disconnected", "simulated unplug"))
                self._stop.wait()
                return
            mono = time.monotonic_ns()
            t = clock.mono_to_session_ms(mono)
            meta = FramePacketMeta(
                session_id=session_id, frame_id=fid, t_session_ms=t, wall_time=clock.wall_at(t), t_capture_mono_ns=mono,
                width=320, height=240, source_mode=source.mode, source_id="qa:fake",
            )
            packet = FramePacket(meta=meta, image=img)
            self._ring.append(packet)
            self._frames += 1
            if self._jpeg:
                self._preview = (
                    PreviewFrameMeta(session_id=session_id, frame_id=fid, t_session_ms=t, wall_time=meta.wall_time, width=320, height=240, source_mode=source.mode, byte_length=len(self._jpeg)),
                    self._jpeg,
                )
            for c in list(self._consumers.values()):
                c.offer(packet)
            fid += 1
            self._stop.wait(1.0 / self.fps)

    def close(self, timeout_s: float = 3.0) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
            consumers = list(self._consumers.values())
        self._stop.set()
        if thread is not None:
            thread.join(timeout_s)
        for c in consumers:
            with c.cond:
                c.stop = True
                c.cond.notify()
            if c.thread is not None:
                c.thread.join(timeout_s)
                c.thread = None
        self._status = HealthStatus.STOPPED

    def add_consumer(self, name: str, callback: Any, *, max_fps: float | None = None) -> None:
        with self._lock:
            c = _Consumer(name, callback, max_fps)
            self._consumers[name] = c
            if self._thread is not None:
                self._start(c)

    def remove_consumer(self, name: str) -> None:
        with self._lock:
            c = self._consumers.pop(name, None)
        if c is not None:
            with c.cond:
                c.stop = True
                c.cond.notify()
            if c.thread is not None:
                c.thread.join(3.0)

    def latest_preview(self):
        return self._preview

    def get_frame(self, frame_id: int):
        return next((p for p in list(self._ring) if p.frame_id == frame_id), None)

    def set_health_listener(self, listener: Any) -> None:
        self._listener = listener

    def health(self) -> Health:
        status = HealthStatus.OK if self._status in (HealthStatus.OK, HealthStatus.STOPPED) else self._status
        return _health(Component.CAPTURE, status, "qa_fake_capture")

    def metrics(self) -> RuntimeMetrics:
        cms = [ConsumerMetrics(name=c.name, processed_fps=0.0, frames_processed=c.processed, frames_skipped=c.skipped, errors=c.errors) for c in self._consumers.values()]
        return RuntimeMetrics(window_s=1.0, capture_fps=self.fps if self._frames else 0.0, frames_captured=self._frames, frames_dropped=0, consumers=cms)


# --------------------------------------------------------------------------- analyzers


def _producer(name: str) -> Producer:
    return Producer(module=f"qa.fake_{name}", version=VERSION)


def _common(frame: FramePacket, prefix: str) -> dict[str, Any]:
    m = frame.meta
    return dict(
        observation_id=f"{prefix}-{m.session_id}-{m.frame_id}", session_id=m.session_id, frame_id=m.frame_id,
        t_session_ms=m.t_session_ms, wall_time=m.wall_time, source_mode=m.source_mode, status=ObservationStatus.OK,
        quality=1.0, quality_flags=["synthetic"],
    )


class FakePhone:
    name = "phone"

    def __init__(self, behaviour: str, arg: str | None):
        self.behaviour, self.arg = behaviour, arg
        self._load_health: Health | None = None

    def load(self) -> Health:
        if self.behaviour == "load_raises":
            raise ModelError(ErrorCode.MODEL_INVALID, f"{LABEL}: simulated corrupt weights")
        if self.behaviour == "model_missing":
            self._load_health = _health(Component.PHONE, HealthStatus.UNAVAILABLE, "model_missing", "weights file not found (simulated)")
        return self.health()

    def start_session(self, session_id: str, source_mode: Any) -> None:
        pass

    def process(self, frame: FramePacket) -> Sequence[PhoneObservation]:
        if self.behaviour == "slow":
            time.sleep(float(self.arg or 1.0))
        phase = frame.t_session_ms % PHONE_CYCLE_MS
        present = PHONE_WINDOW_MS[0] <= phase < PHONE_WINDOW_MS[1]
        dets = [PhoneDetection(bbox=BBox(x_min=0.4, y_min=0.5, x_max=0.55, y_max=0.8), confidence=0.9, class_name="cell phone", class_index=67)] if present else []
        sig = PhoneSignal(name=PhoneSignalName.PHONE_VISIBLE, state=SignalState.PRESENT if present else SignalState.ABSENT, reason="qa_fake_script")
        return [PhoneObservation(**_common(frame, "qa-phone"), producer=_producer("phone"), detections=dets, signals=[sig])]

    def end_session(self) -> None:
        pass

    def health(self) -> Health:
        return self._load_health or _health(Component.PHONE, HealthStatus.OK, "ok")

    def close(self) -> None:
        pass


class FakeAttention:
    name = "attention"
    TARGETS = [CalibrationTarget.CENTER, CalibrationTarget.LEFT, CalibrationTarget.RIGHT, CalibrationTarget.UP, CalibrationTarget.DOWN]
    NEED = 3

    def __init__(self, behaviour: str, arg: str | None):
        self.behaviour = behaviour
        self._lock = threading.Lock()
        self._cal = self._fresh(CalibrationPhase.NOT_STARTED)
        self._load_health: Health | None = None

    def _fresh(self, phase: CalibrationPhase) -> CalibrationState:
        return CalibrationState(
            calibration_id="qa-cal" if phase != CalibrationPhase.NOT_STARTED else None, phase=phase,
            targets=[CalibrationTargetStatus(target=t, state=CalibrationTargetState.PENDING, required_samples=self.NEED) for t in self.TARGETS],
            updated_at=utc_now(),
        )

    def load(self) -> Health:
        if self.behaviour == "model_missing":
            self._load_health = _health(Component.ATTENTION, HealthStatus.UNAVAILABLE, "model_missing", "face_landmarker.task not found (simulated)")
        return self.health()

    def start_session(self, session_id: str, source_mode: Any) -> None:
        with self._lock:
            self._cal = self._fresh(CalibrationPhase.NOT_STARTED)

    def process(self, frame: FramePacket) -> Sequence[AttentionObservation]:
        with self._lock:
            cal = self._cal
            if cal.phase == CalibrationPhase.COLLECTING and cal.current_target is not None:
                targets = []
                for t in cal.targets:
                    if t.target == cal.current_target and t.state == CalibrationTargetState.COLLECTING:
                        n = t.samples + 1
                        t = t.model_copy(update={"samples": n, "state": CalibrationTargetState.OK if n >= self.NEED else t.state})
                    targets.append(t)
                self._cal = cal.model_copy(update={"targets": targets, "updated_at": utc_now()})
        return [
            AttentionObservation(
                **_common(frame, "qa-att"), producer=_producer("attention"), face_count=1,
                faces=[FaceBox(bbox=BBox(x_min=0.35, y_min=0.2, x_max=0.65, y_max=0.6), confidence=0.9, is_primary=True)],
                primary_face_present=True, head_direction=Direction.CENTER,
                gaze=GazeEstimate(direction=Direction.CENTER, method=GazeMethod.HEAD_POSE_ONLY, calibrated=self._cal.phase == CalibrationPhase.COMPLETED),
            )
        ]

    def calibration_start(self) -> CalibrationState:
        with self._lock:
            self._cal = self._fresh(CalibrationPhase.COLLECTING)
            return self._cal

    def calibration_target(self, target: CalibrationTarget) -> CalibrationState:
        with self._lock:
            targets = [t.model_copy(update={"state": CalibrationTargetState.COLLECTING}) if t.target == target and t.state != CalibrationTargetState.OK else t for t in self._cal.targets]
            self._cal = self._cal.model_copy(update={"targets": targets, "current_target": target, "updated_at": utc_now()})
            return self._cal

    def calibration_state(self) -> CalibrationState:
        with self._lock:
            return self._cal

    def calibration_finish(self) -> CalibrationState:
        with self._lock:
            ok = all(t.state == CalibrationTargetState.OK for t in self._cal.targets)
            self._cal = self._cal.model_copy(update={"phase": CalibrationPhase.COMPLETED if ok else CalibrationPhase.FAILED, "current_target": None, "updated_at": utc_now()})
            return self._cal

    def calibration_cancel(self) -> CalibrationState:
        with self._lock:
            self._cal = self._fresh(CalibrationPhase.CANCELLED)
            return self._cal

    def calibration_skip(self, reason: str) -> CalibrationState:
        with self._lock:
            self._cal = self._cal.model_copy(update={"phase": CalibrationPhase.SKIPPED, "current_target": None, "updated_at": utc_now()})
            return self._cal

    def end_session(self) -> None:
        with self._lock:
            self._cal = self._fresh(CalibrationPhase.NOT_STARTED)

    def health(self) -> Health:
        return self._load_health or _health(Component.ATTENTION, HealthStatus.OK, "ok")

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- fusion


class FakeEngine:
    """phone_visible: PRESENT ≥ 0.5 s opens, ABSENT ≥ 0.3 s closes. Nothing else."""

    def __init__(self, session_id: str, source_mode: Any, behaviour: str):
        self.session_id, self.mode, self.behaviour = session_id, source_mode, behaviour
        self._since: float | None = None
        self._absent: float | None = None
        self._open: Incident | None = None
        self._n = 0
        self._paused = False

    def consume(self, obs: Any) -> list[IncidentChange]:
        if self.behaviour == "consume_raises":
            raise RuntimeError("qa fake engine: simulated consume failure")
        if self._paused or not isinstance(obs, PhoneObservation):
            return []
        state = obs.signals[0].state if obs.signals else SignalState.UNKNOWN
        t = obs.t_session_ms
        if state == SignalState.PRESENT:
            self._absent = None
            self._since = t if self._since is None else self._since
            if self._open is None and t - self._since >= 500:
                self._n += 1
                self._open = Incident(
                    incident_id=f"qa-inc-{self.session_id}-{self._n}", session_id=self.session_id, rule_id=IncidentRule.PHONE_VISIBLE,
                    category=IncidentCategory.PHONE, state=IncidentState.OPEN, priority=ReviewPriority.LOW, t_start_ms=self._since,
                    wall_start=obs.wall_time, duration_ms=t - self._since, source_mode=self.mode,
                    explanation=Explanation(summary_ru=f"{LABEL}: сценарный телефон", caveats_ru=["QA fault injection, не CV"]),
                    observation_ids=[obs.observation_id], observation_count=1, trigger_frame_id=obs.frame_id,
                    rule_version="qa-0", config_version="qa-0", update_seq=0,
                )
                return [IncidentChange(change=IncidentChangeType.OPENED, incident=self._open)]
        elif state == SignalState.ABSENT and self._since is not None:
            self._absent = t if self._absent is None else self._absent
            if t - self._absent >= 300:
                end, self._since, self._absent = self._absent, None, None
                return self._close(end, IncidentEndReason.CONDITION_CLEARED)
        return []

    def _close(self, t: float, reason: IncidentEndReason) -> list[IncidentChange]:
        if self._open is None:
            return []
        inc, self._open = self._open, None
        closed = inc.model_copy(update={"state": IncidentState.CLOSED, "t_end_ms": max(t, inc.t_start_ms), "duration_ms": max(0.0, t - inc.t_start_ms), "end_reason": reason, "update_seq": inc.update_seq + 1})
        return [IncidentChange(change=IncidentChangeType.CLOSED, incident=closed)]

    def advance(self, t: float) -> list[IncidentChange]:
        return []

    def set_paused(self, paused: bool, t: float) -> list[IncidentChange]:
        self._paused = paused
        self._since = self._absent = None
        return self._close(t, IncidentEndReason.SESSION_PAUSED) if paused else []

    def finish(self, t: float, reason: IncidentEndReason) -> list[IncidentChange]:
        if self.behaviour == "finish_raises":
            raise RuntimeError("qa fake engine: simulated finish failure")
        return self._close(t, reason)

    def config_snapshot(self) -> dict[str, Any]:
        return {"engine": "qa_fake", "open_after_ms": 500, "close_after_ms": 300}


# --------------------------------------------------------------------------- evidence


class FakeStore:
    def __init__(self, behaviour: str):
        self.behaviour = behaviour
        self._lock = threading.Lock()
        self._open_health: Health | None = None
        self.sessions: dict[str, Any] = {}
        self.incidents: dict[str, dict[str, Incident]] = {}
        self.observations = 0

    def open(self) -> Health:
        if self.behaviour == "open_raises":
            raise StorageError(ErrorCode.STORAGE_ERROR, f"{LABEL}: database is locked (simulated)")
        if self.behaviour == "open_unavailable":
            self._open_health = _health(Component.EVIDENCE, HealthStatus.UNAVAILABLE, "storage_unwritable", "data dir read-only (simulated)")
        return self.health()

    def close(self) -> None:
        pass

    def upsert_session(self, info: Any) -> None:
        with self._lock:
            self.sessions[info.session_id] = info

    def record_observation(self, observation: Any) -> None:
        if self.behaviour == "record_raises":
            raise StorageError(ErrorCode.STORAGE_ERROR, f"{LABEL}: disk full (simulated)")
        with self._lock:
            self.observations += 1

    def record_incident_change(self, change: IncidentChange) -> None:
        if self.behaviour == "record_raises":
            raise StorageError(ErrorCode.STORAGE_ERROR, f"{LABEL}: disk full (simulated)")
        with self._lock:
            self.incidents.setdefault(change.incident.session_id, {})[change.incident.incident_id] = change.incident

    def capture_snapshot(self, session_id: str, incident_id: str, frame: Any) -> None:
        return None

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self.sessions.pop(session_id, None)
            self.incidents.pop(session_id, None)

    def health(self) -> Health:
        return self._open_health or _health(Component.EVIDENCE, HealthStatus.OK, "ok")

    def create_router(self, context: Any) -> Any:
        from fastapi import APIRouter

        router = APIRouter()
        store = self

        @router.get("/sessions/{session_id}/incidents", response_model=list[Incident])
        def incidents(session_id: str) -> list[Incident]:
            with store._lock:
                return sorted(store.incidents.get(session_id, {}).values(), key=lambda i: i.t_start_ms)

        @router.get("/qa-fake/stats")
        def stats() -> dict[str, int]:
            with store._lock:
                return {"observations": store.observations, "incidents": sum(len(v) for v in store.incidents.values())}

        return router


# --------------------------------------------------------------------------- install


class FakeAudioMonitor:
    """No PCM, device API, model, network, thread or microphone lease is used."""

    def __init__(self, session_id, source_mode, clock, publish):
        self.session_id, self.mode, self.clock, self.publish = session_id, source_mode, clock, publish
        self.active = False
        self.starts = 0

    def start(self):
        if self.active:
            return
        self.active = True
        self.starts += 1
        t = self.clock.now_ms()
        self.publish(HealthObservation(
            observation_id=f"qa-audio-{self.session_id}-{self.starts}", session_id=self.session_id,
            frame_id=None, t_session_ms=t, wall_time=self.clock.wall_at(t), source_mode=self.mode,
            producer=Producer(module="qa.fake_audio", version=VERSION), status=ObservationStatus.DEGRADED,
            health=_health(Component.AUDIO, HealthStatus.DEGRADED, "qa_audio_isolated", "no microphone or audio model"),
        ))

    def stop(self):
        self.active = False


def _raise_factory(what: str):
    def factory(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"{LABEL}: {what} factory failed (simulated)")

    return factory


def install(spec: dict[str, str]) -> None:
    import proctor  # the real package; fakes become its submodules
    import proctor.audio  # package __init__ only; never import the production monitor/vad

    audio = types.ModuleType("proctor.audio.monitor")
    audio.AudioMonitor = FakeAudioMonitor
    sys.modules["proctor.audio.monitor"] = audio
    proctor.audio.monitor = audio

    for key, value in spec.items():
        behaviour, arg = _split(value)
        mod = types.ModuleType(f"proctor.{key}")
        mod.__doc__ = f"{LABEL} for {key}: {value}"
        if key == "capture":
            mod.create_capture_service = _raise_factory("capture") if behaviour == "factory_raises" else (lambda s, b=behaviour, a=arg: FakeCapture(b, a))
        elif key == "phone":
            mod.create_phone_analyzer = _raise_factory("phone") if behaviour == "factory_raises" else (lambda s, b=behaviour, a=arg: FakePhone(b, a))
        elif key == "attention":
            mod.create_attention_analyzer = lambda s, b=behaviour, a=arg: FakeAttention(b, a)
        elif key == "fusion":
            mod.create_incident_engine = lambda sid, m, s, b=behaviour: FakeEngine(sid, m, b)
        elif key == "evidence":
            mod.create_evidence_store = lambda s, b=behaviour: FakeStore(b)
        else:
            raise SystemExit(f"unknown QA_FAKES key {key!r}")
        sys.modules[f"proctor.{key}"] = mod
        setattr(proctor, key, mod)
    print(f"{LABEL}: injected {sorted(spec)}; audio monitor isolated (no device/model)", file=sys.stderr, flush=True)


def main(argv: list[str]) -> int:
    rest = argv[argv.index("--") + 1 :] if "--" in argv else argv
    if rest[:2] != ["-m", "proctor"]:
        print("usage: python -m qorgau_qa.fakes -- -m proctor serve ...", file=sys.stderr)
        return 2
    install(_spec())
    sys.argv = ["proctor", *rest[2:]]
    runpy.run_module("proctor", run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
