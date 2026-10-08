"""C2 uplink inside the REAL backend (create_app, synthetic mode, real A02/A05/A08 modules when present).

SYNTHETIC frames and scripted analyzers: proves wiring (status/incident/clip/commands/stream), not CV.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.settings import Settings
from proctor.uplink.tests.fake_server import FakeClassServer, free_port
from proctor.uplink.tests.test_uplink import wait_for

TOKEN = "c" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WS_HEADERS = {"Host": "127.0.0.1", **AUTH}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data", replay_dir=tmp_path / "replay", exam_path=tmp_path / "none.json", synthetic_fps=15.0)
    return TestClient(create_app(settings, TOKEN), base_url="http://127.0.0.1", headers=AUTH)


def _ready_session(c: TestClient) -> str:
    r = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "student_label": "c2", "consent": CONSENT})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    assert c.post(f"/v1/sessions/{sid}/preflight").json()["ready"] is True
    c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "c2 test"})
    return sid


def test_without_env_the_backend_has_no_uplink(tmp_path, monkeypatch):
    monkeypatch.delenv("QORGAU_CLASS_SERVER", raising=False)
    monkeypatch.delenv("QORGAU_CLASS_CODE", raising=False)
    with _client(tmp_path) as c:
        assert c.app.state.proctor["uplink"] is None


def test_server_unavailable_at_start_exam_runs_locally(tmp_path, monkeypatch):
    monkeypatch.setenv("QORGAU_CLASS_SERVER", f"127.0.0.1:{free_port()}")
    monkeypatch.setenv("QORGAU_CLASS_CODE", "123456")
    t0 = time.monotonic()
    with _client(tmp_path) as c:
        up = c.app.state.proctor["uplink"]
        assert up is not None
        sid = _ready_session(c)
        assert c.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
        time.sleep(1.5)
        assert c.get(f"/v1/sessions/{sid}").json()["state"] == "running"
        assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
        assert up.connection in ("connecting", "reconnecting")
    assert time.monotonic() - t0 < 30  # shutdown is not held up by the dead server


def test_end_to_end_commands_incident_clip_and_class_state_on_stream(tmp_path, monkeypatch):
    server = FakeClassServer().start()
    monkeypatch.setenv("QORGAU_CLASS_SERVER", server.address)
    monkeypatch.setenv("QORGAU_CLASS_CODE", "123456")
    try:
        with _client(tmp_path) as c:
            assert wait_for(lambda: server.connected() == 1, timeout=10)
            sid = _ready_session(c)
            assert wait_for(lambda: any(s["exam_state"] == "preflight" for s in server.of_type("status")), timeout=10)
            cid = server.send_command("start_exam")
            assert wait_for(lambda: cid in server.acks(), timeout=10) and server.acks()[cid]["ok"], server.acks().get(cid)
            assert c.get(f"/v1/sessions/{sid}").json()["state"] == "running"
            # class_state for Electron on the existing stream
            with c.websocket_connect("/v1/stream", headers=WS_HEADERS) as ws:
                lock = server.send_command("lock", {"reason_ru": "Проверка преподавателем"})
                seen = []
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    msg = ws.receive_json()["message"]
                    seen.append(msg)
                    if msg.get("type") == "class_state" and msg.get("locked"):
                        break
                cs = [m for m in seen if m.get("type") == "class_state"][-1]
                assert cs["locked"] is True and cs["lock_reason_ru"] == "Проверка преподавателем" and cs["connection"] == "connected"
            assert wait_for(lambda: lock in server.acks()) and server.acks()[lock]["ok"]
            # scripted synthetic phone -> A05 incident -> A08 -> uplink incident -> clip exported at open
            assert wait_for(lambda: any(m["type"] == "incident" and m["clip_available"] for m in server.accepted), timeout=60)
            inc = next(m for m in server.accepted if m["type"] == "incident" and m["clip_available"])
            assert inc["priority"] in ("low", "medium", "high") and inc["explanation_ru"] and inc["t_start_wall"]
            req = server.send_command("request_clip", {"incident_id": inc["incident_id"]})
            assert wait_for(lambda: req in server.acks(), timeout=20) and server.acks()[req]["ok"], server.acks().get(req)
            clip = server.clips[inc["incident_id"]]
            assert 1000 < clip["bytes"] <= 8 * 1024 * 1024 and clip["content_type"] == "video/x-msvideo"
            st = server.of_type("status")[-1]
            assert st["exam_state"] == "running" and st["locked"] is True and st["zone"] in ("green", "yellow", "red", "grey")
            assert server.of_type("preview")
            fin = server.send_command("finish_exam")
            assert wait_for(lambda: fin in server.acks(), timeout=15) and server.acks()[fin]["ok"]
            assert c.get(f"/v1/sessions/{sid}").json()["state"] == "finished"
    finally:
        server.stop()
