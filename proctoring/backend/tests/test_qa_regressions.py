"""A01 regression tests for A09 findings QA-BUG-001..005 and A02 R7 (owner: A01).

In-process tests use explicit module overrides (no dependence on installed modules/weights);
QA-BUG-001/002 are also checked against a REAL `python -m proctor serve` subprocess.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from proctor.app import HTTP_STATUS_BY_CODE, MODULES, create_app
from proctor.bootstrap.engine import BootstrapIncidentEngine
from proctor.bootstrap.memory_store import MemoryEvidenceStore
from proctor.settings import Settings
from proctor_contracts.v1 import ErrorCode

TOKEN = "q" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}
HIDDEN = {key: None for key in MODULES}
ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        replay_dir=tmp_path / "replay",
        exam_path=tmp_path / "missing_exam.json",
        synthetic_fps=30.0,
        fusion_tick_ms=50.0,
    )


def _client(tmp_path, **overrides):
    app = create_app(_settings(tmp_path), TOKEN, module_overrides={**HIDDEN, **overrides})
    return TestClient(app, base_url="http://127.0.0.1", headers=AUTH)


def _running_synthetic(c) -> str:
    r = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT})
    sid = r.json()["session_id"]
    assert c.post(f"/v1/sessions/{sid}/preflight").json()["ready"] is True
    c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "test"})
    assert c.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
    return sid


def _components(c) -> dict:
    return {h["component"]: h for h in c.get("/v1/health").json()["components"]}


class _FailingStore(MemoryEvidenceStore):
    """Store double whose writes fail (SQLite locked / disk full)."""

    def __init__(self) -> None:
        super().__init__()
        self.write_attempts = 0

    def record_observation(self, observation):
        self.write_attempts += 1
        raise OSError("disk full (test double)")

    def record_incident_change(self, change):
        self.write_attempts += 1
        raise OSError("disk full (test double)")

    def upsert_session(self, info):
        self.write_attempts += 1
        raise OSError("disk full (test double)")


class _FailingEngine(BootstrapIncidentEngine):
    def consume(self, observation):
        raise RuntimeError("engine bug (test double)")


# ------------------------------------------------------------------ QA-BUG-004 / 005


def test_storage_failure_does_not_silence_episode_detection(tmp_path):
    store = _FailingStore()
    with _client(tmp_path, evidence=lambda s: store) as c:
        sid = _running_synthetic(c)
        with c.websocket_connect(f"/v1/stream?session_id={sid}", headers={"Host": "127.0.0.1", **AUTH}) as ws:
            opened, deadline = None, time.monotonic() + 25
            while opened is None and time.monotonic() < deadline:
                msg = ws.receive_json()["message"]
                if msg["type"] == "incident" and msg["change"]["incident"]["rule_id"] == "phone_visible":
                    opened = msg["change"]["incident"]
        assert opened is not None, "a failing store must not stop episode detection"
        comps = _components(c)
        assert comps["evidence"]["status"] == "degraded" and comps["evidence"]["code"] == "store_write_failed"
        assert comps["evidence"]["details"]["errors"] >= 1
        info = c.get(f"/v1/sessions/{sid}").json()
        assert info["last_error"]["code"] == "STORAGE_ERROR"
        assert c.get("/v1/health").json()["overall"] != "ok"
        assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
    attempts = store.write_attempts
    assert attempts < 2000, f"error reporting must not loop on the failing store ({attempts} writes)"


def test_engine_failure_is_visible_and_session_controllable(tmp_path):
    with _client(tmp_path, fusion=lambda sid, mode, settings: _FailingEngine(sid, mode)) as c:
        sid = _running_synthetic(c)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _components(c)["fusion"]["status"] == "ok":
            time.sleep(0.1)
        comps = _components(c)
        assert comps["fusion"]["status"] == "degraded" and comps["fusion"]["code"] == "fusion_error"
        assert c.get(f"/v1/sessions/{sid}").json()["last_error"]["details"]["component"] == "fusion"
        assert c.post(f"/v1/sessions/{sid}/pause", json={"reason": "t"}).json()["state"] == "paused"
        assert c.post(f"/v1/sessions/{sid}/resume").json()["state"] == "running"
        assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"


def test_analyzer_failure_is_visible(tmp_path):
    from proctor.bootstrap.synthetic import ScriptedPhoneAnalyzer

    class Broken(ScriptedPhoneAnalyzer):
        def process(self, frame):
            raise ValueError("bad tensor (test double)")

    with _client(tmp_path) as c:
        app_registry = c.app.state.registry
        app_registry.scripted_phone = Broken()  # synthetic pipeline uses the scripted analyzer slot
        sid = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT}).json()["session_id"]
        c.post(f"/v1/sessions/{sid}/preflight")
        c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "t"})
        c.post(f"/v1/sessions/{sid}/start")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _components(c).get("phone", {}).get("code") != "analyzer_error":
            time.sleep(0.1)
        assert _components(c)["phone"]["code"] == "analyzer_error"
        c.post(f"/v1/sessions/{sid}/abort", json={"reason": "t"})


# ------------------------------------------------------------------ A02 R7


def test_finish_does_not_turn_capture_close_into_a_gap(tmp_path):
    with _client(tmp_path) as c:
        sid = _running_synthetic(c)
        with c.websocket_connect(f"/v1/stream?session_id={sid}", headers={"Host": "127.0.0.1", **AUTH}) as ws:
            assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
            seen = []
            while True:
                msg = ws.receive_json()["message"]
                seen.append(msg)
                if msg["type"] == "session_state" and msg["session"]["state"] == "finished":
                    break
        health_obs = [m for m in seen if m["type"] == "observation" and m["observation"]["kind"] == "health"]
        assert not [h for h in health_obs if h["observation"]["health"]["status"] == "stopped"], health_obs


# ------------------------------------------------------------------ QA-BUG-003


def test_every_error_code_has_a_contract_status():
    assert set(HTTP_STATUS_BY_CODE) == set(ErrorCode)
    assert HTTP_STATUS_BY_CODE[ErrorCode.INVALID_ARGUMENT] == 422
    assert HTTP_STATUS_BY_CODE[ErrorCode.SESSION_MISMATCH] == 409
    assert HTTP_STATUS_BY_CODE[ErrorCode.MODULE_NOT_INTEGRATED] == 503


def test_error_statuses_follow_the_table(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": {**CONSENT, "accepted": False}})
        assert (r.status_code, r.json()["error"]["code"]) == (422, "INVALID_ARGUMENT")
        r = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "no-such-exam", "consent": CONSENT})
        assert (r.status_code, r.json()["error"]["code"]) == (422, "INVALID_ARGUMENT")
        sid = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT}).json()["session_id"]
        ev = {"action": "shortcut_ctrl_c", "enforcement": "blocked", "mechanism": "t", "scope": "window",
              "client_seq": 1, "client_wall_time": "2026-10-08T09:00:05Z"}
        r = c.post(f"/v1/sessions/{sid}/environment/events", json={"session_id": "other", "events": [ev]})
        assert (r.status_code, r.json()["error"]["code"]) == (409, "SESSION_MISMATCH")
        r = c.post(f"/v1/sessions/{sid}/start")
        assert (r.status_code, r.json()["error"]["code"]) == (409, "INVALID_STATE")


# ------------------------------------------------------------------ QA-BUG-002 / OBS-004


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/sessions/" + "A" * 1000),
        ("GET", "/v1/sessions/bad%20id"),
        ("PUT", "/v1/sessions/s-1/answers/" + "q" * 200),
        ("POST", "/v1/sessions/s-1/incidents/" + "i" * 1000 + "/reviews"),
        ("GET", "/v1/sessions/" + "A" * 1000 + "/incidents"),  # A08/bootstrap router is covered too
    ],
)
def test_invalid_path_ids_are_422(tmp_path, method, path):
    with _client(tmp_path) as c:
        r = c.request(method, path, json={})
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_ARGUMENT", r.text[:200]
        assert len(r.json()["error"]["message"]) <= 1000
        assert c.get("/v1/health").status_code == 200


def test_deleted_session_is_not_readable(tmp_path):
    with _client(tmp_path) as c:
        sid = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT}).json()["session_id"]
        assert c.delete(f"/v1/sessions/{sid}").status_code == 409  # active
        c.post(f"/v1/sessions/{sid}/abort", json={"reason": "t"})
        assert c.delete(f"/v1/sessions/{sid}").json() == {"deleted": True}
        assert c.get(f"/v1/sessions/{sid}").status_code == 404


# ------------------------------------------------------------------ real subprocess: QA-BUG-001/002/003


@pytest.fixture(scope="module")
def served():
    token = secrets.token_hex(32)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "backend"), str(ROOT / "contracts" / "python")]),
           "QORGAU_LOG_LEVEL": "INFO", "QORGAU_MODELS_DIR": str(ROOT / "nonexistent-models-dir")}
    proc = subprocess.Popen([sys.executable, "-m", "proctor", "serve", "--token-stdin", "--port", "0"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    proc.stdin.write(token + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line.startswith("QORGAU_READY "), line
    port = json.loads(line.split(" ", 1)[1])["port"]
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
    yield token, port, stderr
    proc.stdin.close()
    proc.wait(timeout=15)


def test_subprocess_overlong_id_keeps_connection(served):
    token, port, _ = served
    with httpx.Client(base_url=f"http://127.0.0.1:{port}/v1", headers={"Authorization": f"Bearer {token}"}) as c:
        r = c.get("/sessions/" + "A" * 1000)
        assert r.status_code == 422
        assert c.get("/health").status_code == 200  # same keep-alive connection still usable


def test_subprocess_consent_is_422(served):
    token, port, _ = served
    r = httpx.post(f"http://127.0.0.1:{port}/v1/sessions", headers={"Authorization": f"Bearer {token}"},
                   json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": {**CONSENT, "accepted": False}})
    assert r.status_code == 422


def test_subprocess_token_in_ws_query_not_logged(served):
    from websockets.sync.client import connect

    token, port, stderr = served
    with pytest.raises(Exception):
        with connect(f"ws://127.0.0.1:{port}/v1/stream?token={token}", open_timeout=5) as ws:
            ws.recv(timeout=2)
    time.sleep(0.5)
    assert any("WebSocket /v1/stream" in line for line in stderr), "handshake line expected at INFO"
    assert not [line for line in stderr if token in line], "token leaked into the backend log"


# ------------------------------------------------------------------ A06 #6


def test_release_event_shortly_after_finish_is_accepted(tmp_path):
    with _client(tmp_path) as c:
        sid = _running_synthetic(c)
        assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
        base = {"enforcement": "allowed", "mechanism": "t", "scope": "app", "client_wall_time": "2026-10-08T09:00:05Z"}
        r = c.post(f"/v1/sessions/{sid}/environment/events",
                   json={"session_id": sid, "events": [{**base, "action": "exam_mode_released", "client_seq": 9}]})
        assert r.status_code == 200 and r.json()["accepted"] == 1
        r = c.post(f"/v1/sessions/{sid}/environment/events",
                   json={"session_id": sid, "events": [{**base, "action": "shortcut_alt_tab", "enforcement": "detected_only", "client_seq": 10}]})
        assert r.status_code == 409  # only release/focus bookkeeping is accepted after the end
