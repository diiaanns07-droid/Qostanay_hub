"""Data survives restart (graceful and kill -9); audio signaling relay bound to an audio session."""

from __future__ import annotations

import time

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
def test_audio_signaling_is_bound_to_an_acked_audio_session(server, teacher, session, students):
    ts = TeacherStream(server, teacher.cookies.get("qorgau_teacher"))
    try:
        ts.wait_for(lambda x: x["type"] == "snapshot")
        s = students()
        sid = s.hello(join_code=session["join_code"])["student_id"]
        ts.send({"type": "audio_signal", "student_id": sid, "command_id": "cmd-none", "sdp": "v=0"})
        assert ts.wait_for(lambda x: x["type"] == "error")["code"] == "no_audio_session"
        start = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "audio_start", "payload": {"direction": "listen"}}).json()["command_id"]
        assert teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "audio_start", "payload": {"direction": "both"}}).status_code == 409
        au = ts.wait_for(lambda x: x["type"] == "audio_session_update" and x["audio"]["state"] == "requested")["audio"]
        s.wait_for(lambda x: x.get("command_id") == start)
        s.send("ack", command_id=start, ok=True)
        ts.wait_for(lambda x: x["type"] == "audio_session_update" and x["audio"]["state"] == "active")
        ts.send({"type": "audio_signal", "student_id": sid, "command_id": start, "sdp": "v=0 offer"})
        got = s.wait_for(lambda x: x["type"] == "audio_signal")
        assert got["sdp"] == "v=0 offer" and got["audio_session_id"] == au["audio_session_id"]
        s.send("audio_signal", command_id=start, sdp="v=0 answer")
        back = ts.wait_for(lambda x: x["type"] == "audio_signal" and x["student_id"] == sid)
        assert back["sdp"] == "v=0 answer"
        stop = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "audio_stop"}).json()["command_id"]
        s.wait_for(lambda x: x.get("command_id") == stop)
        s.send("ack", command_id=stop, ok=True)
        ended = ts.wait_for(lambda x: x["type"] == "audio_session_update" and x["audio"]["state"] == "ended")
        assert ended["audio"]["end_reason"] == "stopped"
        assert teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "audio_stop"}).status_code == 409
    finally:
        ts.close()


def test_audio_ends_when_student_goes_offline(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    start = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "audio_start", "payload": {"direction": "talk"}}).json()["command_id"]
    s.wait_for(lambda x: x.get("command_id") == start)
    s.send("ack", command_id=start, ok=True)
    assert wait_until(lambda: [a for a in teacher.get(f"/api/teacher/students/{sid}/audio").json() if a["state"] == "active"])
    s.close()
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}/audio").json()[-1]["state"] == "ended")
    assert teacher.get(f"/api/teacher/students/{sid}/audio").json()[-1]["end_reason"] == "student_offline"
