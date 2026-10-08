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
# Live audio goes ONLY through the negotiated T05 extension (classroom/server/audio_feature.py, qorgau.class.audio.v1):
# the legacy REST audio_start is refused so an ACK can never claim live audio (test_audio_extension.py). These two tests
# keep their original guarantees on that path: signaling is bound to an acked audio session; offline ends the audio.
AUDIO = "qorgau.class.audio.v1"


class AudioTeacher(TeacherStream):
    """The teacher's audio socket /api/teacher/audio/ws (same reader thread as TeacherStream)."""

    def __init__(self, server: ServerProcess, cookie: str | None):
        import threading

        from websockets.sync.client import connect

        self._cm = connect(f"ws://127.0.0.1:{server.port}/api/teacher/audio/ws",
                           additional_headers={"Cookie": f"qorgau_teacher={cookie}"}, open_timeout=10)
        self.ws = self._cm.__enter__()
        self.messages, self.closed = [], None
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()


def _accepted_audio(teacher, server, session, students, listen=True):
    s = students()
    sid = s.hello(join_code=session["join_code"], audio_protocol=AUDIO)["student_id"]
    at = AudioTeacher(server, teacher.cookies.get("qorgau_teacher"))
    at.send({"type": "audio_request", "student_id": sid, "listen": listen, "talk": not listen})
    assert at.wait_for(lambda x: x["type"] == "audio_state")["state"] == "requested"
    start = s.wait_for(lambda x: x["type"] == "command" and x.get("kind") == "audio_start")
    assert start["audio_protocol"] == AUDIO
    asid = start["payload"]["audio_session_id"]
    s.send("ack", audio_protocol=AUDIO, command_id=start["command_id"], ok=True)
    assert at.wait_for(lambda x: x["type"] == "audio_state" and x["state"] == "accepted")["audio_session_id"] == asid  # ACK is not media
    return s, sid, at, asid


def test_audio_signaling_is_bound_to_an_acked_audio_session(server, teacher, session, students):
    s, sid, at, asid = _accepted_audio(teacher, server, session, students)
    try:
        legacy = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "audio_start", "payload": {"direction": "listen"}})
        assert legacy.status_code == 422 and legacy.json()["error"]["code"] == "audio_extension_required"
        at.send({"type": "audio_signal", "audio_session_id": "as-0000000000000000", "kind": "offer", "sdp": "v=0"})
        assert at.wait_for(lambda x: x["type"] == "audio_error")["code"] == "session_mismatch"
        at.send({"type": "audio_signal", "audio_session_id": asid, "kind": "offer", "sdp": "v=0 offer"})
        got = s.wait_for(lambda x: x["type"] == "audio_signal")
        assert got["kind"] == "offer" and got["audio_session_id"] == asid and got["sdp"].startswith("v=0 offer")
        s.send("audio_signal", audio_protocol=AUDIO, audio_session_id=asid, kind="answer", sdp="v=0 answer")
        back = at.wait_for(lambda x: x["type"] == "audio_signal" and x.get("kind") == "answer")
        assert back["audio_session_id"] == asid and back["sdp"].startswith("v=0 answer")
        at.send({"type": "audio_stop", "audio_session_id": asid})
        stop = s.wait_for(lambda x: x["type"] == "command" and x.get("kind") == "audio_stop")
        assert stop["payload"]["audio_session_id"] == asid
        ended = at.wait_for(lambda x: x["type"] == "audio_state" and x["state"] == "ended")
        assert ended["reason"] == "teacher_stop"
        before = len([m for m in s.snapshot() if m["type"] == "audio_signal"])
        at.send({"type": "audio_signal", "audio_session_id": asid, "kind": "offer", "sdp": "v=0 late"})
        assert at.wait_for(lambda x: x["type"] == "audio_error" and x.get("audio_session_id") == asid)["code"] == "session_mismatch"
        assert len([m for m in s.snapshot() if m["type"] == "audio_signal"]) == before  # nothing relayed after the end
    finally:
        at.close()


def test_audio_ends_when_student_goes_offline(server, teacher, session, students):
    s, sid, at, asid = _accepted_audio(teacher, server, session, students, listen=False)
    try:
        s.close()
        ended = at.wait_for(lambda x: x["type"] == "audio_state" and x["state"] == "ended", timeout=8)
        assert ended["audio_session_id"] == asid and ended["reason"] == "student_disconnected"
    finally:
        at.close()
