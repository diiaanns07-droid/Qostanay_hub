"""Real C1 WebSockets: extension negotiation, state evidence, sender isolation, logout."""
from fastapi.testclient import TestClient
from classroom.server.app import create_app
from classroom.server.config import ServerConfig
from classroom.server.core import envelope


def receive(ws, kind):
    for _ in range(20):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    raise AssertionError(kind)


def test_negotiated_audio_transport(tmp_path):
    app = create_app(ServerConfig(data_dir=tmp_path, teacher_pin="123456", ping_interval_s=60))
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/teacher/login", json={"pin": "123456"}).status_code == 200
        session = client.post("/api/teacher/session", json={"title": "Audio test", "mode": "app", "allowed_apps": ["exam.exe"]}).json()
        with client.websocket_connect("/ws/student") as a, client.websocket_connect("/ws/student") as b:
            for socket, label in ((a, "A"), (b, "B")):
                socket.send_json(envelope(type="hello", protocol="qorgau.class.v1", client_id=label, student_label=label,
                                          app_version="test", join_code=session["join_code"], audio_protocol="qorgau.class.audio.v1"))
            aw, bw = receive(a, "welcome"), receive(b, "welcome")
            with client.websocket_connect("ws://127.0.0.1:8765/api/teacher/audio/ws") as teacher:
                teacher.send_json({"type": "audio_request", "student_id": aw["student_id"], "listen": True, "talk": False})
                requested = receive(teacher, "audio_state")
                assert requested["state"] == "requested"
                start = receive(a, "command")
                sid = start["payload"]["audio_session_id"]
                a.send_json(envelope(type="ack", audio_protocol="qorgau.class.audio.v1", command_id=start["command_id"], ok=True))
                assert receive(teacher, "audio_state")["state"] == "accepted"  # ACK is not media
                b.send_json(envelope(type="audio_signal", audio_protocol="qorgau.class.audio.v1", audio_session_id=sid, kind="answer", sdp="v=0\r\n"))
                assert receive(b, "audio_error")["code"] == "session_mismatch"
                teacher.send_json({"type": "audio_signal", "audio_session_id": sid, "kind": "offer", "sdp": "v=0\r\n"})
                assert receive(a, "audio_signal")["kind"] == "offer"
                a.send_json(envelope(type="audio_signal", audio_protocol="qorgau.class.audio.v1", audio_session_id=sid, kind="answer", sdp="v=0\r\n"))
                assert receive(teacher, "audio_signal")["kind"] == "answer"
                # Logout revokes even an idle audio socket within its auth heartbeat.
                client.post("/api/teacher/logout")
                stop = receive(a, "command")
                assert stop["kind"] == "audio_stop" and stop["payload"]["reason"] == "teacher_auth_lost"


def test_legacy_command_cannot_claim_live_audio(tmp_path):
    app = create_app(ServerConfig(data_dir=tmp_path, teacher_pin="123456"))
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client:
        client.post("/api/teacher/login", json={"pin": "123456"})
        import pytest
        from classroom.server.core import ClassroomError
        from classroom.contracts.models import CommandKind
        with pytest.raises(ClassroomError) as caught:
            app.state.core.submit_command("st-legacy", CommandKind.AUDIO_START, {"direction": "listen"}, issued_by="test")
        assert caught.value.code == "audio_extension_required"


def test_panel_audio_module_is_served_as_javascript(tmp_path):
    """T02 panel loads /api/teacher/audio/assets/teacher/class-panel-module.js (class-panel/src/liveModules.js)."""
    app = create_app(ServerConfig(data_dir=tmp_path, teacher_pin="123456"))
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/teacher/audio/assets/teacher/class-panel-module.js").status_code in (401, 403)  # teacher only
        client.post("/api/teacher/login", json={"pin": "123456"})
        for folder, name in (("teacher", "class-panel-module.js"), ("teacher", "teacher-audio.js"), ("teacher", "teacher-signaling.js"),
                             ("teacher", "audio-panel.js"), ("shared", "media-errors.js")):
            r = client.get(f"/api/teacher/audio/assets/{folder}/{name}")
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript"), (name, r.status_code, r.headers.get("content-type"))
        assert client.get("/api/teacher/audio/assets/teacher/audio.css").headers["content-type"].startswith("text/css")
        assert client.get("/api/teacher/audio/assets/teacher/index.html").status_code == 404  # still an allowlist
        assert client.get("/api/teacher/audio/assets/shared/student-endpoint.js").status_code == 404
