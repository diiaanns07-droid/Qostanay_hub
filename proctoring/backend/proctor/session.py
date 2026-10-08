"""Session lifecycle (owner: A01).

One SessionManager per backend process. At most ONE non-terminal session exists at a time,
so two concurrent camera captures and cross-session mixing are impossible by construction:
  * capture is opened at preflight and closed at finish/abort (never by another module);
  * every observation/incident is checked against the runtime's session_id;
  * the incident engine and fusion thread are created per session and discarded at the end.

State machine (CONTRACTS.md §Lifecycle):
  created --preflight--> preflight --calibration/start--> calibrating --calibration/finish--> ready
  preflight|calibrating --calibration/skip--> ready
  calibrating --calibration/cancel--> preflight
  ready --start--> running <--pause/resume--> paused
  any non-terminal --finish--> finished ; any non-terminal --abort--> aborted
Pause = operator action: CV analysis is not fed to fusion/storage, open incidents close with
end_reason=session_paused, the interval counts as a coverage gap, exam answers are rejected,
and the shell (A06) releases environment restrictions until resume.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from uuid import uuid4

import numpy as np

from proctor_contracts.interfaces import (
    AttentionAnalyzer,
    CaptureService,
    EvidenceStore,
    FrameAnalyzer,
    FramePacket,
    IncidentEngine,
    InvalidStateError,
    NotFoundError,
    ProctorError,
    SessionClock,
)
from proctor_contracts.v1 import (
    AbortRequest,
    CalibrationMsg,
    CalibrationPhase,
    CalibrationState,
    CalibrationTarget,
    CheckStatus,
    Component,
    EnvironmentEventAck,
    EnvironmentEventBatch,
    EnvironmentCapabilities,
    EnvironmentObservation,
    ErrorCode,
    ExamDefinition,
    Health,
    HealthObservation,
    HealthStatus,
    IncidentChange,
    IncidentChangeType,
    IncidentEndReason,
    IncidentMsg,
    Observation,
    ObservationMsg,
    ObservationStatus,
    PauseRequest,
    PreflightCheck,
    PreflightCheckId,
    PreflightReport,
    PreviewFrameMeta,
    Producer,
    RuntimeMetrics,
    SessionCreate,
    SessionInfo,
    SessionState,
    SessionStateMsg,
    ApiErrorBody,
    HealthMsg,
    SourceMode,
    utc_now,
)

from .settings import BACKEND_VERSION, Settings

log = logging.getLogger("proctor.session")

ACTIVE_STATES = {
    SessionState.CREATED,
    SessionState.PREFLIGHT,
    SessionState.CALIBRATING,
    SessionState.READY,
    SessionState.RUNNING,
    SessionState.PAUSED,
}
TERMINAL_STATES = {SessionState.FINISHED, SessionState.ABORTED, SessionState.FAILED}
FUSION_QUEUE_MAX = 2000
FIRST_FRAME_TIMEOUT_S = 5.0


class Broadcaster(Protocol):
    def publish(self, message: Any, session_id: str | None) -> None:
        """Thread-safe fan-out of a StreamPayload model to WebSocket clients."""
        ...


@dataclass
class PipelinePart:
    """A pipeline part chosen for a session + how it was chosen (shown in preflight/health)."""

    impl: Any | None
    label: str  # "module" | "bootstrap" | "missing"
    health: Health


@dataclass
class Pipeline:
    capture: PipelinePart
    phone: PipelinePart
    attention: PipelinePart
    store: PipelinePart
    engine_factory: Callable[[str, SourceMode], IncidentEngine] | None
    engine_label: str
    engine_health: Health


class PipelineProvider(Protocol):
    def pipeline_for(self, mode: SourceMode) -> Pipeline: ...

    def environment_capabilities(self) -> EnvironmentCapabilities | None: ...


FAULT_REPORT_INTERVAL_S = 5.0  # re-report an ongoing fault at most this often
FAULT_RECOVERY_S = 2.0  # a component is "recovered" after a success this long after its last failure


@dataclass
class _Fault:
    component: Component
    code: str
    message: str
    count: int = 0
    first_t_ms: float = 0.0
    last_t_ms: float = 0.0
    last_failure_mono: float = 0.0
    reported_mono: float = 0.0
    active: bool = False


class PipelineFaults:
    """Makes pipeline exceptions VISIBLE (QA-BUG-005) without feedback loops.

    fail() returns True only when the caller should report now (first failure, or an ongoing fault
    after FAULT_REPORT_INTERVAL_S); ok() returns True once when a failing component recovers.
    Reporting never writes to the component that failed (no recursive error records).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._faults: dict[Component, _Fault] = {}

    def fail(self, component: Component, code: str, exc: BaseException, t_ms: float) -> bool:
        now = time.monotonic()
        message = f"{type(exc).__name__}: {exc}"[:300]
        with self._lock:
            f = self._faults.get(component)
            if f is None or not f.active:
                f = _Fault(component=component, code=code, message=message, first_t_ms=t_ms)
                self._faults[component] = f
            f.count += 1
            f.code, f.message, f.last_t_ms, f.last_failure_mono = code, message, t_ms, now
            report = not f.active or now - f.reported_mono >= FAULT_REPORT_INTERVAL_S
            f.active = True
            if report:
                f.reported_mono = now
            return report

    def ok(self, component: Component) -> bool:
        with self._lock:
            f = self._faults.get(component)
            if f is None or not f.active or time.monotonic() - f.last_failure_mono < FAULT_RECOVERY_S:
                return False
            f.active = False
            return True

    def health(self) -> list[Health]:
        with self._lock:
            return [
                Health(
                    component=f.component,
                    status=HealthStatus.DEGRADED,
                    code=f.code,
                    message=f"Pipeline errors in this session: {f.message}"[:500],
                    since_t_session_ms=f.first_t_ms,
                    details={"errors": f.count, "last_t_session_ms": round(f.last_t_ms, 1)},
                )
                for f in self._faults.values()
                if f.active
            ]

    def snapshot(self, component: Component) -> _Fault | None:
        with self._lock:
            f = self._faults.get(component)
            return None if f is None else _Fault(**f.__dict__)


