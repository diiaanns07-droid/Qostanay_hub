"""Read/act on the local backend ONLY through public interfaces (owner: C2).

* sessions: ``SessionManager.active_session_id() / get() / runtime()`` and ``SessionRuntime.start() / finish()``;
* incidents + summary: the session's evidence store (A08 ``list_incidents`` / ``summary``);
* zone: A05 ``proctor.fusion.zones.assess_session_zone`` (coverage computed "until now", see ``_coverage_now``);
* preview: ``SessionRuntime.preview()`` (A02 JPEG) re-encoded to 320×240 ≤ 30 KB;
* clips: A02 ``capture.export_clip``.
Every method is synchronous and may block (SQLite, JPEG): the uplink calls them via ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("proctor.uplink")

PREVIEW_W, PREVIEW_H, PREVIEW_MAX_BYTES = 320, 240, 30_000
EXAM_STATE = {
    "created": "idle",
    "preflight": "preflight",
    "calibrating": "calibrating",
    "ready": "preflight",
    "running": "running",
    "paused": "paused",
    "finished": "finished",
    "aborted": "finished",
    "failed": "finished",
}
BUSY_CODES = {"camera_no_frames", "previous_capture_releasing", "capture_closing"}


@dataclass
class Snapshot:
    session_id: str | None = None
    exam_state: str = "idle"
    camera: str = "off"
    monitoring: str = "ok"
    zone: str | None = None
    zone_reasons_ru: list[str] = field(default_factory=list)
    incidents: list[dict[str, Any]] = field(default_factory=list)  # normalized, see _incident
    incidents_total: int = 0
    incidents_by_priority: dict[str, int] = field(default_factory=lambda: {"low": 0, "medium": 0, "high": 0})

    def status_fields(self) -> dict[str, Any]:
        return {
            "exam_state": self.exam_state,
            "camera": self.camera,
            "monitoring": self.monitoring,
            "zone": self.zone,
            "zone_reasons_ru": self.zone_reasons_ru[:3],
            "incidents_total": self.incidents_total,
            "incidents_by_priority": dict(self.incidents_by_priority),
        }


def _val(x: Any) -> Any:
    return getattr(x, "value", x)


def _incident(inc: Any) -> dict[str, Any]:
    return {
        "incident_id": inc.incident_id,
        "rule_id": _val(inc.rule_id),
        "category": _val(inc.category),
        "priority": _val(inc.priority),
        "state": _val(inc.state),
        "t_start_ms": float(inc.t_start_ms),
        "t_end_ms": None if inc.t_end_ms is None else float(inc.t_end_ms),
        "t_start_wall": inc.wall_start.isoformat(),
        "duration_ms": float(inc.duration_ms),
        "explanation_ru": (inc.explanation.summary_ru or "")[:1000],
    }


class BackendView:
    def __init__(self, manager_getter: Callable[[], Any]):
        self._manager = manager_getter
        self._last_sid: str | None = None  # keep reporting a session after it finished

    # ------------------------------------------------------------------ sessions
    def _runtime(self) -> Any | None:
        mgr = self._manager()
        if mgr is None:
            return None
        sid = mgr.active_session_id() or self._last_sid
        if sid is None:
            return None
        try:
            rt = mgr.runtime(sid)
        except Exception:
            self._last_sid = None
            return None
        self._last_sid = sid
        return rt

    def snapshot(self) -> Snapshot:
        rt = self._runtime()
        snap = Snapshot()
        if rt is None:
            return snap
        info = rt.info
        snap.session_id = info.session_id
        snap.exam_state = EXAM_STATE.get(_val(info.state), "idle")
        snap.camera, snap.monitoring = self._camera_and_monitoring(rt, snap.exam_state)
        store = getattr(rt.pipeline.store, "impl", None)
        incidents: list[Any] = []
        summary = None
        if store is not None and hasattr(store, "list_incidents"):
            try:
                incidents = list(store.list_incidents(info.session_id))
                summary = store.summary(info.session_id, info) if hasattr(store, "summary") else None
            except Exception as exc:  # a store error must never break the uplink
                log.warning("uplink: store read failed (%s)", type(exc).__name__)
        snap.incidents = [_incident(i) for i in incidents]
        snap.incidents_total = len(incidents)
        for i in snap.incidents:
            snap.incidents_by_priority[i["priority"]] = snap.incidents_by_priority.get(i["priority"], 0) + 1
        if snap.exam_state in ("running", "paused", "finished"):
            snap.zone, snap.zone_reasons_ru = self._zone(incidents, summary, info)
        return snap

    @staticmethod
    def _camera_and_monitoring(rt: Any, exam_state: str) -> tuple[str, str]:
        cap = getattr(rt.pipeline.capture, "impl", None)
        if cap is None:
            return "unknown", "degraded"
        try:
            h = cap.health()
        except Exception:
            return "unknown", "degraded"
        status, code = _val(h.status), h.code
        if status == "ok":
            camera = "ok"
        elif code in BUSY_CODES:
            camera = "busy"
        elif status == "stopped":
            camera = "off"
        else:
            camera = "unknown"
        degraded = exam_state == "running" and camera != "ok"
        faults = getattr(rt, "faults", None)  # SessionRuntime.faults: analyzer/store failures during the run
        if exam_state == "running" and faults is not None:
            try:
                degraded = degraded or any(_val(h.status) != "ok" for h in faults.health())
            except Exception:
                pass
        return camera, "degraded" if degraded else "ok"

    @staticmethod
    def _coverage_now(summary: Any, info: Any) -> float | None:
        """observed_ms / (elapsed exam time without pauses). A05's coverage_from_summary needs finished_at,
        which is None while the exam runs (it would make every running student grey)."""
        if summary is None or info.started_at is None:
            return None
        end = info.finished_at or datetime.now(timezone.utc)
        active_ms = (end - info.started_at).total_seconds() * 1000.0 - float(getattr(summary, "paused_ms", 0.0) or 0.0)
        if active_ms < 5000.0:  # the first seconds: nothing meaningful to measure yet
            return 1.0
        return max(0.0, min(1.0, float(summary.observed_ms) / active_ms))

    def _zone(self, incidents: list[Any], summary: Any, info: Any) -> tuple[str | None, list[str]]:
        try:
            from proctor.fusion.zones import assess_session_zone
        except Exception:
            return None, []
        try:
            za = assess_session_zone(incidents, summary, coverage=self._coverage_now(summary, info))
            return za.zone, list(za.reasons_ru)[:3]
        except Exception as exc:
            log.warning("uplink: zone assessment failed (%s)", type(exc).__name__)
            return None, []

    # ------------------------------------------------------------------ preview / clips
    def preview_jpeg(self) -> bytes | None:
        rt = self._runtime()
        if rt is None:
            return None
        latest = rt.preview()
        if latest is None:
            return None
        return shrink_jpeg(latest[1])

    def export_clip(self, t_start_ms: float, before_s: float, after_s: float) -> Path:
        rt = self._runtime()
        cap = getattr(rt.pipeline.capture, "impl", None) if rt is not None else None
        if cap is None or not hasattr(cap, "export_clip"):
            from proctor.capture.clips import ClipError

            raise ClipError("no_frames", "capture module with clips is not available")
        return cap.export_clip(t_start_ms, before_s, after_s)

    # ------------------------------------------------------------------ commands
    def start_exam(self) -> tuple[bool, str | None]:
        rt = self._runtime()
        if rt is None:
            return False, "На компьютере студента не создан сеанс экзамена"
        state = _val(rt.info.state)
        if state == "running":
            return True, None
        if state != "ready":
            return False, "Экзамен ещё не готов: студент должен пройти проверку и калибровку"
        try:
            rt.start()
            return True, None
        except Exception as exc:
            return False, f"Не удалось начать экзамен: {getattr(exc, 'message', type(exc).__name__)}"[:200]

    def finish_exam(self) -> tuple[bool, str | None]:
        rt = self._runtime()
        if rt is None:
            return False, "Нет активного экзамена"
        state = _val(rt.info.state)
        if state in ("finished", "aborted", "failed"):
            return True, None
        if state not in ("running", "paused"):
            return False, "Экзамен ещё не начат"
        try:
            rt.finish()
            return True, None
        except Exception as exc:
            return False, f"Не удалось завершить экзамен: {getattr(exc, 'message', type(exc).__name__)}"[:200]


def shrink_jpeg(data: bytes, max_bytes: int = PREVIEW_MAX_BYTES) -> bytes | None:
    """Decode a JPEG, fit into 320×240 (aspect kept), re-encode ≤ max_bytes (quality steps down)."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = min(PREVIEW_W / w, PREVIEW_H / h, 1.0)
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    for q in (70, 55, 40, 25):
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        if ok and len(buf) <= max_bytes:
            return buf.tobytes()
    return None
