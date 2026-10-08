"""Contract roundtrip: warning is visible, start allowed, metadata event accepted."""
import pytest
from fastapi.testclient import TestClient
from proctor.app import MODULES, create_app
from proctor.settings import Settings


@pytest.mark.parametrize("state", ["detected", "unknown"])
def test_vm_warning_allows_preflight_and_start(tmp_path, state):
    settings = Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "models",
                        replay_dir=tmp_path / "replay", exam_path=tmp_path / "missing.json")
    token = "v" * 48
    app = create_app(settings, token, module_overrides={key: None for key in MODULES})
    with TestClient(app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + token}) as client:
        caps = dict(platform="test", shell_version="test", reported_at="2026-10-08T00:00:00Z",
                    exam_mode_supported=True, items=[
                        dict(action="shortcut_ctrl_c", status="blocked", mechanism="electron.before_input_event"),
                        dict(action="exam_mode_engaged", status="unverified" if state == "unknown" else "detected_only",
                             mechanism="native.vm_check." + state, note_ru="VM advisory " + state)])
        assert client.put("/v1/environment/capabilities", json=caps).status_code == 200
        created = client.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1",
            "consent": {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}})
        assert created.status_code == 201, created.text
        sid = created.json()["session_id"]
        report = client.post(f"/v1/sessions/{sid}/preflight").json()
        assert report["ready"] is True
        env = next(c for c in report["checks"] if c["check_id"] == "environment_protection")
        assert env["status"] == "warn" and env["details"]["vm_state"] == state
        assert client.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "test"}).status_code == 200
        assert client.post(f"/v1/sessions/{sid}/start").status_code == 200
        batch = {"session_id": sid, "events": [{"action": "exam_mode_engaged", "enforcement": "allowed",
            "mechanism": "native.vm_check." + state, "scope": "os_session", "client_seq": 101,
            "client_wall_time": "2026-10-08T09:00:00Z", "detail": {"shortcut": "Hyper-V" if state == "detected" else "unknown"}}]}
        result = client.post(f"/v1/sessions/{sid}/environment/events", json=batch)
        assert result.status_code == 200, result.text
        assert result.json()["accepted"] == 1
        repeat = client.post(f"/v1/sessions/{sid}/environment/events", json=batch)
        assert repeat.json()["duplicates"] == 1
        assert client.post(f"/v1/sessions/{sid}/finish").status_code == 200
