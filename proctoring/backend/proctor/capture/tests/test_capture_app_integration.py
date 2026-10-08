"""The REAL proctor.capture module inside A01's composition root (create_app, HTTP API).

No capture override: ModuleRegistry imports proctor.capture.create_capture_service itself.
Phone/attention/fusion/storage are A01's labelled test doubles, so nothing here measures CV.
"""

from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.bootstrap.memory_store import MemoryEvidenceStore
from proctor.bootstrap.synthetic import ScriptedAttentionAnalyzer, ScriptedPhoneAnalyzer
from proctor.capture import FrameCaptureService
from proctor.capture.tests.helpers import sha256, wait_until, write_manifest, write_video
from proctor.settings import Settings
from proctor_contracts.v1 import Component, Health, HealthStatus, PreviewFrameMeta, SourceMode

TOKEN = "a02" * 16
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}


def _settings(tmp_path: Path) -> Settings:
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    return Settings(data_dir=tmp_path / "data", replay_dir=replay_dir, exam_path=tmp_path / "none.json", synthetic_fps=30.0, fusion_tick_ms=50.0)


def _create(c, **source) -> str:
    r = c.post("/v1/sessions", json={"source": source, "exam_id": "demo-exam-1", "consent": CONSENT})
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _checks(report) -> dict:
    return {ch["check_id"]: ch for ch in report["checks"]}


class _RecordingEngine:
    """Minimal IncidentEngine (A05 signature) that records what fusion receives."""

    consumed: list = []
    rule_version = "test"
    config_version = "test"

    def __init__(self, session_id, source_mode, settings):
        self.session_id = session_id

    def consume(self, observation):
        _RecordingEngine.consumed.append(observation)
        return []

    def advance(self, t):
        return []

    def set_paused(self, paused, t):
        return []

    def finish(self, t, reason):
        return []

    def config_snapshot(self):
        return {}


def _ok(component):
    return lambda: Health(component=component, status=HealthStatus.OK, code="ok")


class _PassThroughAnalyzer(ScriptedPhoneAnalyzer):
    """Scripted output, but accepts replay sessions (test double for A03, not CV)."""

    def start_session(self, session_id, source_mode):
        pass


class _PassThroughAttention(ScriptedAttentionAnalyzer):
    def start_session(self, session_id, source_mode):
        pass


def test_synthetic_session_uses_real_capture_module(tmp_path):
    app = create_app(_settings(tmp_path), TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        registry = app.state.registry
        assert isinstance(registry.loaded["capture"].impl, FrameCaptureService)  # not the bootstrap service
        health = {h["component"]: h for h in c.get("/v1/health").json()["components"]}
        assert health["capture"]["code"] == "idle"
        for round_ in range(2):
            sid = _create(c, mode="synthetic")
            report = c.post(f"/v1/sessions/{sid}/preflight").json()
            cam = _checks(report)["camera"]
            assert report["ready"] is True and cam["status"] == "pass" and cam["details"]["impl"] == "module"
            assert _checks(report)["lighting"]["status"] == "pass"
            c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "test"})
            assert c.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
            time.sleep(0.6)
            m = c.get(f"/v1/sessions/{sid}/metrics").json()
            names = {x["name"] for x in m["consumers"]}
            assert {"phone", "attention", "preview"} <= names and m["frames_captured"] > 5
            r = c.get(f"/v1/sessions/{sid}/preview.jpg")
            assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
            meta = PreviewFrameMeta.model_validate_json(r.headers["X-Qorgau-Preview-Meta"])
            assert meta.session_id == sid and meta.source_mode == SourceMode.SYNTHETIC and meta.byte_length == len(r.content)
            assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
            assert registry.loaded["capture"].impl.health().code == "closed"
            assert registry.loaded["capture"].impl.leaked_runs() == 0


