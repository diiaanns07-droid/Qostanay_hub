from fastapi.testclient import TestClient

from proctor.app import MODULES, create_app
from proctor.settings import Settings
from proctor.fusion.engine import FusionEngine
from proctor_contracts.v1 import SourceMode


def test_session_hooks_and_no_mic_for_synthetic(tmp_path, monkeypatch):
    events = []
    class Monitor:
        def __init__(self, sid, mode, clock, publish):
            events.append(("created", sid))
            self.publish = publish
        def start(self):
            events.append("start")
        def stop(self):
            events.append("stop")
    monkeypatch.setattr("proctor.audio.monitor.AudioMonitor", Monitor)
    app = create_app(Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "models",
                    replay_dir=tmp_path / "replay", exam_path=tmp_path / "missing"), "a" * 48,
                    module_overrides={key: None for key in MODULES})
    with TestClient(app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + "a" * 48}) as c:
        for use_live_hook in (False, True):
            created = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1",
                "consent": {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}})
            sid = created.json()["session_id"]
            assert c.post(f"/v1/sessions/{sid}/preflight").json()["ready"]
            assert c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "test"}).status_code == 200
            runtime = app.state.proctor["manager"].runtime(sid)
            if use_live_hook:
                # Only the microphone hook uses LIVE; camera and API fixtures remain synthetic.
                runtime.mode = SourceMode.LIVE
                runtime.pipeline.engine_factory = FusionEngine
            for action, body in (("start", None), ("pause", {"reason": "test"}), ("resume", None), ("finish", None)):
                assert c.post(f"/v1/sessions/{sid}/{action}", json=body).status_code == 200
            if not use_live_hook:
                assert events == []
            else:
                assert events == [("created", sid), "start", "stop", "start", "stop"]
                assert runtime._audio_monitor.publish.__self__ is runtime
