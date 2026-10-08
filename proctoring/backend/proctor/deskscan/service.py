"""Desk (workplace) scan before the exam (owner: A15, contract 1.2).

The student tilts/turns the laptop (or sweeps a USB camera over the desk) for ``duration_s`` seconds.
Frames come ONLY from the session's CaptureService (A02) through a temporary consumer "deskscan";
this module never opens a camera. Each frame (about 4 fps) goes through a dedicated instance of the
A03 phone analyzer built with the public factory ``create_phone_analyzer`` (same YOLO11n weights,
classes "cell phone", "book", "laptop", "tv"); no new detector. A dedicated instance keeps the
session analyzer's tracker/frame order untouched.

Result: CLEAR or OBJECTS_FOUND (per class: max confidence, seen time; a class counts only when it
was seen with confidence >= 0.5 for >= 0.5 s in a row). Any camera/model problem gives FAILED with a
message: an unknown result is never reported as "clear". The scan helps the teacher; it is NOT
evidence of a violation. The clip is kept only when the session retains media.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from proctor_contracts.interfaces import FramePacket, InvalidStateError, ProctorError
from proctor_contracts.v1 import (
    CheckStatus,
    DeskScanObject,
    DeskScanRequest,
    DeskScanResult,
    DeskScanState,
    ErrorCode,
    HealthStatus,
    PreflightCheck,
    PreflightCheckId,
    SessionState,
    SourceMode,
)

log = logging.getLogger("proctor.deskscan")

CONSUMER_NAME = "deskscan"
SCAN_FPS = 4.0  # analysis rate (the capture worker drops the rest)
MIN_CONFIDENCE = 0.5
MIN_STABLE_MS = 500.0  # longest continuous run of one class must last at least this long
RUN_GAP_MS = 700.0  # one or two missed frames while the camera moves do not break a run
MIN_FRAME_SHARE = 0.4  # fewer analysed frames than 40 % of the expected count -> FAILED
MIN_FRAMES = 3
CLIP_MAX_BEFORE_S = 9.5  # A02 clip ring is 10 s: the clip covers the last <= 9.5 s of the scan
ALLOWED_STATES = (SessionState.PREFLIGHT, SessionState.CALIBRATING, SessionState.READY)
FIXED_CAMERA_REASON = "fixed_camera_teacher_check"

LABELS_RU: dict[str, str] = {
    "cell phone": "телефон",
    "book": "книга",
    "laptop": "ноутбук",
    "tv": "второй экран",
}
MODES_RU: dict[str, str] = {"laptop": "ноутбук", "usb": "USB-камера", "fixed": "камера не двигается"}


# --------------------------------------------------------------------------------------- aggregation
@dataclass
class _ClassTrack:
    max_confidence: float = 0.0
    seen_ms: float = 0.0  # sum of continuous runs (time between first and last detection of each run)
    longest_run_ms: float = 0.0
    run_start: float | None = None
    last_seen: float | None = None

    def add(self, t_ms: float, confidence: float) -> None:
        self.max_confidence = max(self.max_confidence, confidence)
        if self.last_seen is None or t_ms - self.last_seen > RUN_GAP_MS:
            self._close_run()
            self.run_start = t_ms
        self.last_seen = t_ms

    def _close_run(self) -> None:
        if self.run_start is not None and self.last_seen is not None:
            span = max(0.0, self.last_seen - self.run_start)
            self.seen_ms += span
            self.longest_run_ms = max(self.longest_run_ms, span)
        self.run_start = self.last_seen = None

    def finish(self) -> None:
        self._close_run()


@dataclass
class DeskScanAggregator:
    """Per-class aggregation of detections over the scan (pure; unit-tested without a model)."""

    classes: tuple[str, ...] = tuple(LABELS_RU)
    min_confidence: float = MIN_CONFIDENCE
    min_stable_ms: float = MIN_STABLE_MS
    frames_ok: int = 0
    frames_error: int = 0
    tracks: dict[str, _ClassTrack] = field(default_factory=dict)

    def add_frame(self, t_ms: float, detections: Iterable[tuple[str, float]], *, ok: bool = True) -> None:
        if not ok:
            self.frames_error += 1
            return
        self.frames_ok += 1
        best: dict[str, float] = {}
        for name, conf in detections:
            if name in self.classes and conf >= self.min_confidence:
                best[name] = max(best.get(name, 0.0), float(conf))
        for name, conf in best.items():
            self.tracks.setdefault(name, _ClassTrack()).add(t_ms, conf)

    def objects(self) -> list[DeskScanObject]:
        found = []
        for name in self.classes:
            tr = self.tracks.get(name)
            if tr is None:
                continue
            tr.finish()
            if tr.longest_run_ms >= self.min_stable_ms:
                found.append(
                    DeskScanObject(
                        class_name=name,
                        label_ru=LABELS_RU.get(name, name),
                        max_confidence=round(min(1.0, tr.max_confidence), 3),
                        seen_ms=round(tr.seen_ms, 1),
                    )
                )
        return found


def objects_text_ru(objects: list[DeskScanObject]) -> str:
    return ", ".join(o.label_ru for o in objects)


def pronoun_ru(objects: list[DeskScanObject]) -> str:
    """«уберите его / её / их» (книга — женский род)."""
    if len(objects) != 1:
        return "их"
    return "её" if objects[0].class_name == "book" else "его"


def outcome_ru(state: DeskScanState, objects: list[DeskScanObject], problem: str | None = None) -> str:
    if state == DeskScanState.CLEAR:
        return "Стол осмотрен: посторонних предметов не замечено."
    if state == DeskScanState.OBJECTS_FOUND:
        return f"Замечено: {objects_text_ru(objects)} — уберите {pronoun_ru(objects)} и повторите осмотр."
    if state == DeskScanState.FAILED:
        return f"Осмотр не выполнен: {problem or 'неизвестная ошибка'}. Это не означает «чисто»."
    return problem or ""


def message_ru(mode: str, text: str) -> str:
    return f"Вариант: {MODES_RU.get(mode, mode)}. {text}".strip()[:300]


def preflight_check(result: DeskScanResult | None) -> PreflightCheck:
    """PreflightCheckId.DESK_SCAN, required=false: PASS clear, WARN objects/failed, NOT_RUN otherwise."""
    state = result.state if result is not None else DeskScanState.NOT_STARTED
    base = dict(check_id=PreflightCheckId.DESK_SCAN, required=False)
    if state == DeskScanState.CLEAR:
        return PreflightCheck(**base, status=CheckStatus.PASS, message_code="desk_scan_clear",
                              message_ru="Стол осмотрен: посторонних предметов не замечено")
    if state == DeskScanState.OBJECTS_FOUND:
        objs = result.objects if result is not None else []
        return PreflightCheck(**base, status=CheckStatus.WARN, message_code="desk_scan_objects_found",
                              message_ru=f"На столе замечено: {objects_text_ru(objs)} — уберите {pronoun_ru(objs)}",
                              details={"objects": ",".join(o.class_name for o in objs)})
    if state == DeskScanState.FAILED:
        return PreflightCheck(**base, status=CheckStatus.WARN, message_code="desk_scan_failed",
                              message_ru=(result.message_ru if result else None) or "Осмотр рабочего места не удался")
    if state == DeskScanState.SKIPPED:
        return PreflightCheck(**base, status=CheckStatus.NOT_RUN, message_code="desk_scan_skipped",
                              message_ru=(result.message_ru if result else None) or "Осмотр рабочего места пропущен",
                              details={"skip_reason": (result.skip_reason or "") if result else ""})
    return PreflightCheck(**base, status=CheckStatus.NOT_RUN, message_code="desk_scan_not_run",
                          message_ru="Осмотр рабочего места не проводился")


# ------------------------------------------------------------------------------------------ service
@dataclass
class _Scan:
    session_id: str
    scan_id: str
    mode: str
    duration_s: float
    started_t_ms: float
    started_mono: float
    agg: DeskScanAggregator = field(default_factory=DeskScanAggregator)
    first_t: float | None = None
    last_t: float | None = None
    last_frame_id: int = -1
    done: threading.Event = field(default_factory=threading.Event)


class DeskScanService:
    """One scan at a time per backend (one active session at a time, see session.py)."""

    def __init__(
        self,
        analyzer_factory: Callable[[], Any] | None,
        *,
        clip_writer: Callable[..., tuple[bytes, str, float] | None] | None = None,
        scan_fps: float = SCAN_FPS,
    ):
        self._analyzer_factory = analyzer_factory
        self._clip_writer = clip_writer
        self._scan_fps = scan_fps
        self._lock = threading.Lock()
        self._analyzer: Any | None = None
        self._analyzer_problem: str | None = None
        self._analyzer_lock = threading.Lock()
        self._results: dict[str, DeskScanResult] = {}
        self._active: _Scan | None = None
        self._warming = False

    # ------------------------------------------------------------------ analyzer (public A03 factory)
    def _get_analyzer(self) -> tuple[Any | None, str | None]:
        with self._analyzer_lock:
            if self._analyzer is not None:
                return self._analyzer, None
            if self._analyzer_factory is None:
                return None, "модуль распознавания предметов (A03) не подключён"
            try:
                analyzer = self._analyzer_factory()
                if analyzer is None:
                    return None, "модуль распознавания предметов (A03) не подключён"
                health = analyzer.load()
            except Exception as exc:  # a broken model must not take the backend down
                log.exception("desk scan analyzer failed to load")
                return None, f"модель распознавания предметов не загрузилась ({type(exc).__name__})"
            if health.status not in (HealthStatus.OK, HealthStatus.DEGRADED):
                return None, f"модель распознавания предметов недоступна ({health.code})"
            self._analyzer = analyzer
            return analyzer, None

    def warm_up(self) -> None:
        """Load the analyzer in the background (once) so the first scan does not wait for it."""
        with self._lock:
            if self._warming or self._analyzer is not None:
                return
            self._warming = True
        threading.Thread(target=self._get_analyzer, name="deskscan-warmup", daemon=True).start()

    # ----------------------------------------------------------------------------------- queries
    def result(self, session_id: str, store: Any = None) -> DeskScanResult:
        with self._lock:
            res = self._results.get(session_id)
        if res is None and store is not None and hasattr(store, "desk_scan"):
            try:
                res = store.desk_scan(session_id)
            except Exception:
                log.exception("desk scan read from store failed")
                res = None
        return res if res is not None else DeskScanResult(state=DeskScanState.NOT_STARTED)

    def known_result(self, session_id: str) -> DeskScanResult | None:
        with self._lock:
            return self._results.get(session_id)

    # ------------------------------------------------------------------------------------- start
    def start(self, rt: Any, body: DeskScanRequest, mode: str, store: Any = None) -> DeskScanResult:
        if mode not in ("laptop", "usb"):
            raise ProctorError(ErrorCode.INVALID_ARGUMENT, "mode must be 'laptop' or 'usb'", parameter="mode")
        sid = rt.session_id
        with self._lock:
            self._require_state(rt, "start desk scan")
            if self._active is not None and not self._active.done.is_set():
                raise InvalidStateError(ErrorCode.INVALID_STATE, "a desk scan is already recording", state="recording")
            scan = _Scan(
                session_id=sid,
                scan_id=f"ds-{secrets.token_hex(8)}",
                mode=mode,
                duration_s=float(body.duration_s),
                started_t_ms=round(rt.clock.now_ms(), 1),
                started_mono=time.monotonic(),
            )
            self._active = scan
            recording = DeskScanResult(
                scan_id=scan.scan_id,
                state=DeskScanState.RECORDING,
                started_t_ms=scan.started_t_ms,
                message_ru=message_ru(mode, "Идёт осмотр рабочего места…"),
            )
            self._results[sid] = recording
        threading.Thread(target=self._run, args=(rt, scan, store), name=f"deskscan-{sid}", daemon=True).start()
        return recording

    def skip(self, rt: Any, reason: str, store: Any = None) -> DeskScanResult:
        """Operator/teacher action (PIN is enforced by the shell, like calibration skip)."""
        with self._lock:
            self._require_state(rt, "skip desk scan")
            if self._active is not None and not self._active.done.is_set() and self._active.session_id == rt.session_id:
                raise InvalidStateError(ErrorCode.INVALID_STATE, "a desk scan is recording; wait for it to finish", state="recording")
        if reason == FIXED_CAMERA_REASON:
            text = message_ru("fixed", "Осмотр камерой невозможен (стационарная камера) — подтверждён преподавателем.")
        else:
            text = f"Осмотр пропущен оператором. Причина: {reason}"[:300]
        result = DeskScanResult(
            scan_id=f"ds-{secrets.token_hex(8)}",
            state=DeskScanState.SKIPPED,
            started_t_ms=round(rt.clock.now_ms(), 1),
            duration_ms=0.0,
            message_ru=text,
            skip_reason=reason[:200],
        )
        return self._publish(rt.session_id, result, store, None)

    @staticmethod
    def _require_state(rt: Any, action: str) -> None:
        state = rt.state
        if state not in ALLOWED_STATES:
            raise InvalidStateError(
                ErrorCode.INVALID_STATE,
                f"cannot {action}: session is {state.value} (allowed before the exam only: preflight/calibrating/ready)",
                state=state.value,
                action=action,
            )

    # ----------------------------------------------------------------------------------- worker
    def _run(self, rt: Any, scan: _Scan, store: Any) -> None:
        try:
            result, clip = self._scan(rt, scan)
        except Exception as exc:  # never leave the result stuck in "recording"
            log.exception("desk scan failed")
            result, clip = self._failed(scan, f"внутренняя ошибка ({type(exc).__name__})"), None
        try:
            self._publish(rt.session_id, result, store, clip)
        finally:
            scan.done.set()

    def _failed(self, scan: _Scan, problem: str, duration_ms: float | None = None) -> DeskScanResult:
        return DeskScanResult(
            scan_id=scan.scan_id,
            state=DeskScanState.FAILED,
            started_t_ms=scan.started_t_ms,
            duration_ms=round(duration_ms if duration_ms is not None else (time.monotonic() - scan.started_mono) * 1000.0, 1),
            message_ru=message_ru(scan.mode, outcome_ru(DeskScanState.FAILED, [], problem)),
        )

    def _scan(self, rt: Any, scan: _Scan) -> tuple[DeskScanResult, Any]:
        capture = rt.pipeline.capture.impl
        if capture is None or not getattr(rt, "_capture_open", False):
            return self._failed(scan, "камера не открыта (сначала пройдите подготовку)", 0.0), None
        analyzer, problem = self._get_analyzer()
        if analyzer is None:
            return self._failed(scan, problem or "модель недоступна", 0.0), None
        # the scan window starts once the model is ready (first load may take a moment)
        scan.started_mono = time.monotonic()
        analyzer.start_session(scan.session_id, rt.mode)
        try:
            capture.add_consumer(CONSUMER_NAME, self._consumer(scan, analyzer), max_fps=self._scan_fps)
        except Exception as exc:
            analyzer.end_session()
            return self._failed(scan, f"кадры недоступны ({type(exc).__name__})", 0.0), None
        try:
            scan.done.wait(scan.duration_s)  # done is set only by _run: this waits the full duration
        finally:
            try:
                capture.remove_consumer(CONSUMER_NAME)  # waits until the callback has returned
            except Exception:
                log.exception("remove_consumer(deskscan) failed")
            analyzer.end_session()
        duration_ms = round((time.monotonic() - scan.started_mono) * 1000.0, 1)
        if rt.state not in ALLOWED_STATES and rt.state != SessionState.RUNNING:
            return self._failed(scan, f"сессия перешла в состояние {rt.state.value} во время осмотра", duration_ms), None
        agg = scan.agg
        expected = scan.duration_s * self._scan_fps
        if agg.frames_ok < max(MIN_FRAMES, MIN_FRAME_SHARE * expected):
            if agg.frames_ok == 0 and agg.frames_error == 0:
                problem = "камера не передала кадры за время осмотра"
            elif agg.frames_error > agg.frames_ok:
                problem = "ошибка анализа кадров (модель распознавания)"
            else:
                problem = f"слишком мало кадров ({agg.frames_ok} из ~{int(expected)})"
            return self._failed(scan, problem, duration_ms), None
        objects = agg.objects()
        state = DeskScanState.OBJECTS_FOUND if objects else DeskScanState.CLEAR
        text = outcome_ru(state, objects)
        if rt.mode == SourceMode.SYNTHETIC:
            text += " (СИНТЕТИЧЕСКИЙ источник, не камера.)"
        result = DeskScanResult(
            scan_id=scan.scan_id,
            state=state,
            started_t_ms=scan.started_t_ms,
            duration_ms=duration_ms,
            objects=objects[:16],
            message_ru=message_ru(scan.mode, text),
        )
        clip = None
        if rt.info.retain_media and self._clip_writer is not None and scan.last_t is not None:
            try:
                clip = self._clip_writer(capture, scan.last_t, min(scan.duration_s, CLIP_MAX_BEFORE_S), scan.scan_id)
            except Exception:
                log.exception("desk scan clip failed")
                clip = None
        return result, clip

    def _consumer(self, scan: _Scan, analyzer: Any) -> Callable[[FramePacket], None]:
        def on_frame(frame: FramePacket) -> None:
            if frame.session_id != scan.session_id or frame.frame_id <= scan.last_frame_id:
                return
            if time.monotonic() - scan.started_mono > scan.duration_s:
                return
            scan.last_frame_id = frame.frame_id
            t = float(frame.t_session_ms)
            try:
                observations = analyzer.process(frame)
            except Exception:
                scan.agg.add_frame(t, [], ok=False)
                return
            for obs in observations or []:
                status = getattr(obs, "status", None)
                ok = getattr(status, "value", status) != "error"
                dets = [(d.class_name, float(d.confidence)) for d in getattr(obs, "detections", [])]
                scan.agg.add_frame(t, dets, ok=ok)
            scan.first_t = t if scan.first_t is None else scan.first_t
            scan.last_t = t

        return on_frame

    # ----------------------------------------------------------------------------------- publish
    def _publish(self, session_id: str, result: DeskScanResult, store: Any, clip: Any) -> DeskScanResult:
        record = getattr(store, "record_desk_scan", None) if store is not None else None
        if callable(record):
            try:
                stored = record(session_id, result, clip)
                if isinstance(stored, DeskScanResult):
                    result = stored
            except Exception:
                log.exception("desk scan could not be stored")
        with self._lock:
            self._results[session_id] = result
        return result

    def close(self) -> None:
        analyzer, self._analyzer = self._analyzer, None
        if analyzer is not None and hasattr(analyzer, "close"):
            try:
                analyzer.close()
            except Exception:
                log.exception("desk scan analyzer close failed")
