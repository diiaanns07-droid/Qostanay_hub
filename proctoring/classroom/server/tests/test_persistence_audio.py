"""Data survives restart (graceful and kill -9); audio signaling relay bound to an audio session."""

from __future__ import annotations

import time
import json

from websockets.sync.client import connect

from classroom.server.tests.harness import ServerProcess, TeacherStream, wait_until


def _scenario_before_restart(srv: ServerProcess) -> dict:
    t = srv.teacher()
    sess = t.post("/api/teacher/session", json={"title": "Физика", "mode": "app", "allowed_apps": ["exam.exe"]}).json()
    s = srv.student()
    w = s.hello(join_code=sess["join_code"], student_label="Данияр")
    sid = w["student_id"]
    s.status(zone="yellow", zone_reasons_ru=["Телефон виден"])
    s.incident(1, "inc-1")
    s.incident(2, "inc-1", state="closed")
    s.incident(3, "inc-2")
    done = t.post(f"/api/teacher/students/{sid}/commands", json={"kind": "lock", "payload": {"reason_ru": "Проверка"}}).json()["command_id"]
    s.wait_for(lambda x: x.get("command_id") == done)
    s.send("ack", command_id=done, ok=True)
    assert wait_until(lambda: t.get(f"/api/teacher/commands/{done}").json()["status"] == "succeeded")
    assert wait_until(lambda: len(t.get(f"/api/teacher/students/{sid}/events").json()) == 3)
    s.close()
    assert wait_until(lambda: t.get(f"/api/teacher/students/{sid}").json()["connected"] is False)
    pending = t.post(f"/api/teacher/students/{sid}/commands", json={"kind": "unlock", "ttl_ms": 600_000}).json()["command_id"]
    t.close()
    return {"session": sess, "welcome": w, "sid": sid, "done": done, "pending": pending}


def _check_after_restart(srv: ServerProcess, before: dict) -> None:
    sid = before["sid"]
    t = srv.teacher()  # new PIN after restart: teacher logs in again
    try:
        session = t.get("/api/teacher/session").json()
        assert session["session_id"] == before["session"]["session_id"] and session["join_code"] == before["session"]["join_code"]
        card = t.get(f"/api/teacher/students/{sid}").json()
        assert card["connected"] is False and card["student_label"] == "Данияр"
        assert card["zone_reported"] == "yellow" and card["zone"] == "grey"  # offline -> grey, report kept
        assert len(t.get(f"/api/teacher/students/{sid}/events").json()) == 3
        incs = {i["incident_id"]: i["state"] for i in t.get(f"/api/teacher/students/{sid}/incidents").json()}
        assert incs == {"inc-1": "closed", "inc-2": "open"}
        assert t.get(f"/api/teacher/commands/{before['done']}").json()["status"] == "succeeded"
        assert t.get(f"/api/teacher/commands/{before['pending']}").json()["status"] == "queued"
        s = srv.student()
        try:
            w = s.hello(resume_token=before["welcome"]["resume_token"])
            assert w["type"] == "welcome" and w["student_id"] == sid and w["resumed"] is True
            got = s.wait_for(lambda x: x["type"] == "command")
            assert got["command_id"] == before["pending"]  # queued before the restart, delivered after it
            s.incident(1, "inc-1")  # re-sent from the client's offline queue: still a duplicate
            s.incident(4, "inc-3")
            assert wait_until(lambda: len(t.get(f"/api/teacher/students/{sid}/events").json()) == 4)
            time.sleep(0.3)
            assert len(t.get(f"/api/teacher/students/{sid}/events").json()) == 4
        finally:
            s.close()
    finally:
        t.close()


def test_state_survives_graceful_restart(tmp_path):
    srv = ServerProcess(tmp_path)
    before = _scenario_before_restart(srv)
    assert srv.stop() == 0
    srv2 = ServerProcess(tmp_path)
    try:
        assert srv2.pin  # a fresh PIN is printed every start
        _check_after_restart(srv2, before)
    finally:
        assert srv2.stop() == 0


def test_state_survives_crash(tmp_path):
    srv = ServerProcess(tmp_path)
    before = _scenario_before_restart(srv)
    srv.kill()  # SIGKILL / TerminateProcess: no shutdown code runs
    srv2 = ServerProcess(tmp_path)
    try:
        _check_after_restart(srv2, before)
    finally:
        assert srv2.stop() == 0


# ------------------------------------------------------------------------------------------------ audio
def test_legacy_audio_command_cannot_create_a_session_or_relay(server, teacher, session, students):
    ts = TeacherStream(server, teacher.cookies.get("qorgau_teacher"))
    try:
        ts.wait_for(lambda x: x["type"] == "snapshot")
        s = students()
        sid = s.hello(join_code=session["join_code"])["student_id"]
        ts.send({"type": "audio_signal", "student_id": sid, "command_id": "cmd-none", "sdp": "v=0"})
        assert ts.wait_for(lambda x: x["type"] == "error")["code"] == "no_audio_session"
        for kind, payload in (("audio_start", {"direction": "listen"}), ("audio_stop", {})):
            response = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": kind, "payload": payload})
            assert response.status_code == 422
            assert "audio_extension_required" in response.text
        assert teacher.get(f"/api/teacher/students/{sid}/audio").json() == []
        assert not s.of_type("command"), "Legacy requests must never reach the student's capture path"
    finally:
        ts.close()


def test_audio_ends_when_student_goes_offline(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"], audio_protocol="qorgau.class.audio.v1")["student_id"]
    with connect(f"ws://127.0.0.1:{server.port}/api/teacher/audio/ws",
                 additional_headers={"Cookie": f"qorgau_teacher={teacher.cookies.get('qorgau_teacher')}"}) as audio:
        def receive_state(state):
            for _ in range(10):
                msg = json.loads(audio.recv(timeout=5))
                if msg.get("type") == "audio_state" and msg.get("state") == state:
                    return msg
            raise AssertionError(f"audio state not received: {state}")

        audio.send(json.dumps({"type": "audio_request", "student_id": sid, "listen": False, "talk": True}))
        requested = receive_state("requested")
        start = s.wait_for(lambda x: x["type"] == "command" and x.get("kind") == "audio_start")
        s.send("ack", audio_protocol="qorgau.class.audio.v1", command_id=start["command_id"], ok=True)
        accepted = receive_state("accepted")
        assert accepted["audio_session_id"] == requested["audio_session_id"]  # accepted is not actual media
        s.close()
        ended = receive_state("ended")
        assert ended["audio_session_id"] == requested["audio_session_id"]
        assert ended["reason"] == "student_disconnected"
