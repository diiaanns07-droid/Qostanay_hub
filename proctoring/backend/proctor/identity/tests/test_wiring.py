"""A13 wiring through A01 (app.py MODULES + session.py): identity is a capture consumer, gets exam_started()
on RUNNING, its observations reach fusion, end_session() drops the reference. Stub face engine, fake capture."""

from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.bootstrap.engine import BootstrapIncidentEngine
from proctor.bootstrap.memory_store import MemoryEvidenceStore
from proctor.bootstrap.synthetic import ScriptedAttentionAnalyzer, ScriptedPhoneAnalyzer, SyntheticCaptureService
from proctor.identity import IdentityAnalyzer
from proctor.identity.face import FaceEngine
from proctor.settings import Settings
from proctor_contracts.v1 import Component, Health, HealthStatus, IdentityObservation, SignalState, SourceConfig, SourceMode

TOKEN = "t" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}


class _FakeLiveCapture(SyntheticCaptureService):
    def open(self, session_id, source, clock):  # type: ignore[override]
        super().open(session_id, SourceConfig(mode=SourceMode.SYNTHETIC), clock)


class _Recorder(BootstrapIncidentEngine):
    consumed: list = []

    def __init__(self, session_id, source_mode, settings):  # A05 factory signature; records only
        self.session_id = session_id

    def consume(self, observation):
        _Recorder.consumed.append(observation)
        return []

    def advance(self, t):
        return []

    def set_paused(self, paused, t):
        return []

    def finish(self, t, reason):
        return []


class _OneFace:
    def setInputSize(self, size):  # noqa: N802
        pass

    def detect(self, image):
        return 1, np.array([[200, 120, 180, 180, 250, 190, 330, 190, 290, 230, 260, 270, 320, 270, 0.97]], np.float32)


class _SamePerson:
    def alignCrop(self, image, row):  # noqa: N802
        return np.zeros((112, 112, 3), np.uint8)

    def feature(self, aligned):
        v = np.random.default_rng(1).standard_normal(128).astype(np.float32)
        return v.reshape(1, 128) + 0.01 * np.random.default_rng().standard_normal((1, 128)).astype(np.float32)


def _ok(component):
    return lambda: Health(component=component, status=HealthStatus.OK, code="ok")


@pytest.fixture()
def wired(tmp_path):
    _Recorder.consumed = []
    settings = Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "models", replay_dir=tmp_path / "replay",
                        exam_path=tmp_path / "missing.json", fusion_tick_ms=50.0)
    store = MemoryEvidenceStore()
    store.health = _ok(Component.EVIDENCE)  # type: ignore[method-assign]
    phone, attention = ScriptedPhoneAnalyzer(), ScriptedAttentionAnalyzer()
    phone.health, attention.health = _ok(Component.PHONE), _ok(Component.ATTENTION)  # type: ignore[method-assign]
    phone.start_session = lambda *a: None  # type: ignore[method-assign]
    attention.start_session = lambda *a: None  # type: ignore[method-assign]
    identity = IdentityAnalyzer(settings, engine=FaceEngine(_OneFace(), _SamePerson()))
    overrides = {
        "capture": lambda s: _FakeLiveCapture(fps=30),
        "phone": lambda s: phone,
        "attention": lambda s: attention,
        "identity": lambda s: identity,
        "fusion": _Recorder,
        "evidence": lambda s: store,
    }
    app = create_app(settings, TOKEN, module_overrides=overrides)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        caps = {"reported_at": "2026-10-08T09:00:00Z", "platform": "test", "shell_version": "0", "exam_mode_supported": True,
                "items": [{"action": "shortcut_ctrl_c", "status": "blocked", "mechanism": "test"}]}
        assert client.put("/v1/environment/capabilities", json=caps).status_code == 200
        yield client, identity


def _identity_obs():
    return [o for o in _Recorder.consumed if isinstance(o, IdentityObservation)]


def test_identity_enrolls_after_running_and_reaches_fusion(wired):
    client, identity = wired
    health = {c["component"]: c for c in client.get("/v1/health").json()["components"]}
    assert health["identity"]["code"] == "model_loaded"
    r = client.post("/v1/sessions", json={"source": {"mode": "live"}, "exam_id": "demo-exam-1", "consent": CONSENT})
    sid = r.json()["session_id"]
    report = client.post(f"/v1/sessions/{sid}/preflight").json()
    assert report["ready"] is True
    client.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "t"})
    time.sleep(0.6)  # frames before RUNNING: no reference may be taken from them
    assert identity.health().details["enrolled"] is False
    assert client.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not any(o.enrolled for o in _identity_obs()):
        time.sleep(0.1)
    obs = _identity_obs()
    assert obs and all(o.session_id == sid for o in obs)
    enrolled = [o for o in obs if o.enrolled]
    assert enrolled and enrolled[0].same_person == SignalState.PRESENT
    first = obs[0]
    assert first.reasons in (["enrolling"], ["exam_not_started"]) and first.same_person == SignalState.UNKNOWN
    assert client.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
    assert identity.health().details["enrolled"] is False  # end_session dropped the reference


def test_synthetic_session_has_no_identity_consumer(wired):
    client, identity = wired
    r = client.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT})
    sid = r.json()["session_id"]
    client.post(f"/v1/sessions/{sid}/preflight")
    client.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "t"})
    assert client.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
    time.sleep(1.5)
    assert client.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
    assert _identity_obs() == []