@dataclass
class _Control:
    kind: str  # "pause" | "resume" | "finish"
    t_ms: float
    reason: IncidentEndReason | None = None
    done: threading.Event = field(default_factory=threading.Event)


class SessionRuntime:
    def __init__(
        self,
        settings: Settings,
        info: SessionInfo,
        clock: SessionClock,
        pipeline: Pipeline,
        broadcaster: Broadcaster,
        provider: PipelineProvider,
    ):
        self.settings = settings
        self.session_id = info.session_id
        self.mode = info.source_mode
        self.clock = clock
        self.pipeline = pipeline
        self._provider = provider
        self._bus = broadcaster
        self._lock = threading.RLock()
        self._info = info
        self._preflight: PreflightReport | None = None
        self._capture_open = False
        self._consumers: list[str] = []
        self._engine: IncidentEngine | None = None
        self._queue: queue.Queue[Observation | _Control] = queue.Queue(maxsize=FUSION_QUEUE_MAX)
        self._fusion_thread: threading.Thread | None = None
        self._accepting = False  # observations go to fusion only while running and not closing
        self._env_seqs: set[int] = set()
        self._pause_started_ms: float | None = None
        self._timeline_max_ms = 0.0
        self.counters = {"session_mismatch": 0, "fusion_dropped": 0, "fusion_errors": 0, "analyzer_errors": 0, "store_errors": 0}
        self.faults = PipelineFaults()
        self._info_lock = threading.Lock()  # guards _info; never held while waiting on the fusion thread
        self._analyzer_status: dict[Component, HealthStatus] = {}
        self.health_reporter: Callable[[], Any] | None = None  # set by the app: builds a HealthReport

    # ------------------------------------------------------------------ info
    @property
    def info(self) -> SessionInfo:
        return self._info

    @property
    def state(self) -> SessionState:
        return self._info.state

    def _update(self, **changes: Any) -> SessionInfo:
        with self._info_lock:
            self._info = self._info.model_copy(update=changes)
            info = self._info
            store = self.pipeline.store.impl
            if store is not None:
                try:
                    store.upsert_session(info)
                except Exception as exc:
                    # Report without re-entering _update (non-reentrant lock, no recursive store write).
                    self._store_failed(exc, report_via_session=False)
                    error = self._fault_error(Component.EVIDENCE, ErrorCode.STORAGE_ERROR)
                    if error is not None:
                        self._info = info = info.model_copy(update={"last_error": error})
        self._bus.publish(SessionStateMsg(session=info), self.session_id)
        return info

    def _fault_error(self, component: Component, code: ErrorCode) -> ApiErrorBody | None:
        fault = self.faults.snapshot(component)
        if fault is None:
            return None
        return ApiErrorBody(
            code=code,
            message=f"{component.value}: {fault.code}: {fault.message}"[:1000],
            retryable=True,
            details={"component": component.value, "errors": fault.count},
        )

    # ---------------------------------------------------- pipeline faults
    def _store_failed(self, exc: BaseException, *, report_via_session: bool = True) -> None:
        """Store write failed: count, log (rate-limited), make visible. Never writes to the store again here."""
        self.counters["store_errors"] += 1
        if self.faults.fail(Component.EVIDENCE, "store_write_failed", exc, self.clock.now_ms()):
            log.error("evidence store write failed (%s errors so far): %s", self.counters["store_errors"], exc)
            self._report_fault(Component.EVIDENCE, ErrorCode.STORAGE_ERROR, report_via_session)

    def _store_ok(self) -> None:
        if self.faults.ok(Component.EVIDENCE):
            self._inject_health(Health(component=Component.EVIDENCE, status=HealthStatus.OK, code="store_recovered"))
            self._publish_health_report()

    def _engine_failed(self, exc: BaseException) -> None:
        self.counters["fusion_errors"] += 1
        if self.faults.fail(Component.FUSION, "fusion_error", exc, self.clock.now_ms()):
            log.exception("incident engine error (%s so far)", self.counters["fusion_errors"])
            # The engine itself is failing: do not feed it a health observation (no feedback loop).
            self._report_fault(Component.FUSION, ErrorCode.INTERNAL, True, inject=False)

    def _engine_ok(self) -> None:
        if self.faults.ok(Component.FUSION):
            self._inject_health(Health(component=Component.FUSION, status=HealthStatus.OK, code="fusion_recovered"))
            self._publish_health_report()

    def _analyzer_failed(self, component: Component, exc: BaseException) -> None:
        self.counters["analyzer_errors"] += 1
        if self.faults.fail(component, "analyzer_error", exc, self.clock.now_ms()):
            log.exception("%s analyzer error (%s so far)", component.value, self.counters["analyzer_errors"])
            self._report_fault(component, ErrorCode.INTERNAL, True)

    def _report_fault(self, component: Component, code: ErrorCode, via_session: bool, *, inject: bool = True) -> None:
        fault = self.faults.snapshot(component)
        if fault is None:
            return
        if inject:  # becomes a monitoring_degraded episode / coverage gap in fusion
            self._inject_health(
                Health(
                    component=component,
                    status=HealthStatus.DEGRADED,
                    code=fault.code,
                    message=fault.message,
                    since_t_session_ms=fault.first_t_ms,
                    details={"errors": fault.count},
                )
            )
        if via_session:  # visible in GET /sessions/{id} and session_state on the stream
            self._update(last_error=self._fault_error(component, code))
        self._publish_health_report()

    def _inject_health(self, health: Health) -> None:
        t = self.clock.now_ms()
        obs = HealthObservation(
            observation_id=f"health-{self.session_id}-{uuid4().hex[:12]}",
            session_id=self.session_id,
            frame_id=None,
            t_session_ms=t,
            wall_time=self.clock.wall_at(t),
            source_mode=self.mode,
            producer=Producer(module="backend", version=BACKEND_VERSION),
            status=ObservationStatus.OK if health.status == HealthStatus.OK else ObservationStatus.DEGRADED,
            health=health,
        )
        self.publish_observation(obs)

    def _publish_health_report(self) -> None:
        reporter = self.health_reporter
        if reporter is None:
            return
        try:
            self._bus.publish(HealthMsg(report=reporter()), self.session_id)
        except Exception:
            log.exception("health report failed")

    def _poll_analyzer_health(self) -> None:
        """Analyzer health changes during a running session become HealthObservations (A05 A01-4)."""
        for part, component in ((self.pipeline.phone, Component.PHONE), (self.pipeline.attention, Component.ATTENTION)):
            analyzer = part.impl
            if analyzer is None:
                continue
            try:
                health = analyzer.health()
            except Exception as exc:
                health = Health(component=component, status=HealthStatus.ERROR, code="health_error", message=str(exc)[:300])
            previous = self._analyzer_status.get(component)
            self._analyzer_status[component] = health.status
            if previous is not None and previous != health.status:
                self._inject_health(health)
                self._publish_health_report()

    def _require(self, action: str, *allowed: SessionState) -> None:
        if self._info.state not in allowed:
            raise InvalidStateError(
                ErrorCode.INVALID_STATE,
                f"cannot {action}: session is {self._info.state.value}",
                state=self._info.state.value,
                action=action,
            )

    # ------------------------------------------------------------- preflight
    def preflight(self) -> PreflightReport:
        with self._lock:
            self._require("preflight", SessionState.CREATED, SessionState.PREFLIGHT)
            camera_check = self._open_capture()
            checks = [
                PreflightCheck(
                    check_id=PreflightCheckId.BACKEND,
                    status=CheckStatus.PASS,
                    required=True,
                    message_code="backend_ok",
                    message_ru="Локальный сервис работает",
                    details={"backend_version": BACKEND_VERSION},
                ),
                camera_check,
                self._lighting_check(),
                self._component_check(PreflightCheckId.PHONE_MODEL, self.pipeline.phone),
                self._component_check(PreflightCheckId.FACE_MODEL, self.pipeline.attention),
                self._engine_check(),
                self._component_check(PreflightCheckId.STORAGE, self.pipeline.store),
                self._environment_check(),
                PreflightCheck(
                    check_id=PreflightCheckId.OFFLINE_ASSETS,
                    status=CheckStatus.NOT_RUN,
                    required=False,
                    message_code="offline_check_not_integrated",
                    message_ru="Проверка офлайн-ресурсов ещё не подключена (A09)",
                ),
            ]
            report = PreflightReport(
                session_id=self.session_id,
                source_mode=self.mode,
                checks=checks,
                ready=all(c.status == CheckStatus.PASS for c in checks if c.required),
                created_at=utc_now(),
            )
            self._preflight = report
            self._update(state=SessionState.PREFLIGHT)
            return report

    def _open_capture(self) -> PreflightCheck:
        cap = self.pipeline.capture
        if cap.impl is None:
            return PreflightCheck(
                check_id=PreflightCheckId.CAMERA,
                status=CheckStatus.FAIL,
                required=True,
                message_code=cap.health.code,
                message_ru=f"Источник кадров недоступен: {cap.health.message}",
            )
        capture: CaptureService = cap.impl
        if not self._capture_open:
            for comp in (self.pipeline.phone, self.pipeline.attention):
                analyzer: FrameAnalyzer | None = comp.impl
                if analyzer is None:
                    continue
                analyzer.start_session(self.session_id, self.mode)
                name = f"{analyzer.name}"
                capture.add_consumer(name, self._consumer(analyzer), max_fps=self._max_fps(analyzer.name))
                self._consumers.append(name)
            capture.set_health_listener(self._on_capture_health)
            try:
                capture.open(self.session_id, self._info.source, self.clock)
            except ProctorError as exc:
                self._release_capture()
                return PreflightCheck(
                    check_id=PreflightCheckId.CAMERA,
                    status=CheckStatus.FAIL,
                    required=True,
                    message_code=exc.code.value.lower(),
                    message_ru=f"Источник кадров не открыт: {exc.message}",
                )
            self._capture_open = True
        deadline = time.monotonic() + FIRST_FRAME_TIMEOUT_S
        while time.monotonic() < deadline and capture.metrics().frames_captured == 0:
            time.sleep(0.05)
        frames = capture.metrics().frames_captured
        label = {SourceMode.LIVE: "Камера", SourceMode.REPLAY: "REPLAY-запись", SourceMode.SYNTHETIC: "СИНТЕТИЧЕСКИЙ источник (не камера)"}[self.mode]
        if frames == 0:
            return PreflightCheck(
                check_id=PreflightCheckId.CAMERA,
                status=CheckStatus.FAIL,
                required=True,
                message_code="no_frames",
                message_ru=f"{label}: нет кадров за {FIRST_FRAME_TIMEOUT_S:.0f} с",
            )
        return PreflightCheck(
            check_id=PreflightCheckId.CAMERA,
            status=CheckStatus.PASS,
            required=True,
            message_code=f"{self.mode.value}_source_ok",
            message_ru=f"{label}: кадры поступают",
            details={"frames": frames, "impl": cap.label},
        )

    def _lighting_check(self) -> PreflightCheck:
        capture: CaptureService | None = self.pipeline.capture.impl
        preview = capture.latest_preview() if (capture is not None and self._capture_open) else None
        frame: FramePacket | None = capture.get_frame(preview[0].frame_id) if preview else None
        if frame is None:
            return PreflightCheck(
                check_id=PreflightCheckId.LIGHTING,
                status=CheckStatus.NOT_RUN,
                required=False,
                message_code="no_frame_for_lighting",
                message_ru="Освещённость не оценена: нет кадра",
            )
        mean = float(np.asarray(frame.image, dtype=np.float32).mean())
        status = CheckStatus.PASS if 50.0 <= mean <= 210.0 else CheckStatus.WARN
        return PreflightCheck(
            check_id=PreflightCheckId.LIGHTING,
            status=status,
            required=False,
            message_code="mean_brightness_ok" if status == CheckStatus.PASS else "mean_brightness_out_of_range",
            message_ru="Средняя яркость кадра в норме" if status == CheckStatus.PASS else "Кадр слишком тёмный или пересвеченный",
            details={"mean_brightness": round(mean, 1), "method": "mean_pixel_value_0_255"},
        )

    def _component_check(self, check_id: PreflightCheckId, comp: PipelinePart) -> PreflightCheck:
        h = comp.health
        if comp.impl is None:
            status = CheckStatus.FAIL
        elif comp.label == "bootstrap":
            status = CheckStatus.WARN  # allowed only in synthetic sessions (not required there)
        elif h.status == HealthStatus.OK:
            status = CheckStatus.PASS
        elif h.status == HealthStatus.DEGRADED:
            status = CheckStatus.WARN
        else:
            status = CheckStatus.FAIL
        return PreflightCheck(
            check_id=check_id,
            status=status,
            required=self.mode != SourceMode.SYNTHETIC,
            message_code=h.code,
            message_ru=h.message or h.code,
            details={"impl": comp.label},
        )

    def _engine_check(self) -> PreflightCheck:
        p = self.pipeline
        if p.engine_factory is None:
            status = CheckStatus.FAIL
        elif p.engine_label == "bootstrap":
            status = CheckStatus.WARN
        else:
            status = CheckStatus.PASS
        return PreflightCheck(
            check_id=PreflightCheckId.FUSION,
            status=status,
            required=self.mode != SourceMode.SYNTHETIC,
            message_code=p.engine_health.code,
            message_ru=p.engine_health.message or p.engine_health.code,
            details={"impl": p.engine_label},
        )

    def _environment_check(self) -> PreflightCheck:
        caps = self._provider.environment_capabilities()
        required = self.mode == SourceMode.LIVE
        if caps is None:
            return PreflightCheck(
                check_id=PreflightCheckId.ENVIRONMENT_PROTECTION,
                status=CheckStatus.FAIL if required else CheckStatus.NOT_RUN,
                required=required,
                message_code="shell_not_reported",
                message_ru="Оболочка экзамена не сообщила возможности защиты среды",
            )
        counts: dict[str, int] = {}
        for item in caps.items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        ok = caps.exam_mode_supported and counts.get("blocked", 0) > 0
        status = CheckStatus.WARN if ok and len(counts) > 1 else (CheckStatus.PASS if ok else CheckStatus.FAIL)
        if required and status == CheckStatus.WARN:
            status = CheckStatus.PASS  # partial protection is visible in details, not hidden
        return PreflightCheck(
            check_id=PreflightCheckId.ENVIRONMENT_PROTECTION,
            status=status,
            required=required,
            message_code="capabilities_reported",
            message_ru="Возможности защиты среды получены от оболочки",
            details={**{f"count_{k}": v for k, v in counts.items()}, "platform": caps.platform},
        )

    def _max_fps(self, name: str) -> float | None:
        return {"phone": self.settings.phone_max_fps, "attention": self.settings.attention_max_fps}.get(name)

    # ------------------------------------------------------- observation path
    def _consumer(self, analyzer: FrameAnalyzer) -> Callable[[FramePacket], None]:
        component = Component.PHONE if analyzer.name == "phone" else Component.ATTENTION

        def on_frame(frame: FramePacket) -> None:
            if frame.session_id != self.session_id:
                self.counters["session_mismatch"] += 1
                return
            try:
                observations = analyzer.process(frame)
            except Exception as exc:
                self._analyzer_failed(component, exc)
                raise  # capture still counts it in ConsumerMetrics.errors
            if self.faults.ok(component):
                self._inject_health(Health(component=component, status=HealthStatus.OK, code="analyzer_recovered"))
                self._publish_health_report()
            for obs in observations:
                self.publish_observation(obs)

        return on_frame

    def publish_observation(self, obs: Observation) -> None:
        """Thread-safe entry for every observation of this session."""
        if obs.session_id != self.session_id:
            self.counters["session_mismatch"] += 1
            return
        self._bus.publish(ObservationMsg(observation=obs), self.session_id)
        if not self._accepting:
            return
        try:
            self._queue.put_nowait(obs)
        except queue.Full:
            self.counters["fusion_dropped"] += 1
            if self.faults.fail(Component.FUSION, "fusion_queue_overflow", RuntimeError("fusion queue full"), obs.t_session_ms):
                log.error("fusion queue full: %s observations dropped", self.counters["fusion_dropped"])
                self._publish_health_report()

    def _on_capture_health(self, health: Health) -> None:
        t = self.clock.now_ms()
        obs = HealthObservation(
            observation_id=f"health-{self.session_id}-{uuid4().hex[:12]}",
            session_id=self.session_id,
            frame_id=None,
            t_session_ms=t,
            wall_time=self.clock.wall_at(t),
            source_mode=self.mode,
            producer=Producer(module="capture", version=BACKEND_VERSION),
            status=ObservationStatus.OK if health.status == HealthStatus.OK else ObservationStatus.DEGRADED,
            health=health,
        )
        self.publish_observation(obs)

    # ------------------------------------------------------------ calibration
    def _attention(self) -> AttentionAnalyzer:
        att = self.pipeline.attention.impl
        if att is None:
            raise ProctorError(ErrorCode.MODULE_NOT_INTEGRATED, "attention module is not available")
        return att

    def _calibration_changed(self, cal: CalibrationState, **changes: Any) -> CalibrationState:
        self._update(calibration=cal, **changes)
        self._bus.publish(CalibrationMsg(session_id=self.session_id, calibration=cal), self.session_id)
        return cal

    def _require_preflight_ready(self) -> None:
        if self._preflight is None or not self._preflight.ready:
            raise InvalidStateError(ErrorCode.PREFLIGHT_FAILED, "preflight has not passed all required checks")

    def calibration_start(self) -> CalibrationState:
        with self._lock:
            self._require("start calibration", SessionState.PREFLIGHT, SessionState.CALIBRATING, SessionState.READY)
            self._require_preflight_ready()
            return self._calibration_changed(self._attention().calibration_start(), state=SessionState.CALIBRATING)

    def calibration_target(self, target: CalibrationTarget) -> CalibrationState:
        with self._lock:
            self._require("select calibration target", SessionState.CALIBRATING)
            return self._calibration_changed(self._attention().calibration_target(target))

    def calibration_state(self) -> CalibrationState:
        att = self.pipeline.attention.impl
        return att.calibration_state() if att is not None else self._info.calibration

    def calibration_finish(self) -> CalibrationState:
        with self._lock:
            self._require("finish calibration", SessionState.CALIBRATING)
            cal = self._attention().calibration_finish()
            state = SessionState.READY if cal.phase == CalibrationPhase.COMPLETED else SessionState.CALIBRATING
            return self._calibration_changed(cal, state=state)

    def calibration_cancel(self) -> CalibrationState:
        with self._lock:
            self._require("cancel calibration", SessionState.CALIBRATING)
            return self._calibration_changed(self._attention().calibration_cancel(), state=SessionState.PREFLIGHT)

    def calibration_skip(self, reason: str) -> CalibrationState:
        with self._lock:
            self._require("skip calibration", SessionState.PREFLIGHT, SessionState.CALIBRATING)
            self._require_preflight_ready()
            log.info("calibration skipped: %s", reason)
            return self._calibration_changed(self._attention().calibration_skip(reason), state=SessionState.READY)

    # -------------------------------------------------------------- exam run
    def start(self) -> SessionInfo:
        with self._lock:
            self._require("start", SessionState.READY)
            self._require_preflight_ready()
            if self._info.calibration.phase not in (CalibrationPhase.COMPLETED, CalibrationPhase.SKIPPED):
                raise InvalidStateError(ErrorCode.INVALID_STATE, "calibration must be completed or explicitly skipped")
            if self.pipeline.engine_factory is None:
                raise ProctorError(ErrorCode.MODULE_NOT_INTEGRATED, "incident engine is not available")
            self._engine = self.pipeline.engine_factory(self.session_id, self.mode)
            self._poll_analyzer_health()  # baseline; only later changes are reported
            self._record_session_config()
            self._fusion_thread = threading.Thread(target=self._fusion_loop, name=f"fusion-{self.session_id}", daemon=True)
            self._fusion_thread.start()
            self._accepting = True
            t = self.clock.now_ms()
            return self._update(state=SessionState.RUNNING, started_at=utc_now(), exam_started_t_ms=t)

    def _record_session_config(self) -> None:
        """Hand loaded model manifests + the engine's effective thresholds to the store (A08 #2, A05 A01-2).
        Optional store method; a failure is a visible store fault, never a reason to block the exam."""
        store = self.pipeline.store.impl
        record = getattr(store, "record_session_config", None)
        if record is None or self._engine is None:
            return
        models = []
        for part in (self.pipeline.phone, self.pipeline.attention):
            analyzer = part.impl
            manifests = getattr(analyzer, "model_manifests", None)
            if callable(manifests):
                models.extend(m for m in manifests() if m is not None)
            elif getattr(analyzer, "manifest", None) is not None:
                models.append(analyzer.manifest)
        try:
            config = self._engine.config_snapshot()
        except Exception as exc:
            self._engine_failed(exc)
            config = {}
        try:
            record(self.session_id, models, config)
        except Exception as exc:
            self._store_failed(exc)

    def pause(self, body: PauseRequest) -> SessionInfo:
        with self._lock:
            self._require("pause", SessionState.RUNNING)
            t = self.clock.now_ms()
            self._accepting = False
            self._control(_Control("pause", t))
            self._pause_started_ms = t
            log.info("session %s paused: %s", self.session_id, body.reason)
            return self._update(state=SessionState.PAUSED)

    def resume(self) -> SessionInfo:
        with self._lock:
            self._require("resume", SessionState.PAUSED)
            t = self.clock.now_ms()
            paused = t - (self._pause_started_ms if self._pause_started_ms is not None else t)
            self._pause_started_ms = None
            self._control(_Control("resume", t))
            self._accepting = True
            return self._update(state=SessionState.RUNNING, paused_total_ms=self._info.paused_total_ms + paused)

    def finish(self) -> SessionInfo:
        return self._end(SessionState.FINISHED, IncidentEndReason.SESSION_FINISHED)

    def abort(self, body: AbortRequest) -> SessionInfo:
        log.warning("session %s aborted: %s", self.session_id, body.reason)
        return self._end(SessionState.ABORTED, IncidentEndReason.SESSION_ABORTED)

    def _end(self, final: SessionState, reason: IncidentEndReason) -> SessionInfo:
        with self._lock:
            if self._info.state in TERMINAL_STATES:
                if self._info.state == final:
                    return self._info  # idempotent
                self._require("end", *ACTIVE_STATES)
            t = self.clock.now_ms()
            # 1) stop producing frames/observations (joins consumer threads)
            self._release_capture()
            # 2) drain queued observations, then close all open incidents
            self._accepting = False
            if self._fusion_thread is not None:
                ctl = _Control("finish", t, reason)
                self._control(ctl)
                if not ctl.done.wait(10.0):
                    log.error("fusion thread did not finish in time")
                self._fusion_thread.join(2.0)
                self._fusion_thread = None
            for comp in (self.pipeline.phone, self.pipeline.attention):
                if comp.impl is not None:
                    try:
                        comp.impl.end_session()
                    except Exception:
                        log.exception("end_session failed")
            paused_extra = (t - self._pause_started_ms) if self._pause_started_ms is not None else 0.0
            self._pause_started_ms = None
            return self._update(
                state=final,
                finished_at=utc_now(),
                paused_total_ms=self._info.paused_total_ms + paused_extra,
            )

    def _release_capture(self) -> None:
        capture: CaptureService | None = self.pipeline.capture.impl
        if capture is None:
            return
        # Detach first (A02 R7): the deliberate close must not become a "monitoring_degraded" gap.
        capture.set_health_listener(None)
        if self._capture_open:
            capture.close()
            self._capture_open = False
        for name in self._consumers:
            try:
                capture.remove_consumer(name)
            except Exception:
                log.exception("remove_consumer failed")
        self._consumers.clear()
        capture.set_health_listener(None)

    # ---------------------------------------------------------------- fusion
    def _control(self, ctl: _Control) -> None:
        if self._fusion_thread is None:
            ctl.done.set()
            return
        self._queue.put(ctl, timeout=5.0)

    def _timeline_now(self) -> float:
        if self.mode == SourceMode.REPLAY:
            return self._timeline_max_ms
        return max(self.clock.now_ms(), self._timeline_max_ms)

    def _fusion_loop(self) -> None:
        engine = self._engine
        assert engine is not None
        tick_s = self.settings.fusion_tick_ms / 1000.0
        next_tick = time.monotonic() + tick_s
        while True:
            timeout = max(0.0, next_tick - time.monotonic())
            try:
                item: Observation | _Control | None = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = None
            changes: list[IncidentChange] = []
            if isinstance(item, _Control):
                try:
                    if item.kind == "finish":
                        changes = engine.finish(item.t_ms, item.reason or IncidentEndReason.SESSION_FINISHED)
                    else:
                        changes = engine.set_paused(item.kind == "pause", item.t_ms)
                    self._engine_ok()
                except Exception as exc:
                    self._engine_failed(exc)
                self._emit(changes)
                item.done.set()
                if item.kind == "finish":
                    return
                continue
            if item is not None:
                self._timeline_max_ms = max(self._timeline_max_ms, item.t_session_ms)
                # QA-BUG-004: the engine sees every observation even when the store is failing.
                try:
                    changes = engine.consume(item)
                    self._engine_ok()
                except Exception as exc:
                    self._engine_failed(exc)
                store = self.pipeline.store.impl
                if store is not None:
                    try:
                        store.record_observation(item)
                        self._store_ok()
                    except Exception as exc:
                        self._store_failed(exc)
            if time.monotonic() >= next_tick:
                next_tick = time.monotonic() + tick_s
                try:
                    changes = changes + engine.advance(self._timeline_now())
                except Exception as exc:
                    self._engine_failed(exc)
                self._poll_analyzer_health()
            self._emit(changes)

    def _emit(self, changes: list[IncidentChange]) -> None:
        store: EvidenceStore | None = self.pipeline.store.impl
        capture: CaptureService | None = self.pipeline.capture.impl
        for change in changes:
            if change.incident.session_id != self.session_id:
                self.counters["session_mismatch"] += 1
                continue
            if store is not None:
                try:
                    store.record_incident_change(change)
                    self._store_ok()
                except Exception as exc:
                    self._store_failed(exc)
                frame_id = change.incident.trigger_frame_id
                if (
                    change.change == IncidentChangeType.OPENED
                    and self._info.retain_media
                    and frame_id is not None
                    and capture is not None
                ):
                    try:
                        frame = capture.get_frame(frame_id)
                        if frame is not None and frame.session_id == self.session_id:
                            store.capture_snapshot(self.session_id, change.incident.incident_id, frame)
                    except Exception as exc:
                        self._store_failed(exc)
            self._bus.publish(IncidentMsg(change=change), self.session_id)

    # ----------------------------------------------------------- environment
    def environment_events(self, batch: EnvironmentEventBatch) -> EnvironmentEventAck:
        if batch.session_id != self.session_id:
            raise ProctorError(ErrorCode.SESSION_MISMATCH, "batch.session_id does not match the URL")
        with self._lock:
            if self._info.state not in ACTIVE_STATES:
                raise InvalidStateError(ErrorCode.INVALID_STATE, "session is not active")
            accepted, dups, ids = 0, 0, []
            for ev in batch.events:
                if ev.client_seq in self._env_seqs:
                    dups += 1
                    continue
                self._env_seqs.add(ev.client_seq)
                t = self.clock.now_ms()
                obs = EnvironmentObservation(
                    observation_id=f"env-{self.session_id}-{ev.client_seq}",
                    session_id=self.session_id,
                    frame_id=None,
                    t_session_ms=t,
                    wall_time=self.clock.wall_at(t),
                    source_mode=self.mode,
                    producer=Producer(module="environment", version=BACKEND_VERSION),
                    status=ObservationStatus.OK,
                    action=ev.action,
                    enforcement=ev.enforcement,
                    mechanism=ev.mechanism,
                    scope=ev.scope,
                    client_seq=ev.client_seq,
                    client_wall_time=ev.client_wall_time,
                    detail=ev.detail,
                )
                self.publish_observation(obs)
                accepted += 1
                ids.append(obs.observation_id)
            return EnvironmentEventAck(accepted=accepted, duplicates=dups, observation_ids=ids)

    # --------------------------------------------------------------- runtime
    def metrics(self) -> RuntimeMetrics:
        capture: CaptureService | None = self.pipeline.capture.impl
        if capture is None or not self._capture_open:
            return RuntimeMetrics(session_id=self.session_id, window_s=1.0, capture_fps=0.0, frames_captured=0, frames_dropped=0)
        m = capture.metrics()
        return m.model_copy(update={"session_id": self.session_id, "t_session_ms": self.clock.now_ms()})

    def preview(self) -> tuple[PreviewFrameMeta, bytes] | None:
        capture: CaptureService | None = self.pipeline.capture.impl
        if capture is None or not self._capture_open:
            return None
        latest = capture.latest_preview()
        if latest is None or latest[0].session_id != self.session_id:
            return None
        return latest


