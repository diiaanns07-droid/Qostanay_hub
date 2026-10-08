"""A11 composition regressions: labelled synthetic inputs, real SQLite/fusion, no devices/models."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proctor.app import MODULES, create_app
from proctor.evidence import EvidenceConfig, create_evidence_store
from proctor.fusion import create_incident_engine
from proctor.settings import PROCTORING_ROOT, Settings
from proctor_contracts.v1 import Health, HealthObservation, IncidentChange, PhoneObservation, SessionInfo

TOKEN = "a11-labelled-test-token-" * 3
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "qa-synthetic", "accepted_at": "2026-10-08T09:00:00Z"}


def fixture(name):
    return json.loads((PROCTORING_ROOT / "contracts" / "fixtures" / "v1" / f"{name}.json").read_text())


def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "no-models",
                    replay_dir=tmp_path / "no-media", exam_path=tmp_path / "no-exam.json",
                    synthetic_fps=10.0, fusion_tick_ms=20.0)


def client(tmp_path, store=None):
    cfg = settings(tmp_path)
    store = store or create_evidence_store(cfg, EvidenceConfig(min_free_disk_bytes=0))
    app = create_app(cfg, TOKEN, module_overrides={**{key: None for key in MODULES},
        "evidence": lambda _: store, "fusion": create_incident_engine})
    return TestClient(app, base_url="http://127.0.0.1", headers=AUTH), store


def running(c):
    sid = c.post("/v1/sessions", json={"source": {"mode": "synthetic"},
        "exam_id": "demo-exam-1", "consent": CONSENT}).json()["session_id"]
    assert c.post(f"/v1/sessions/{sid}/preflight").json()["ready"]
    assert c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "SYNTHETIC A11 fixture"}).status_code == 200
    assert c.post(f"/v1/sessions/{sid}/start").status_code == 200
    return sid, c.app.state.proctor["manager"].runtime(sid)


def wait_for(predicate):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def change(sid, seq=0, closed=False):
    raw = fixture("IncidentChange.opened")
    raw["incident"].update(session_id=sid, incident_id="a11-retry", update_seq=seq,
                           source_mode="synthetic", observation_ids=[], evidence_ids=[])
    if closed:
        raw["change"] = "closed"
        raw["incident"].update(state="closed", t_end_ms=4400, duration_ms=2000,
            wall_end="2026-10-09T09:00:04.400000Z", end_reason="condition_cleared")
    return IncidentChange.model_validate(raw)


def test_real_store_failure_is_visible_and_closed_change_retried(tmp_path, monkeypatch):
    c, store = client(tmp_path)
    with c:
        sid, rt = running(c)
        messages = []
        original_publish = rt._bus.publish
        def publish(msg, session_id):
            messages.append(msg)
            original_publish(msg, session_id)
        monkeypatch.setattr(rt._bus, "publish", publish)
        rt._emit([change(sid)])
        def fail(point):
            if point == "incident_change:after_history":
                raise sqlite3.OperationalError("database or disk is full (A11 synthetic fixture)")
        store._fault = fail
        rt._emit([change(sid, 1, True)])
        assert c.get(f"/v1/sessions/{sid}").json()["last_error"]["code"] == "STORAGE_ERROR"
        assert rt.counters["store_errors"] > 0
        assert any(m.type == "health" for m in messages)
        assert store.get_incident_detail(sid, "a11-retry").incident.state.value == "open"
        store._fault = None
        assert c.post(f"/v1/sessions/{sid}/finish").status_code == 200
        actual = store.get_incident_detail(sid, "a11-retry").incident
        assert (actual.state.value, actual.update_seq, actual.t_end_ms) == ("closed", 1, 4400)
        assert not rt._pending_incidents
        assert any(d.incident.incident_id == "a11-retry" and d.incident.state.value == "closed" for d in store.export_snapshot(sid).incidents)


def test_permanent_store_fault_is_bounded_and_does_not_recurse_into_upsert(tmp_path, monkeypatch):
    c, store = client(tmp_path)
    with c:
        sid, rt = running(c)
        calls = []
        original = store.upsert_session
        monkeypatch.setattr(store, "upsert_session", lambda info: (calls.append(info), original(info))[1])
        def fail(point):
            raise sqlite3.OperationalError("A11 persistent synthetic disk fault")
        store._fault = fail
        rt._emit([change(sid, n, True) for n in range(1, 9)])
        assert not calls, "fault reporting must not rewrite the failed store synchronously"
        assert len(rt._pending_incidents) <= 2  # latest test incident + genuine storage-health incident
        assert rt._pending_incidents["a11-retry"].incident.update_seq == 8
        attempts = store.write_error_count()
        for _ in range(100):
            rt._emit([])
        assert store.write_error_count() <= attempts + 2
        store._fault = None
        c.post(f"/v1/sessions/{sid}/finish")
        assert store.get_incident_detail(sid, "a11-retry").incident.update_seq == 8
        assert c.delete(f"/v1/sessions/{sid}").status_code == 200
        rt._retry_incident_writes(force=True)
        assert store.get_session(sid) is None


def test_paused_health_recovery_and_environment_are_persisted_without_cv(tmp_path):
    c, store = client(tmp_path)
    with c:
        sid, rt = running(c)
        rt._on_capture_health(Health(component="capture", status="unavailable", code="camera_disconnected"))
        wait_for(lambda: "capture" in rt._engine._health_bad)
        assert c.post(f"/v1/sessions/{sid}/pause", json={"reason": "SYNTHETIC pause"}).status_code == 200
        rt._on_capture_health(Health(component="capture", status="ok", code="qa_reconnected"))
        wait_for(lambda: "capture" not in rt._engine._health_bad)
        raw = fixture("PhoneObservation.phone_visible")
        raw.update(session_id=sid, observation_id="a11-paused-cv", source_mode="synthetic", t_session_ms=rt.clock.now_ms())
        rt.publish_observation(PhoneObservation.model_validate(raw))
        ack = c.post(f"/v1/sessions/{sid}/environment/events", json={"session_id": sid, "events": [{
            "action": "focus_regained", "enforcement": "allowed", "mechanism": "qa", "scope": "window",
            "client_seq": 765, "client_wall_time": "2026-10-08T09:00:05Z"}]}).json()
        assert ack["accepted"] == 1
        wait_for(lambda: store._conn.execute("SELECT 1 FROM observations WHERE observation_id=?", (ack["observation_ids"][0],)).fetchone())
        assert "a11-paused-cv" not in store._live[sid].ring
        before = {i.incident_id for i in store.list_incidents(sid)}
        assert c.post(f"/v1/sessions/{sid}/resume").status_code == 200
        c.post(f"/v1/sessions/{sid}/finish")
        new = [i for i in store.list_incidents(sid) if i.incident_id not in before]
        assert not [i for i in new if "camera_disconnected" in i.model_dump_json()]
        gaps = [g for g in store.summary(sid).gaps if g.component == "capture"]
        assert all(g.t_end_ms is not None for g in gaps)


def test_pre_start_frame_cannot_open_an_incident(tmp_path):
    c, store = client(tmp_path)
    with c:
        sid, rt = running(c)
        raw = fixture("PhoneObservation.phone_visible")
        raw.update(session_id=sid, observation_id="a11-before-exam", source_mode="synthetic",
                   t_session_ms=max(0, rt.info.exam_started_t_ms - 1))
        rt.publish_observation(PhoneObservation.model_validate(raw))
        c.post(f"/v1/sessions/{sid}/finish")
        assert not [i for i in store.list_incidents(sid) if "a11-before-exam" in i.observation_ids]
        assert all(i.t_start_ms >= rt.info.exam_started_t_ms for i in store.list_incidents(sid))


@pytest.mark.parametrize("initial,expected", [("running", "failed"), ("finished", "finished")])
def test_persisted_session_is_readable_after_restart_but_never_reactivated(tmp_path, initial, expected):
    cfg = settings(tmp_path)
    first = create_evidence_store(cfg, EvidenceConfig(min_free_disk_bytes=0))
    assert first.open().status.value == "ok"
    raw = fixture("SessionInfo.running")
    raw.update(session_id="a11-persisted", state=initial, source_mode="synthetic")
    first.upsert_session(SessionInfo.model_validate(raw))
    first.close()  # leave a RUNNING row exactly as an interrupted backend does
    c, store = client(tmp_path)
    with c:
        info = c.get("/v1/sessions/a11-persisted")
        assert info.status_code == 200 and info.json()["state"] == expected
        if initial == "running":
            assert info.json()["last_error"]["details"]["recovered"] is True
        assert c.get("/v1/sessions/a11-persisted/exam").status_code == 200
        for action in ("start", "resume", "finish", "abort"):
            response = c.post(f"/v1/sessions/a11-persisted/{action}", json={"reason": "test"} if action == "abort" else None)
            assert response.status_code == 409, response.text
        assert c.app.state.proctor["manager"].active_runtime() is None
        assert not c.app.state.proctor["manager"]._sessions
        assert c.get("/v1/sessions/unknown").status_code == 404
        assert c.get("/v1/sessions/unknown/exam").status_code == 404
        assert c.post("/v1/sessions/unknown/finish").status_code == 404
        assert c.delete("/v1/sessions/a11-persisted").status_code == 200
        assert c.get("/v1/sessions/a11-persisted").status_code == 404
