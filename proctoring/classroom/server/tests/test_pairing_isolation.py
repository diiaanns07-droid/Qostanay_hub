"""Registration, secure pairing and client isolation (real server process)."""

from __future__ import annotations

import re
import time

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from classroom.contracts import models as m
from classroom.server.tests.harness import ServerProcess, TeacherStream, wait_until


def test_join_with_code_gives_stable_identity_and_card(server, teacher, session, students):
    s = students()
    w = s.hello(join_code=session["join_code"], student_label="Әлия", computer_name="PC-07")
    assert w["type"] == "welcome"
    welcome = m.Welcome.model_validate(w)
    assert re.fullmatch(r"[0-9a-f]{64}", welcome.resume_token) and welcome.resumed is False
    assert welcome.exam.mode == m.ExamMode.URL and welcome.exam.allowed_urls == ["https://exam.example/*"]
    cards = [m.StudentCard.model_validate(c) for c in teacher.get("/api/teacher/students").json()]
    card = next(c for c in cards if c.student.student_id == welcome.student_id)
    assert card.connected and card.student.student_label == "Әлия" and card.student.origin == m.DataOrigin.REAL
    assert card.status.zone == m.Zone.GREY and card.status.stale  # no status yet = not enough data, not "all clear"


def test_wrong_code_rejected_then_rate_limited_per_ip(server, session, students):
    for _ in range(5):
        s = students()
        r = s.hello(join_code="000000" if session["join_code"] != "000000" else "111111")
        assert r["type"] == "error" and r["code"] == "join_rejected"
        assert s.wait_closed().rcvd.code == 4403
    s = students()
    r = s.hello(join_code=session["join_code"])  # even the right code is refused while blocked
    assert r["code"] == "join_rate_limited" and s.wait_closed().rcvd.code == 4429
    time.sleep(1.6)  # QORGAU_CLASS_JOIN_BLOCK_S=1.5 in tests (30 s in production, v1 §2.5)
    assert students().hello(join_code=session["join_code"])["type"] == "welcome"


def test_credentials_only_in_hello_never_in_url(server, session, students):
    s = server.student()
    try:
        s.ws  # connect without anything, then send a status first
        s.status()
        msg = s.wait_for(lambda x: x["type"] == "error")
        assert msg["code"] == "bad_hello"
        assert s.wait_closed().rcvd.code == 4400
    finally:
        s.close()
    from classroom.server.tests.harness import StudentClient

    q = StudentClient(f"ws://127.0.0.1:{server.port}/ws/student?join_code={session['join_code']}")
    try:
        q.status()
        assert q.wait_for(lambda x: x["type"] == "error")["code"] == "bad_hello"
    finally:
        q.close()


def test_hello_needs_exactly_one_credential(server, session, students):
    s = students()
    s.send("hello", protocol="qorgau.class.v1", join_code=session["join_code"], resume_token="a" * 64, computer_name="x", student_label="x", app_version="x")
    assert s.wait_for(lambda x: x["type"] == "error")["code"] == "bad_hello"


def test_resume_keeps_student_and_supersedes_old_socket(server, teacher, session, students):
    a = students()
    w = a.hello(join_code=session["join_code"])
    b = students()
    w2 = b.hello(resume_token=w["resume_token"])
    assert w2["type"] == "welcome" and w2["student_id"] == w["student_id"] and w2["resumed"] is True
    closed = a.wait_closed()
    assert closed.rcvd.code == 4409  # the older socket of the same student is closed, not left dangling
    card = teacher.get(f"/api/teacher/students/{w['student_id']}").json()
    assert card["connected"] is True and card["student"]["reconnects"] == 1


def test_bad_resume_token_rejected(server, session, students):
    s = students()
    r = s.hello(resume_token="0" * 64)
    assert r["code"] == "resume_rejected" and s.wait_closed().rcvd.code == 4403


def test_closed_session_blocks_code_and_tokens(server, teacher, session, students):
    s = students()
    w = s.hello(join_code=session["join_code"])
    assert teacher.post("/api/teacher/session/close").status_code == 200
    late = students()
    assert late.hello(join_code=session["join_code"])["code"] == "join_rejected"
    again = students()
    assert again.hello(resume_token=w["resume_token"])["code"] == "resume_rejected"


# ------------------------------------------------------------------------------------- teacher access
def test_teacher_api_needs_pin_cookie(server):
    with httpx.Client(base_url=server.base, timeout=5) as c:
        r = c.get("/api/teacher/students")
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
        assert c.post("/api/teacher/login", json={"pin": "999999" if server.pin != "999999" else "888888"}).status_code == 401


