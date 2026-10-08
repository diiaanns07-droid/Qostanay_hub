"""Teacher HTTP API (FastAPI router) and the loopback-only DEV server."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from proctor_classctl import ClassControl, FakeClock
from proctor_classctl.api import create_teacher_router, install_error_handlers

from .conftest import OBSERVER, STRANGER, TEACHER, URL_POLICY, RecordingTransport

PEOPLE = {p.teacher_id: p for p in (TEACHER, OBSERVER, STRANGER)}
BASE = "/api/teacher/control"


@pytest.fixture()
def api():
    clock = FakeClock()
    transport = RecordingTransport()
    control = ClassControl(transport, clock=clock)
    app = FastAPI()
    install_error_handlers(app)

    def principal(request: Request):
        return PEOPLE.get(request.headers.get("x-test-teacher", ""))

    app.include_router(create_teacher_router(control, principal), prefix="/api/teacher")
    client = TestClient(app)

    def as_(who):
        return {"x-test-teacher": who.teacher_id}

    return client, control, transport, as_


def _exam(client, as_):
    r = client.post(f"{BASE}/exams", headers=as_(TEACHER), json={"title": "Физика", "policy": {**URL_POLICY, "instructions_ru": ""}})
    assert r.status_code == 201, r.text
    return r.json()


def _student(control, transport, exam_id, sid="s1"):
    transport.online.add(sid)
    control.student_connected(sid, {"student_label": "Студент", "capabilities": {
        "commands": ["start_exam", "finish_exam", "lock", "unlock", "apply_policy"], "modes": ["url", "app"]}}, exam_id)


def test_unauthenticated_is_401(api):
    client, *_ = api
    r = client.get(f"{BASE}/exams")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_create_edit_and_conflict(api):
    client, control, transport, as_ = api
    exam = _exam(client, as_)
    assert exam["policies"][0]["mode"] == "url" and exam["policies"][0]["start_url"] == "https://exam.example.kz/test/1"
    r = client.patch(f"{BASE}/exams/{exam['exam_id']}", headers=as_(TEACHER), json={"revision": exam["revision"], "title": "Физика-2"})
    assert r.status_code == 200 and r.json()["title"] == "Физика-2"
    r = client.patch(f"{BASE}/exams/{exam['exam_id']}", headers=as_(TEACHER), json={"revision": exam["revision"], "title": "старое"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_edit" and r.json()["error"]["details"]["current"] == exam["revision"] + 1


def test_validation_errors_are_422_with_field(api):
    client, control, transport, as_ = api
    r = client.post(f"{BASE}/exams", headers=as_(TEACHER), json={"title": "X", "policy": {"mode": "url", "start_url": "http://localhost/"}})
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "start_url"
    r = client.post(f"{BASE}/exams", headers=as_(TEACHER), json={"title": "X", "policy": {"mode": "url"}, "unknown": 1})
    assert r.status_code == 422  # extra fields are rejected


def test_permissions_over_http(api):
    client, control, transport, as_ = api
    exam = _exam(client, as_)
    eid = exam["exam_id"]
    _student(control, transport, eid)
    assert client.get(f"{BASE}/exams/{eid}", headers=as_(STRANGER)).status_code == 403
    assert client.get(f"{BASE}/exams", headers=as_(STRANGER)).json() == []
    body = {"kind": "lock", "student_ids": ["s1"], "payload": {"reason_ru": "x"}}
    assert client.post(f"{BASE}/exams/{eid}/commands", headers=as_(STRANGER), json=body).status_code == 403
    assert client.get(f"{BASE}/exams/{eid}/journal", headers=as_(STRANGER)).status_code == 403
    assert client.get(f"{BASE}/exams/missing", headers=as_(TEACHER)).status_code == 404


def test_command_roundtrip_and_idempotent_post(api):
    client, control, transport, as_ = api
    eid = _exam(client, as_)["exam_id"]
    _student(control, transport, eid)
    body = {"kind": "lock", "student_ids": ["s1"], "payload": {"reason_ru": "Проверка"}, "idempotency_key": "click-0000000001"}
    r1 = client.post(f"{BASE}/exams/{eid}/commands", headers=as_(TEACHER), json=body)
    r2 = client.post(f"{BASE}/exams/{eid}/commands", headers=as_(TEACHER), json=body)
    assert r1.status_code == 202 and r2.status_code == 202
    c1, c2 = r1.json()["results"][0], r2.json()["results"][0]
    assert c1["created"] is True and c2["created"] is False and c1["command"]["command_id"] == c2["command"]["command_id"]
    assert len(transport.to("s1")) == 1
    cid = c1["command"]["command_id"]
    control.handle_student_message("s1", {"type": "ack", "command_id": cid, "ok": True})
    cmds = client.get(f"{BASE}/exams/{eid}/commands", headers=as_(TEACHER)).json()
    assert cmds[0]["state"] == "succeeded" and cmds[0]["group"] == "done"
    r = client.get(f"{BASE}/exams/{eid}/students", headers=as_(OBSERVER))
    assert r.status_code == 403  # the observer is not staff of this exam -> no access at all


def test_observer_can_view_but_not_command(api):
    client, control, transport, as_ = api
    r = client.post(f"{BASE}/exams", headers=as_(TEACHER), json={"title": "Физика", "policy": URL_POLICY,
                                                                   "staff": {OBSERVER.teacher_id: "observer"}})
    eid = r.json()["exam_id"]
    _student(control, transport, eid)
    assert client.get(f"{BASE}/exams/{eid}/students", headers=as_(OBSERVER)).status_code == 200
    r = client.post(f"{BASE}/exams/{eid}/commands", headers=as_(OBSERVER), json={"kind": "unlock", "student_ids": ["s1"]})
    assert r.status_code == 403


def test_bad_command_inputs(api):
    client, control, transport, as_ = api
    eid = _exam(client, as_)["exam_id"]
    _student(control, transport, eid)
    url = f"{BASE}/exams/{eid}/commands"
    assert client.post(url, headers=as_(TEACHER), json={"kind": "lock", "student_ids": ["s1"]}).status_code == 422
    assert client.post(url, headers=as_(TEACHER), json={"kind": "reboot", "student_ids": ["s1"]}).status_code == 422
    assert client.post(url, headers=as_(TEACHER), json={"kind": "unlock", "student_ids": []}).status_code == 422
    assert client.post(url, headers=as_(TEACHER), json={"kind": "unlock", "student_ids": ["../x"]}).status_code == 422
    assert client.post(url, headers=as_(TEACHER), json={"kind": "unlock", "student_ids": ["s1"], "ttl_s": 99999}).status_code == 422
    assert client.post(url, headers=as_(TEACHER), json={"kind": "unlock", "student_ids": ["ghost"]}).status_code == 403
    r = client.post(f"{BASE}/exams/{eid}/commands/cmd-x/cancel", headers=as_(TEACHER))
    assert r.status_code == 404


def test_meta_explains_auth_domains_and_timer(api):
    client, control, transport, as_ = api
    meta = client.get(f"{BASE}/meta", headers=as_(TEACHER)).json()
    assert "домены" in meta["auth_domains_note_ru"] and "НЕ" in meta["site_timer_note_ru"]
    assert set(meta["status_groups_ru"]) >= {"pending", "executing", "done", "error", "no_connection"}
    assert meta["sso_suggestions"]["Google"]


def test_devserver_is_loopback_only_and_labelled():
    from proctor_classctl.devserver import build_app

    app = build_app(tick_s=0.05)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        info = client.get("/sim/info").json()
        assert info["simulator"] is True and all(s["label"].startswith("СИМУЛЯТОР") for s in info["students"])
        h = {"x-qorgau-dev-teacher": "t-aigerim"}
        students = client.get(f"{BASE}/exams/{info['exam_id']}/students", headers=h).json()
        assert len(students) == 8 and all(s["simulated"] for s in students)
        assert client.get(f"{BASE}/exams", headers={"x-qorgau-dev-teacher": "nobody"}).status_code == 401
        r = client.get("/sim/info", headers={"host": "evil.example"})
        assert r.status_code == 403