def test_preview_websocket_binary_frames(tmp_path):
    app = create_app(_settings(tmp_path), TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        sid = _create(c, mode="synthetic")
        assert c.post(f"/v1/sessions/{sid}/preflight").json()["ready"] is True
        with c.websocket_connect(f"/v1/preview?session_id={sid}", headers={**AUTH, "Host": "127.0.0.1"}) as ws:
            seen = []
            for _ in range(3):
                data = ws.receive_bytes()
                (n,) = struct.unpack(">I", data[:4])
                meta = PreviewFrameMeta.model_validate_json(data[4 : 4 + n])
                assert data[4 + n : 4 + n + 2] == b"\xff\xd8" and len(data) - 4 - n == meta.byte_length
                seen.append(meta.frame_id)
            assert seen == sorted(set(seen))
        c.post(f"/v1/sessions/{sid}/abort", json={"reason": "test"})


def test_replay_goes_through_the_same_pipeline(tmp_path):
    settings = _settings(tmp_path)
    media = settings.replay_dir / "media"
    media.mkdir()
    video = write_video(media / "demo.avi", n=40, fps=20.0)
    write_manifest(settings.replay_dir, "demo_clip", {"kind": "video", "path": "media/demo.avi", "sha256": sha256(video)}, loop=True)
    _RecordingEngine.consumed = []
    store = MemoryEvidenceStore()
    store.health = _ok(Component.EVIDENCE)  # type: ignore[method-assign]
    phone, attention = _PassThroughAnalyzer(), _PassThroughAttention()
    phone.health, attention.health = _ok(Component.PHONE), _ok(Component.ATTENTION)  # type: ignore[method-assign]
    overrides = {"phone": lambda s: phone, "attention": lambda s: attention, "fusion": _RecordingEngine, "evidence": lambda s: store}
    app = create_app(settings, TOKEN, module_overrides=overrides)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        sid = _create(c, mode="replay", replay_id="demo_clip")
        report = c.post(f"/v1/sessions/{sid}/preflight").json()
        cam = _checks(report)["camera"]
        assert cam["status"] == "pass" and cam["message_code"] == "replay_source_ok", cam
        assert report["ready"] is True, [(ch["check_id"], ch["status"]) for ch in report["checks"]]
        c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "test"})
        assert c.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
        assert wait_until(lambda: len([o for o in _RecordingEngine.consumed if getattr(o, "frame_id", None) is not None]) > 10, timeout=5)
        r = c.get(f"/v1/sessions/{sid}/preview.jpg")
        meta = PreviewFrameMeta.model_validate_json(r.headers["X-Qorgau-Preview-Meta"])
        assert meta.source_mode == SourceMode.REPLAY
        assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
    frame_obs = [o for o in _RecordingEngine.consumed if getattr(o, "frame_id", None) is not None]
    assert frame_obs and all(o.source_mode == SourceMode.REPLAY and o.session_id == sid for o in frame_obs)
    health_obs = [o for o in _RecordingEngine.consumed if o.kind == "health"]
    assert all(o.source_mode == SourceMode.REPLAY for o in health_obs)


def test_invalid_replay_fails_preflight_honestly(tmp_path):
    settings = _settings(tmp_path)
    (settings.replay_dir / "broken.json").write_text(json.dumps({"format": "qorgau.replay.v1"}))
    app = create_app(settings, TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        sid = _create(c, mode="replay", replay_id="broken")
        report = c.post(f"/v1/sessions/{sid}/preflight").json()
        cam = _checks(report)["camera"]
        assert report["ready"] is False and cam["status"] == "fail" and cam["message_code"] == "replay_invalid"
        health = {h["component"]: h for h in c.get("/v1/health").json()["components"]}
        assert health["capture"]["status"] == "unavailable" and health["capture"]["code"] == "manifest_invalid"
        c.post(f"/v1/sessions/{sid}/abort", json={"reason": "test"})


@pytest.mark.skipif(any(Path(f"/dev/video{i}").exists() for i in range(4)) or os.name == "nt", reason="a camera may be present")
def test_live_without_camera_fails_preflight_and_never_falls_back(tmp_path):
    app = create_app(_settings(tmp_path), TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        sid = _create(c, mode="live")
        report = c.post(f"/v1/sessions/{sid}/preflight").json()
        cam = _checks(report)["camera"]
        assert report["ready"] is False and cam["status"] == "fail" and cam["message_code"] == "camera_unavailable"
        capture = {h["component"]: h for h in c.get("/v1/health").json()["components"]}["capture"]
        assert capture["code"] == "camera_not_found" and capture["status"] == "unavailable"
        assert app.state.registry.loaded["capture"].impl.metrics().frames_captured == 0
        c.post(f"/v1/sessions/{sid}/abort", json={"reason": "test"})
