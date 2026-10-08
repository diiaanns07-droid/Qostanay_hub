"""A01 lifecycle/API tests (in-process). Module owners keep their tests in their own packages."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from proctor.app import MODULES, create_app
from proctor.bootstrap.engine import BootstrapIncidentEngine
from proctor.bootstrap.memory_store import MemoryEvidenceStore
from proctor.bootstrap.synthetic import ScriptedAttentionAnalyzer, ScriptedPhoneAnalyzer, SyntheticCaptureService
from proctor.settings import Settings
from proctor_contracts import interfaces as itf
from proctor_contracts.v1 import (
    CONTRACT_VERSION,
    AttentionObservation,
    Component,
    Health,
    HealthStatus,
    Explanation,
    Incident,
    IncidentCategory,
    IncidentChange,
    IncidentChangeType,
    IncidentRule,
    IncidentState,
    ReviewPriority,
    SourceConfig,
    SourceMode,
    StreamEnvelope,
)

TOKEN = "t" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}


# A01 unit tests run against the bootstrap composition ONLY: real modules are hidden explicitly, and
# models/data/replay dirs point into tmp, so results do not depend on which modules or weights exist on
# the machine. Integrated-module behaviour is tested separately (test_integrated_*.py).
HIDDEN = {key: None for key in MODULES}


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        replay_dir=tmp_path / "replay",
        exam_path=tmp_path / "missing_exam.json",
        synthetic_fps=30.0,
        fusion_tick_ms=50.0,
    )


def _app(tmp_path, overrides=None):
    return create_app(_settings(tmp_path), TOKEN, module_overrides={**HIDDEN, **(overrides or {})})


@pytest.fixture()
def client(tmp_path):
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1", headers=AUTH) as c:
        yield c


def _create(client, mode="synthetic", **extra) -> str:
    r = client.post("/v1/sessions", json={"source": {"mode": mode, **extra}, "exam_id": "demo-exam-1", "consent": CONSENT})
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _ready(client, sid: str) -> None:
    assert client.post(f"/v1/sessions/{sid}/preflight").json()["ready"] is True
    r = client.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "test"})
    assert r.status_code == 200 and r.json()["phase"] == "skipped"


# --------------------------------------------------------------------------- security


def test_requires_token(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.get("/v1/health")
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
        assert c.get("/v1/health", headers={"Authorization": "Bearer " + "x" * 48}).status_code == 401


def test_rejects_non_loopback_host_and_foreign_origin(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, base_url="http://evil.example", headers=AUTH) as c:
        assert c.get("/v1/health").status_code == 403  # DNS-rebinding style Host
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        assert c.get("/v1/health", headers={"Origin": "http://evil.example"}).status_code == 403


def test_rejects_large_body(client):
    r = client.post("/v1/sessions", content=b"x" * 1_100_000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_short_token_refused():
    with pytest.raises(ValueError):
        create_app(Settings(), "short")


def test_validation_error_uses_api_error(client):
    r = client.post("/v1/sessions", json={"source": {"mode": "webcam"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_ARGUMENT"


# --------------------------------------------------------------------------- lifecycle


def test_health_reports_missing_modules_honestly(client):
    body = client.get("/v1/health").json()
    assert body["contract_version"] == CONTRACT_VERSION
    codes = {c["component"]: c["code"] for c in body["components"]}
    for comp in ("capture", "phone", "attention", "fusion", "evidence"):
        assert codes[comp] == "module_not_integrated"
    assert body["overall"] == "degraded"


def test_consent_required(client):
    r = client.post(
        "/v1/sessions",
        json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": {**CONSENT, "accepted": False}},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_invalid_transitions(client):
    sid = _create(client)
    r = client.post(f"/v1/sessions/{sid}/start")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert client.post(f"/v1/sessions/{sid}/pause", json={"reason": "x"}).status_code == 409
    assert client.post(f"/v1/sessions/{sid}/calibration/start").status_code == 409
    assert client.get("/v1/sessions/nope").json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_live_never_falls_back_to_synthetic(client):
    sid = _create(client, mode="live")
    report = client.post(f"/v1/sessions/{sid}/preflight").json()
    checks = {c["check_id"]: c for c in report["checks"]}
    assert report["ready"] is False
    assert checks["camera"]["status"] == "fail"
    for key in ("phone_model", "face_model", "fusion", "storage", "environment_protection"):
        assert checks[key]["required"] is True and checks[key]["status"] == "fail", key
    assert client.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "x"}).json()["error"]["code"] == "PREFLIGHT_FAILED"
    assert client.post(f"/v1/sessions/{sid}/abort", json={"reason": "test"}).json()["state"] == "aborted"


def test_single_active_session_and_restart(client):
    sid = _create(client)
    r = client.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT})
    assert r.status_code == 409 and r.json()["error"]["details"]["active_session_id"] == sid
    _ready(client, sid)
    assert client.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
    sid2 = _create(client)
    assert sid2 != sid
    _ready(client, sid2)  # capture reopened for the new session
    assert client.post(f"/v1/sessions/{sid2}/abort", json={"reason": "t"}).json()["state"] == "aborted"
    r = client.post(f"/v1/sessions/{sid2}/finish")
    assert r.status_code == 409  # aborted is terminal; finish does not rewrite history


def test_pause_resume_and_answers(client):
    sid = _create(client)
    _ready(client, sid)
    assert client.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
    assert client.put(f"/v1/sessions/{sid}/answers/q1", json={"value": ["b"], "client_seq": 1}).status_code == 200
    assert client.post(f"/v1/sessions/{sid}/pause", json={"reason": "operator"}).json()["state"] == "paused"
    assert client.put(f"/v1/sessions/{sid}/answers/q1", json={"value": ["a"], "client_seq": 2}).status_code == 409
    time.sleep(0.2)
    info = client.post(f"/v1/sessions/{sid}/resume").json()
    assert info["state"] == "running" and info["paused_total_ms"] >= 150
    assert client.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"


def test_environment_batch_session_mismatch(client):
    sid = _create(client)
    ev = {
        "action": "shortcut_ctrl_c",
        "enforcement": "blocked",
        "mechanism": "test",
        "scope": "window",
        "client_seq": 1,
        "client_wall_time": "2026-10-08T09:00:05Z",
    }
    r = client.post(f"/v1/sessions/{sid}/environment/events", json={"session_id": "other", "events": [ev]})
    assert r.json()["error"]["code"] == "SESSION_MISMATCH"


def test_stream_delivers_contract_envelopes(client):
    sid = _create(client)
    with client.websocket_connect("/v1/stream", headers={"Host": "127.0.0.1", **AUTH}) as ws:
        hello = StreamEnvelope.model_validate(ws.receive_json())
        assert hello.message.type == "hello"
        client.post(f"/v1/sessions/{sid}/preflight")
        seen = set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not {"session_state", "observation"} <= seen:
            env = StreamEnvelope.model_validate(ws.receive_json())
            seen.add(env.message.type)
            assert env.session_id in (None, sid)
        assert {"session_state", "observation"} <= seen
    client.post(f"/v1/sessions/{sid}/abort", json={"reason": "t"})


def test_websocket_requires_token(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/v1/stream", headers={"Host": "127.0.0.1"}) as ws:
                ws.receive_json()
        # browser-style subprotocol token is accepted
        with c.websocket_connect(
            "/v1/stream", subprotocols=["qorgau.v1", f"qorgau.bearer.{TOKEN}"], headers={"Host": "127.0.0.1"}
        ) as ws:
            assert ws.receive_json()["message"]["type"] == "hello"


# --------------------------------------------------------------------------- composition


class _FakeLiveCapture(SyntheticCaptureService):
    """Test double standing in for A02: accepts live/replay configs, produces synthetic pixels."""

    def open(self, session_id, source, clock):  # type: ignore[override]
        super().open(session_id, SourceConfig(mode=SourceMode.SYNTHETIC), clock)


class _RecordingEngine(BootstrapIncidentEngine):
    consumed: list = []

    def __init__(self, session_id, source_mode, settings):  # A05 factory signature
        self.session_id = session_id
        self._sid = session_id
        self._mode = source_mode
        self._finished = False

    def consume(self, observation):
        _RecordingEngine.consumed.append(observation)
        return []

    def advance(self, t):
        return []

    def set_paused(self, paused, t):
        return []

    def finish(self, t, reason):
        inc = Incident(
            incident_id=f"inc-{self._sid}-final",
            session_id=self._sid,
            rule_id=IncidentRule.FACE_MISSING,
            category=IncidentCategory.PRESENCE,
            state=IncidentState.CLOSED,
            priority=ReviewPriority.LOW,
            t_start_ms=0.0,
            t_end_ms=t,
            wall_start=datetime.now(timezone.utc),
            duration_ms=t,
            source_mode=self._mode,
            explanation=Explanation(summary_ru="test"),
            rule_version="t",
            config_version="t",
            update_seq=1,
        )
        return [IncidentChange(change=IncidentChangeType.CLOSED, incident=inc)]


def test_registered_modules_are_used_for_live(tmp_path):
    """With module factories present, LIVE uses them (and never the scripted analyzers)."""
    _RecordingEngine.consumed = []

    def ok(component):
        return lambda: Health(component=component, status=HealthStatus.OK, code="ok")

    store = MemoryEvidenceStore()
    store.health = ok(Component.EVIDENCE)  # type: ignore[method-assign]
    phone, attention = ScriptedPhoneAnalyzer(), ScriptedAttentionAnalyzer()
    phone.health, attention.health = ok(Component.PHONE), ok(Component.ATTENTION)  # type: ignore[method-assign]
    phone.start_session = lambda *a: None  # type: ignore[method-assign]
    attention.start_session = lambda *a: None  # type: ignore[method-assign]
    overrides = {
        "capture": lambda s: _FakeLiveCapture(fps=30),
        "phone": lambda s: phone,
        "attention": lambda s: attention,
        "fusion": _RecordingEngine,
        "evidence": lambda s: store,
    }
    app = create_app(_settings(tmp_path), TOKEN, module_overrides=overrides)  # every module explicitly provided
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        caps = {
            "reported_at": "2026-10-08T09:00:00Z",
            "platform": "test",
            "shell_version": "0",
            "exam_mode_supported": True,
            "items": [{"action": "shortcut_ctrl_c", "status": "blocked", "mechanism": "test"}],
        }
        assert c.put("/v1/environment/capabilities", json=caps).status_code == 200
        sid = _create(c, mode="live")
        report = c.post(f"/v1/sessions/{sid}/preflight").json()
        assert all(ch["status"] == "pass" for ch in report["checks"] if ch["required"]), [(ch["check_id"], ch["status"], ch["message_code"]) for ch in report["checks"]]
        c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "t"})
        assert c.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any(isinstance(o, AttentionObservation) for o in _RecordingEngine.consumed):
            time.sleep(0.05)
        assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
        assert any(isinstance(o, AttentionObservation) for o in _RecordingEngine.consumed)
        assert all(o.session_id == sid for o in _RecordingEngine.consumed)
        # incident closed by finish() is recorded, not lost
        assert store._incidents[sid][f"inc-{sid}-final"].state == IncidentState.CLOSED


def test_bootstrap_parts_satisfy_protocols():
    assert isinstance(SyntheticCaptureService(), itf.CaptureService)
    assert isinstance(ScriptedPhoneAnalyzer(), itf.FrameAnalyzer)
    assert isinstance(ScriptedAttentionAnalyzer(), itf.AttentionAnalyzer)
    assert isinstance(BootstrapIncidentEngine("s", SourceMode.SYNTHETIC), itf.IncidentEngine)
    assert isinstance(MemoryEvidenceStore(), itf.EvidenceStore)
