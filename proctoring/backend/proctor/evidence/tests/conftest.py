"""Shared fixtures for A08 tests. Data is built from the contract fixtures (all synthetic).
Frames are generated gradients: no real faces or recordings anywhere in the tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from proctor.evidence import EvidenceConfig, create_evidence_store
from proctor.settings import PROCTORING_ROOT, Settings
from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import (
    AttentionObservation,
    EnvironmentObservation,
    FramePacketMeta,
    HealthObservation,
    IncidentChange,
    PhoneObservation,
    SessionInfo,
)

FIXTURES = PROCTORING_ROOT / "contracts" / "fixtures" / "v1"
T0 = datetime(2026, 10, 9, 9, 0, 0, tzinfo=timezone.utc)


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def session_info(sid: str, *, state: str = "running", mode: str = "synthetic", retain_media: bool = False, **extra) -> SessionInfo:
    raw = load("SessionInfo.running")
    raw.update(
        session_id=sid,
        state=state,
        source_mode=mode,
        retain_media=retain_media,
        source={**raw["source"], "mode": mode, "replay_id": "demo-replay" if mode == "replay" else None},
    )
    if state in ("finished", "aborted") and "finished_at" not in extra:
        extra["finished_at"] = (T0 + timedelta(seconds=60)).isoformat()
    raw.update({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in extra.items()})
    return SessionInfo.model_validate(raw)


def incident_change(sid: str, iid: str = "inc-1", seq: int = 0, *, change: str = "opened", mode: str = "synthetic", **inc) -> IncidentChange:
    raw = load("IncidentChange.opened")
    raw["change"] = change
    body = raw["incident"]
    body.update(session_id=sid, incident_id=iid, update_seq=seq, source_mode=mode, evidence_ids=[], review_status="pending")
    if change == "closed":
        body.update(state="closed", t_end_ms=body["t_start_ms"] + 2000.0, wall_end="2026-10-09T09:00:04.400000Z",
                    end_reason="condition_cleared", duration_ms=2000.0)
    body.update(inc)
    return IncidentChange.model_validate(raw)


def phone_obs(sid: str, oid: str, t: float, frame_id: int = 0, *, status: str = "ok", mode: str = "synthetic") -> PhoneObservation:
    raw = load("PhoneObservation.phone_visible")
    raw.update(session_id=sid, observation_id=oid, t_session_ms=t, frame_id=frame_id, status=status, source_mode=mode)
    return PhoneObservation.model_validate(raw)


def attention_obs(sid: str, oid: str, t: float, frame_id: int = 0, *, status: str = "ok", mode: str = "synthetic") -> AttentionObservation:
    raw = load("AttentionObservation.gaze_down")
    raw.update(session_id=sid, observation_id=oid, t_session_ms=t, frame_id=frame_id, status=status, source_mode=mode)
    return AttentionObservation.model_validate(raw)


def env_obs(sid: str, oid: str, t: float, seq: int = 1, *, mode: str = "synthetic", **detail) -> EnvironmentObservation:
    raw = load("EnvironmentObservation.alt_tab_detected")
    raw.update(session_id=sid, observation_id=oid, t_session_ms=t, client_seq=seq, source_mode=mode)
    raw["detail"].update(detail)
    return EnvironmentObservation.model_validate(raw)


def health_obs(sid: str, oid: str, t: float, *, ok: bool, mode: str = "synthetic") -> HealthObservation:
    raw = load("HealthObservation.camera_disconnected")
    raw.update(session_id=sid, observation_id=oid, t_session_ms=t, source_mode=mode, status="ok" if ok else "error")
    raw["health"].update(status="ok" if ok else "error", code="ok" if ok else "camera_disconnected")
    return HealthObservation.model_validate(raw)


def frame(sid: str, frame_id: int = 120, t: float = 4000.0, *, mode: str = "synthetic", w: int = 320, h: int = 240) -> FramePacket:
    """Synthetic gradient frame (no person in it)."""
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None, None]
    image = np.ascontiguousarray(np.concatenate([np.broadcast_to(x, (h, w, 1)), np.broadcast_to(y, (h, w, 1)),
                                                 np.full((h, w, 1), (frame_id * 37) % 256, np.float32)], axis=2).astype(np.uint8))
    image.flags.writeable = False
    meta = FramePacketMeta(
        session_id=sid, frame_id=frame_id, t_session_ms=t, wall_time=T0 + timedelta(milliseconds=t),
        t_capture_mono_ns=1, width=w, height=h, source_mode=mode, source_id="synthetic:test",
    )
    return FramePacket(meta=meta, image=image)


@pytest.fixture()
def fx():
    return SimpleNamespace(
        load=load, session_info=session_info, incident_change=incident_change, phone_obs=phone_obs,
        attention_obs=attention_obs, env_obs=env_obs, health_obs=health_obs, frame=frame, T0=T0,
    )


@pytest.fixture()
def make_store(tmp_path):
    opened = []

    def factory(data_dir: Path | None = None, **cfg):
        config = EvidenceConfig(**{"min_free_disk_bytes": 0, **cfg})
        store = create_evidence_store(Settings(data_dir=data_dir or (tmp_path / "data")), config)
        opened.append(store)
        return store

    yield factory
    for store in opened:
        store.close()


@pytest.fixture()
def store(make_store):
    s = make_store()
    health = s.open()
    assert health.status.value == "ok", health
    return s