def test_student_token_never_grants_teacher_access(server, session, students):
    s = students()
    token = s.hello(join_code=session["join_code"])["resume_token"]
    with httpx.Client(base_url=server.base, timeout=5) as c:
        for path in ("/api/teacher/students", "/api/teacher/info", "/api/teacher/session"):
            r = c.get(path, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401, path
    with httpx.Client(base_url=server.base, timeout=5, cookies={"qorgau_teacher": token}) as c:
        assert c.get("/api/teacher/students").status_code == 401


def test_teacher_routes_reject_foreign_host_and_origin(server, teacher):
    r = teacher.get("/api/teacher/info", headers={"Host": "evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_host"
    r = teacher.get("/api/teacher/info", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"
    assert teacher.get("/api/teacher/info", headers={"Origin": f"http://127.0.0.1:{server.port}"}).status_code == 200


def test_pin_bruteforce_is_rate_limited(tmp_path):
    srv = ServerProcess(tmp_path)
    try:
        wrong = "000000" if srv.pin != "000000" else "111111"
        with httpx.Client(base_url=srv.base, timeout=5) as c:
            codes = [c.post("/api/teacher/login", json={"pin": wrong}).status_code for _ in range(5)]
            assert codes == [401] * 5
            r = c.post("/api/teacher/login", json={"pin": srv.pin})
            assert r.status_code == 429  # correct PIN refused while blocked
            time.sleep(1.6)
            assert c.post("/api/teacher/login", json={"pin": srv.pin}).status_code == 200
    finally:
        assert srv.stop() == 0


def test_teacher_cookie_is_httponly_strict(server):
    with httpx.Client(base_url=server.base, timeout=5) as c:
        r = c.post("/api/teacher/login", json={"pin": server.pin})
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and server.pin not in cookie


def test_teacher_stream_needs_cookie(server):
    ts = TeacherStream(server, cookie=None)
    try:
        assert wait_until(lambda: ts.closed is not None, 5)
        assert ts.closed.rcvd.code == 4401
    finally:
        ts.close()


# ---------------------------------------------------------------------------------- student isolation
def test_student_cannot_ack_another_students_command(server, teacher, session, students):
    a, b = students(), students()
    wa = a.hello(join_code=session["join_code"])
    b.hello(join_code=session["join_code"])
    cmd = teacher.post(f"/api/teacher/students/{wa['student_id']}/commands", json={"kind": "lock", "payload": {"reason_ru": "Проверка"}}).json()
    a.wait_for(lambda x: x["type"] == "command" and x["command_id"] == cmd["command_id"])
    b.send("ack", command_id=cmd["command_id"], ok=True)  # B tries to confirm A's command
    time.sleep(0.4)
    assert teacher.get(f"/api/teacher/commands/{cmd['command_id']}").json()["status"] == "sent"
    a.send("ack", command_id=cmd["command_id"], ok=True)
    assert wait_until(lambda: teacher.get(f"/api/teacher/commands/{cmd['command_id']}").json()["status"] == "succeeded")


def test_student_messages_are_attributed_to_the_socket_not_to_fields(server, teacher, session, students):
    a, b = students(), students()
    wa = a.hello(join_code=session["join_code"])
    wb = b.hello(join_code=session["join_code"])
    a.incident(1, "inc-spoof", student_id=wb["student_id"])  # a forged student_id field is ignored
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{wa['student_id']}/incidents").json())
    assert teacher.get(f"/api/teacher/students/{wb['student_id']}/incidents").json() == []


def test_student_http_needs_its_own_token(server, session, students):
    s = students()
    token = s.hello(join_code=session["join_code"])["resume_token"]
    with httpx.Client(base_url=server.base, timeout=5) as c:
        assert c.post("/api/student/ping").status_code == 401
        assert c.post("/api/student/ping", headers={"Authorization": "Bearer " + "f" * 64}).status_code == 401
        r = c.post("/api/student/ping", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json()["student_id"]


def test_message_size_limit_and_unknown_types(server, teacher, session, students):
    s = students()
    w = s.hello(join_code=session["join_code"])
    s.send("future_feature", data="x")  # unknown type: ignored, connection stays (v1 §3)
    s.status()
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{w['student_id']}").json()["last_status_at"])
    s.send("status", exam_state="running", camera="ok", monitoring="ok", padding="x" * 300_000)
    with pytest.raises(AssertionError):
        s.wait_for(lambda x: x["type"] == "welcome" and False, 0.5)
    closed_or_error = wait_until(lambda: s.closed is not None or any(x["type"] == "error" for x in s.snapshot()), 3)
    assert closed_or_error, "an oversized message must be rejected (error or close), never processed"