class SessionManager:
    def __init__(self, settings: Settings, provider: PipelineProvider, broadcaster: Broadcaster, exam: ExamDefinition):
        self.settings = settings
        self._provider = provider
        self._bus = broadcaster
        self.exam = exam
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRuntime] = {}
        self.health_reporter: Callable[[], Any] | None = None

    def create(self, req: SessionCreate) -> SessionInfo:
        if not req.consent.accepted:
            raise ProctorError(ErrorCode.INVALID_ARGUMENT, "informed consent must be accepted before a session")
        if req.exam_id != self.exam.exam_id:
            raise ProctorError(ErrorCode.INVALID_ARGUMENT, f"unknown exam_id {req.exam_id!r}")
        with self._lock:
            active = self.active_session_id()
            if active is not None:
                raise InvalidStateError(
                    ErrorCode.SESSION_ACTIVE,
                    "another session is active; finish or abort it first",
                    active_session_id=active,
                )
            session_id = f"s-{time.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
            clock = SessionClock()
            info = SessionInfo(
                session_id=session_id,
                state=SessionState.CREATED,
                source=req.source,
                source_mode=req.source.mode,
                exam_id=req.exam_id,
                student_label=req.student_label,
                retain_media=req.retain_media,
                created_at=clock.origin_wall,
                calibration=CalibrationState(phase=CalibrationPhase.NOT_STARTED, updated_at=clock.origin_wall),
                backend_version=BACKEND_VERSION,
            )
            pipeline = self._provider.pipeline_for(req.source.mode)
            runtime = SessionRuntime(self.settings, info, clock, pipeline, self._bus, self._provider)
            runtime.health_reporter = self.health_reporter
            self._sessions[session_id] = runtime
        runtime._update()  # persist + broadcast "created"
        return runtime.info

    def get(self, session_id: str) -> SessionInfo | None:
        rt = self._sessions.get(session_id)
        return rt.info if rt is not None else None

    def runtime(self, session_id: str) -> SessionRuntime:
        rt = self._sessions.get(session_id)
        if rt is None:
            raise NotFoundError(ErrorCode.SESSION_NOT_FOUND, f"session {session_id} not found")
        return rt

    def active_session_id(self) -> str | None:
        for sid, rt in list(self._sessions.items()):
            if rt.state in ACTIVE_STATES:
                return sid
        return None

    def forget(self, session_id: str) -> None:
        """Drop a TERMINAL runtime after its data was deleted (QA-OBS-004 / A08 #3)."""
        with self._lock:
            rt = self._sessions.get(session_id)
            if rt is not None and rt.state in ACTIVE_STATES:
                raise InvalidStateError(ErrorCode.SESSION_ACTIVE, "finish or abort the session before deleting it")
            self._sessions.pop(session_id, None)

    def active_runtime(self) -> SessionRuntime | None:
        sid = self.active_session_id()
        return self._sessions.get(sid) if sid else None

    def shutdown(self) -> None:
        rt = self.active_runtime()
        if rt is not None:
            try:
                rt.abort(AbortRequest(reason="backend_shutdown"))
            except Exception:
                log.exception("abort on shutdown failed")
