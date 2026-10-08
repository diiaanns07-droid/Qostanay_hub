"""Real C1 wire -> persisted device status -> public card and teacher stream.

Synthetic WS peers only. A receipt means the peer reports its app overlay, not native OS lockdown.
"""
from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from classroom.contracts.models import Status
from classroom.server.tests.harness import ServerProcess, TeacherStream


def wait_for(fn, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("condition not reached")


def test_legacy_and_strict_receipt_contract():
    base = dict(type="status", v=1, msg_id="m", sent_at="2026-10-08T10:00:00Z", exam_state="running", camera="ok", monitoring="ok", locked=False)
    assert Status.model_validate(base).lock_confirmed is None
    for invalid in ({"lock_confirmed": "true"}, {"lock_requested": 1}, {"lock_scope": "os"}, {"lock_state": "success"}):
        with pytest.raises(ValidationError):
            Status.model_validate({**base, **invalid})


def test_live_status_receipt_lifecycle(server, teacher, session, students):
    peer = students()
    welcome = peer.hello(join_code=session["join_code"])
    sid = welcome["student_id"]
    url = f"/api/teacher/students/{sid}"
    card = lambda: teacher.get(url).json()
    peer.status(locked=False)
    wait_for(lambda: card()["locked"] is False)
    assert card()["lock_confirmed"] is False
    stream = TeacherStream(server, teacher.cookies.get("qorgau_teacher"))
    try:
        peer.status(locked=True, lock_state="applied", lock_confirmed=True, lock_requested=True, lock_scope="app_overlay")
        wait_for(lambda: card()["lock_confirmed"])
        event = stream.wait_for(lambda e: e["type"] == "student_update" and e["student"].get("lock_confirmed"))
        assert event["student"]["lock_scope"] == "app_overlay"
        assert event["student"]["locked"] is True
        peer.status(locked=True, lock_state="requested", lock_confirmed=True, lock_requested=False, lock_scope="app_overlay")
        wait_for(lambda: card()["lock_state"] == "requested")
        assert card()["lock_requested"] is False
        peer.status(locked=True, lock_state="failed", lock_confirmed=True, lock_requested=True, lock_scope="app_overlay")
        wait_for(lambda: card()["lock_state"] == "failed")
        peer.status(locked=False, lock_state="applied", lock_confirmed=True, lock_requested=False, lock_scope="app_overlay")
        wait_for(lambda: card()["lock_confirmed"] and card()["locked"] is False)
        wait_for(lambda: card()["stale"])
        assert card()["lock_confirmed"] is False
        assert card()["lock_state"] == "unconfirmed"
        peer.status(locked=True, lock_state="applied", lock_confirmed=True, lock_requested=True, lock_scope="app_overlay")
        wait_for(lambda: card()["lock_confirmed"])
        peer.close()
        wait_for(lambda: not card()["connected"])
        assert card()["lock_confirmed"] is False
        again = students()
        assert again.hello(resume_token=welcome["resume_token"])["student_id"] == sid
        assert card()["lock_confirmed"] is False, "reconnect cannot revive a previous connection's receipt"
        again.status(locked=False)
        wait_for(lambda: card()["locked"] is False)
        assert card()["lock_confirmed"] is False
    finally:
        stream.close()


def test_control_assets_and_core_commands(tmp_path):
    srv = ServerProcess(tmp_path / "controls", {"QORGAU_CLASS_FEATURES": "proctor_classctl.classroom_feature:create"})
    teacher = srv.teacher()
    anonymous = srv.teacher(login=False)
    peer = srv.student()
    try:
        prefix = "/api/teacher/control/assets/"
        assert anonymous.get(prefix + "classroom-module.js").status_code == 401
        for path, content_type in [("classroom-module.js", "javascript"), ("classroom-model.js", "javascript"), ("src/api.js", "javascript"), ("src/model.js", "javascript"), ("src/dom.js", "javascript"), ("t02-module.css", "css")]:
            response = teacher.get(prefix + path)
            assert response.status_code == 200, response.text
            assert content_type in response.headers["content-type"]
        assert teacher.get(prefix + "src/app.js").status_code == 404
        session = teacher.post("/api/teacher/session", json={"title": "One current class", "mode": "url", "allowed_urls": ["https://exam.example/*"]}).json()
        sid = peer.hello(join_code=session["join_code"])["student_id"]
        peer.status()
        # No T04 exam needs to be created: the active classroom is the only selection.
        assert teacher.get("/api/teacher/control/exams").json() == []
        response = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "lock", "payload": {"reason_ru": "Дождитесь преподавателя"}})
        assert response.status_code == 202
        cmd = response.json()
        assert cmd["status"] in ("queued", "sent") and cmd["ack"] is None
        peer.wait_for(lambda e: e["type"] == "command" and e["command_id"] == cmd["command_id"])
        peer.send("ack", command_id=cmd["command_id"], ok=False, code="failed", error_ru="Экран не подтвердил команду")
        wait_for(lambda: teacher.get(f"/api/teacher/commands/{cmd['command_id']}").json()["status"] == "failed")
        assert teacher.get(f"/api/teacher/students/{sid}").json()["lock_confirmed"] is False
    finally:
        peer.close()
        teacher.close()
        anonymous.close()
        assert srv.stop() == 0, "".join(srv.stderr[-20:])


def test_persisted_receipt_never_revives_on_server_restart(tmp_path):
    directory = tmp_path / "restart"
    srv = ServerProcess(directory, {"QORGAU_CLASS_FEATURES": ""})
    teacher, peer = srv.teacher(), srv.student()
    try:
        session = teacher.post("/api/teacher/session", json={"title": "Receipt restart", "mode": "url", "allowed_urls": ["https://exam.example/*"]}).json()
        welcome = peer.hello(join_code=session["join_code"])
        url = f"/api/teacher/students/{welcome['student_id']}"
        peer.status(locked=True, lock_state="applied", lock_confirmed=True, lock_requested=True, lock_scope="app_overlay")
        wait_for(lambda: teacher.get(url).json()["lock_confirmed"])
    finally:
        peer.close()
        teacher.close()
        assert srv.stop() == 0
    srv = ServerProcess(directory, {"QORGAU_CLASS_FEATURES": ""})
    teacher, peer = srv.teacher(), srv.student()
    try:
        card = teacher.get(url).json()
        assert card["locked"] is True and card["lock_scope"] == "app_overlay", "history is retained"
        assert card["lock_confirmed"] is False
        peer.hello(resume_token=welcome["resume_token"])
        assert teacher.get(url).json()["lock_confirmed"] is False
        peer.status(locked=False, lock_state="applied", lock_confirmed=True, lock_requested=False, lock_scope="app_overlay")
        wait_for(lambda: teacher.get(url).json()["lock_confirmed"])
        assert teacher.get(url).json()["locked"] is False
    finally:
        peer.close()
        teacher.close()
        assert srv.stop() == 0
