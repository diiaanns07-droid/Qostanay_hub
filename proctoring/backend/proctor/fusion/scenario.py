"""Observation streams for tests, golden scenarios and replay tuning (tooling, not runtime).

Every observation is built from a shared contract fixture (``contracts/fixtures/v1``) and re-validated
by the contract models, so scenarios cannot drift from the wire format. The engine itself never
reads files.

Golden scenario format (``fusion/tests/golden/*.json``)::

    {"name": ..., "mode": "synthetic", "config": {...overrides...},
     "phone":     [{"from": 0, "to": 2000, "step": 125, "visible": "absent"}, ...],
     "attention": [{"from": 0, "to": 2000, "step": 100, "direction": "center"}, ...],
     "events":    [{"t": 5000, "kind": "environment", "action": "shortcut_alt_tab", "enforcement": "blocked"},
                   {"t": 6000, "kind": "health", "component": "capture", "status": "error", "code": "camera_disconnected"}],
     "finish": 20000,
     "expected": [{"rule_id": ..., "t_start_ms": ..., "t_end_ms": ..., ...}]}

A segment produces samples at ``from, from+step, ...`` strictly below ``to``.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from pydantic import TypeAdapter

from proctor.settings import PROCTORING_ROOT
from proctor_contracts.v1 import (
    AttentionObservation,
    EnvironmentObservation,
    HealthObservation,
    Observation,
    PhoneObservation,
)

FIXTURE_DIR = PROCTORING_ROOT / "contracts" / "fixtures" / "v1"
TEMPLATES = {
    "phone": "PhoneObservation.phone_visible.json",
    "attention": "AttentionObservation.gaze_down.json",
    "environment": "EnvironmentObservation.alt_tab_detected.json",
    "health": "HealthObservation.camera_disconnected.json",
}
# order of sources sharing one timestamp (deterministic merge)
SOURCE_RANK = {"health": 0, "environment": 1, "phone": 2, "attention": 3}
OBSERVATION = TypeAdapter(Observation)
CAPTURE_FPS = 30.0


def load_fixture(name: str, fixtures_dir: Path = FIXTURE_DIR) -> dict[str, Any]:
    return json.loads((fixtures_dir / name).read_text(encoding="utf-8"))


def frame_for(t_ms: float) -> int:
    return int(round(t_ms * CAPTURE_FPS / 1000.0))


class ScenarioBuilder:
    """Builds valid observations of one session from the contract fixture templates."""

    def __init__(self, session_id: str = "fx-session-0001", mode: str = "synthetic", fixtures_dir: Path = FIXTURE_DIR):
        self.session_id = session_id
        self.mode = mode
        self.templates = {kind: load_fixture(name, fixtures_dir) for kind, name in TEMPLATES.items()}
        phone = self.templates["phone"]
        self.anchor = datetime.fromisoformat(phone["wall_time"].replace("Z", "+00:00")) - timedelta(
            milliseconds=phone["t_session_ms"]
        )
        self._n: dict[str, int] = {}

    # ------------------------------------------------------------------ helpers
    def _base(self, kind: str, t: float, observation_id: str | None, **over: Any) -> dict[str, Any]:
        data = deepcopy(self.templates[kind])
        n = self._n[kind] = self._n.get(kind, 0) + 1
        data.update(
            observation_id=observation_id or f"sc-{kind}-{n}",
            session_id=over.pop("session_id", self.session_id),
            source_mode=over.pop("source_mode", self.mode),
            t_session_ms=float(t),
            wall_time=over.pop("wall_time", (self.anchor + timedelta(milliseconds=t)).isoformat()),
        )
        if kind in ("phone", "attention"):
            data["frame_id"] = frame_for(t)
        data.update(over)
        return data

    # ------------------------------------------------------------------ observations
    def phone(
        self,
        t: float,
        *,
        visible: str = "present",
        raised: str = "absent",
        capture: str = "insufficient_evidence",
        status: str = "ok",
        quality: float | None = 0.8,
        confidence: float = 0.71,
        phones: int | None = None,
        observation_id: str | None = None,
        **over: Any,
    ) -> PhoneObservation:
        data = self._base("phone", t, observation_id, **over)
        det = data["detections"][0]
        det["confidence"] = confidence
        n_phones = (1 if visible == "present" else 0) if phones is None else phones
        data["detections"] = [deepcopy(det) for _ in range(n_phones)]
        signals = {s["name"]: s for s in data["signals"]}
        for name, state in (("phone_visible", visible), ("phone_raised", raised), ("possible_screen_capture", capture)):
            sig = signals[name]
            sig["state"] = state
            sig["confidence"] = confidence if state == "present" else None
        data.update(status=status, quality=quality, latency_ms=None)
        return PhoneObservation.model_validate(data)

    def attention(
        self,
        t: float,
        *,
        direction: str = "center",
        head: str | None = None,
        face_count: int | None = 1,
        primary: bool | None = None,
        status: str = "ok",
        quality: float | None = 0.75,
        calibrated: bool = True,
        gaze_confidence: float | None = 0.6,
        observation_id: str | None = None,
        **over: Any,
    ) -> AttentionObservation:
        data = self._base("attention", t, observation_id, **over)
        if face_count is None or status in ("unknown", "error"):
            data.update(faces=[], primary_face_present=None, head_pose=None, head_direction="unknown", gaze=None,
                        face_count=None)
        else:
            face = data["faces"][0]
            data["faces"] = [dict(face, is_primary=(i == 0)) for i in range(face_count)]
            data["face_count"] = face_count
            present = face_count > 0 if primary is None else primary
            data["primary_face_present"] = present
            if face_count == 0:
                data.update(head_pose=None, head_direction="unknown", gaze=None)
            else:
                data["head_direction"] = head or direction
                data["gaze"].update(direction=direction, calibrated=calibrated, confidence=gaze_confidence)
        data.update(status=status, quality=quality, latency_ms=None, reasons=[])
        return AttentionObservation.model_validate(data)

    def environment(
        self,
        t: float,
        action: str,
        *,
        enforcement: str = "detected_only",
        process_name: str | None = None,
        client_seq: int | None = None,
        observation_id: str | None = None,
        **over: Any,
    ) -> EnvironmentObservation:
        data = self._base("environment", t, observation_id, **over)
        data.update(
            action=action,
            enforcement=enforcement,
            client_seq=client_seq if client_seq is not None else self._n["environment"],
            client_wall_time=data["wall_time"],
            detail={"process_name": process_name, "shortcut": None, "duration_ms": None},
        )
        return EnvironmentObservation.model_validate(data)

    def health(
        self,
        t: float,
        *,
        component: str = "capture",
        status: str = "error",
        code: str = "camera_disconnected",
        observation_id: str | None = None,
        **over: Any,
    ) -> HealthObservation:
        data = self._base("health", t, observation_id, **over)
        data["status"] = "ok" if status == "ok" else ("degraded" if status == "degraded" else "error")
        data["health"] = {
            "component": component,
            "status": status,
            "code": code,
            "message": "",
            "since_t_session_ms": float(t),
            "details": {},
        }
        return HealthObservation.model_validate(data)

    # ------------------------------------------------------------------ runs
    def run(self, kind: str, segments: Iterable[dict[str, Any]]) -> list[Observation]:
        make = {"phone": self.phone, "attention": self.attention}[kind]
        out: list[Observation] = []
        for seg in segments:
            seg = dict(seg)
            start, stop, step = float(seg.pop("from")), float(seg.pop("to")), float(seg.pop("step"))
            n = 0
            while start + n * step < stop - 1e-9:
                out.append(make(round(start + n * step, 3), **seg))
                n += 1
        return out

    def event(self, spec: dict[str, Any]) -> Observation:
        spec = dict(spec)
        kind, t = spec.pop("kind"), spec.pop("t")
        if kind == "environment":
            return self.environment(t, spec.pop("action"), **spec)
        if kind == "health":
            return self.health(t, **spec)
        if kind == "phone":
            return self.phone(t, **spec)
        if kind == "attention":
            return self.attention(t, **spec)
        raise ValueError(f"unknown event kind {kind}")


def merge(*streams: Iterable[Observation], delay_ms: dict[str, float] | None = None) -> list[Observation]:
    """One delivery order for several streams: by arrival time (t + per-source delay), then source rank.
    Per-source order is preserved; ``delay_ms`` simulates cross-source processing latency."""
    delay_ms = delay_ms or {}
    items = [obs for stream in streams for obs in stream]
    return sorted(items, key=lambda o: (o.t_session_ms + delay_ms.get(o.kind, 0.0), SOURCE_RANK[o.kind]))


def build_scenario(spec: dict[str, Any], *, delay_ms: dict[str, float] | None = None) -> list[Observation]:
    b = ScenarioBuilder(session_id=spec.get("session_id", "fx-session-0001"), mode=spec.get("mode", "synthetic"))
    phone = b.run("phone", spec.get("phone", []))
    attention = b.run("attention", spec.get("attention", []))
    events = [b.event(e) for e in spec.get("events", [])]
    return merge(phone, attention, events, delay_ms=delay_ms)


def load_golden(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_observation(data: dict[str, Any]) -> Observation:
    return OBSERVATION.validate_python(data)
