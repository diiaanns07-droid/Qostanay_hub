"""Event dedup, command lifecycle (sent != executed), heartbeat/offline, reconnect re-delivery."""

from __future__ import annotations

import time

from classroom.contracts import models as m
from classroom.server.tests.harness import TeacherStream, wait_until


def _cmd(teacher, cid):
    return teacher.get(f"/api/teacher/commands/{cid}").json()


# --------------------------------------------------------------------------------------------- dedup
def test_duplicate_incident_is_stored_once(server, teacher, session, students):
    s = students()
    w = s.hello(join_code=session["join_code"])
    sid = w["student_id"]
    for _ in range(3):
        s.incident(1, "inc-a")
    s.incident(2, "inc-a", state="closed")
    s.incident(2, "inc-a", state="closed")
    assert wait_until(lambda: len(teacher.get(f"/api/teacher/students/{sid}/events").json()) == 2)
    time.sleep(0.3)
    events = [m.ObservationEvent.model_validate(e) for e in teacher.get(f"/api/teacher/students/{sid}/events").json()]
    assert len(events) == 2 and {e.event_id for e in events} == {"v1::1", "v1::2"}  # plain v1: (student_id, seq), §3.1
    for e in events:
        assert e.received_at.tzinfo is not None and e.event_time.tzinfo is not None
    closed = next(e for e in events if e.payload["state"] == "closed")
    assert (closed.event_time - closed.event_time.replace()).total_seconds() == 0
    incs = teacher.get(f"/api/teacher/students/{sid}/incidents").json()
    assert len(incs) == 1 and incs[0]["state"] == "closed" and incs[0]["events"] == 2


def test_v1_clip_ready_resend_is_an_update_not_a_duplicate(server, teacher, session, students):
    """C2 re-sends an open episode with clip_available=true under a NEW seq; a retransmission keeps its seq."""
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    s.incident(1, "inc-c")
    s.incident(1, "inc-c")  # outbox retransmission after an unconfirmed send
    s.incident(2, "inc-c", clip_available=True)  # clip became ready while the episode is still open
    s.incident(2, "inc-c", clip_available=True)
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}/incidents").json()[0]["clip_available"] is True)
    s.incident(3, "inc-c", state="closed", clip_available=True)
    assert wait_until(lambda: len(teacher.get(f"/api/teacher/students/{sid}/events").json()) == 3)
    time.sleep(0.3)
    assert len(teacher.get(f"/api/teacher/students/{sid}/events").json()) == 3
    inc = teacher.get(f"/api/teacher/students/{sid}/incidents").json()[0]
    assert inc["state"] == "closed" and inc["clip_available"] is True and inc["events"] == 3


def test_seq_conflict_keeps_both_events_and_flags_it(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    s.incident(5, "inc-x")
    s.incident(5, "inc-y")  # same seq, different event (e.g. client lost its counter)
    assert wait_until(lambda: len(teacher.get(f"/api/teacher/students/{sid}/events").json()) == 2)
    events = teacher.get(f"/api/teacher/students/{sid}/events").json()
    assert sorted(e["seq_conflict"] for e in events) == [False, True]
    s.incident(5, "inc-y")  # the conflicting event re-delivered: still one copy
    s.incident(6, "inc-z")
    assert wait_until(lambda: len(teacher.get(f"/api/teacher/students/{sid}/events").json()) == 3)
    time.sleep(0.3)
    assert len(teacher.get(f"/api/teacher/students/{sid}/events").json()) == 3


def test_v11_event_id_dedup_and_out_of_order_close(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    s.incident(1, "inc-o", state="closed", event_id="e-close")
    s.incident(2, "inc-o", state="open", event_id="e-open")  # older "open" arriving after "closed"
    s.incident(3, "inc-o", state="open", event_id="e-open")  # re-delivery with another seq, same event
    assert wait_until(lambda: len(teacher.get(f"/api/teacher/students/{sid}/events").json()) == 2)
    time.sleep(0.3)
    inc = teacher.get(f"/api/teacher/students/{sid}/incidents").json()[0]
    assert inc["state"] == "closed"  # never regresses to open


def test_teacher_stream_sees_each_event_once(server, teacher, session, students):
    ts = TeacherStream(server, teacher.cookies.get("qorgau_teacher"))
    try:
        ts.wait_for(lambda x: x["type"] == "snapshot")
        s = students()
        sid = s.hello(join_code=session["join_code"])["student_id"]
        for _ in range(4):
            s.incident(1, "inc-s")
        ts.wait_for(lambda x: x["type"] == "incident" and x["student_id"] == sid)
        time.sleep(0.4)
        assert len([x for x in ts.snapshot() if x["type"] == "incident" and x["student_id"] == sid]) == 1
        seqs = [x["seq"] for x in ts.snapshot()]
        assert seqs == list(range(1, len(seqs) + 1))
        for x in ts.snapshot():
            m.TeacherStreamMessage  # noqa: B018
        from pydantic import TypeAdapter

        ta = TypeAdapter(m.TeacherStreamMessage)
        for x in ts.snapshot():
            ta.validate_python(x)
    finally:
        ts.close()


# ------------------------------------------------------------------------------------------ commands
def test_sent_is_not_executed_until_ack(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    cmd = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "lock", "payload": {"reason_ru": "Телефон на столе"}})
    assert cmd.status_code == 202
    cid = cmd.json()["command_id"]
    got = s.wait_for(lambda x: x["type"] == "command")
    assert got["command_id"] == cid and got["kind"] == "lock" and got["payload"] == {"reason_ru": "Телефон на столе"}
    assert got["expires_at"] and got["attempt"] == 1
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "sent")
    time.sleep(1.0)  # ack window 0.8 s in tests (10 s in v1)
    c = _cmd(teacher, cid)
    assert c["status"] == "sent" and c["unconfirmed"] is True  # "команда не подтверждена", still pending
    s.send("ack", command_id=cid, ok=True, result={"locked": True})
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "succeeded")
    c = m.Command.model_validate(_cmd(teacher, cid))
    assert [h.status.value for h in c.history] == ["queued", "sent", "succeeded"]
    assert c.ack and c.ack.ok and not c.ack.late and c.unconfirmed is False
    s.send("ack", command_id=cid, ok=False, error_ru="повтор")  # a second ack changes nothing
    time.sleep(0.3)
    assert _cmd(teacher, cid)["status"] == "succeeded"


def test_failed_ack_is_failed(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    cid = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "start_exam"}).json()["command_id"]
    s.wait_for(lambda x: x.get("command_id") == cid)
    s.send("ack", command_id=cid, ok=False, error_ru="Браузер не открылся", code="failed")
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "failed")
    assert _cmd(teacher, cid)["ack"]["error_ru"] == "Браузер не открылся"


def test_offline_student_gets_queued_command_on_reconnect(server, teacher, session, students):
    s = students()
    w = s.hello(join_code=session["join_code"])
    s.close()
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{w['student_id']}").json()["connected"] is False)
    cid = teacher.post(f"/api/teacher/students/{w['student_id']}/commands", json={"kind": "unlock"}).json()["command_id"]
    assert _cmd(teacher, cid)["status"] == "queued"
    s2 = students()
    s2.hello(resume_token=w["resume_token"])
    got = s2.wait_for(lambda x: x["type"] == "command")
    assert got["command_id"] == cid and got["attempt"] == 1
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "sent")


def test_unacked_command_is_redelivered_with_same_id_after_reconnect(server, teacher, session, students):
    s = students()
    w = s.hello(join_code=session["join_code"])
    cid = teacher.post(f"/api/teacher/students/{w['student_id']}/commands", json={"kind": "finish_exam"}).json()["command_id"]
    s.wait_for(lambda x: x.get("command_id") == cid)
    s.close()  # connection lost before the ack
    s2 = students()
    s2.hello(resume_token=w["resume_token"])
    again = s2.wait_for(lambda x: x["type"] == "command" and x["command_id"] == cid)
    assert again["attempt"] == 2
    s2.send("ack", command_id=cid, ok=True)
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "succeeded")
    assert _cmd(teacher, cid)["attempts"] == 2


def test_expired_command_is_never_delivered(server, teacher, session, students):
    s = students()
    w = s.hello(join_code=session["join_code"])
    s.close()
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{w['student_id']}").json()["connected"] is False)
    cid = teacher.post(f"/api/teacher/students/{w['student_id']}/commands", json={"kind": "lock", "payload": {"reason_ru": "x"}, "ttl_ms": 1000}).json()["command_id"]
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "expired", 3)
    s2 = students()
    s2.hello(resume_token=w["resume_token"])
    time.sleep(0.6)
    assert not [x for x in s2.snapshot() if x["type"] == "command" and x["command_id"] == cid]


def test_late_ack_after_expiry_is_recorded_honestly(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    cid = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "lock", "payload": {"reason_ru": "x"}, "ttl_ms": 1000}).json()["command_id"]
    s.wait_for(lambda x: x.get("command_id") == cid)
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "expired", 3)
    s.send("ack", command_id=cid, ok=True)
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "succeeded")
    assert _cmd(teacher, cid)["ack"]["late"] is True


def test_cancel_only_while_queued_and_payload_validation(server, teacher, session, students):
    s = students()
    w = s.hello(join_code=session["join_code"])
    r = teacher.post(f"/api/teacher/students/{w['student_id']}/commands", json={"kind": "lock", "payload": {}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_payload"
    r = teacher.post(f"/api/teacher/students/{w['student_id']}/commands", json={"kind": "lock", "payload": {"reason_ru": "x" * 201}})
    assert r.status_code == 422
    r = teacher.post(f"/api/teacher/students/{w['student_id']}/commands", json={"kind": "apply_policy", "payload": {}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "command_unsupported"  # plain v1 client
    cid = teacher.post(f"/api/teacher/students/{w['student_id']}/commands", json={"kind": "unlock"}).json()["command_id"]
    s.wait_for(lambda x: x.get("command_id") == cid)
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "sent")
    assert teacher.post(f"/api/teacher/commands/{cid}/cancel").status_code == 409
    s.close()
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{w['student_id']}").json()["connected"] is False)
    cid2 = teacher.post(f"/api/teacher/students/{w['student_id']}/commands", json={"kind": "unlock"}).json()["command_id"]
    r = teacher.post(f"/api/teacher/commands/{cid2}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_v11_client_can_receive_apply_policy_and_progress(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"], capabilities={"commands": ["apply_policy"], "command_progress": True, "command_expiry": True})["student_id"]
    policy = {"exam_id": "ex-1", "title": "Математика", "mode": "app", "allowed_apps": ["calc.exe"], "policy_id": "p-1", "version": 2}
    cid = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "apply_policy", "payload": policy}).json()["command_id"]
    got = s.wait_for(lambda x: x.get("command_id") == cid)
    assert got["payload"]["policy_id"] == "p-1"
    s.send("command_progress", command_id=cid, state="received")
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "received")
    s.send("ack", command_id=cid, ok=True, result={"policy_id": "p-1", "policy_version": 2})
    assert wait_until(lambda: _cmd(teacher, cid)["status"] == "succeeded")


# ------------------------------------------------------------------------------- heartbeat / zones
def test_silent_client_goes_offline_and_grey(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    s.status(zone="red")
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}").json()["status"]["zone"] == "red")
    s.silent = True  # stops answering pings and sending anything; TCP stays open
    t0 = time.monotonic()
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}").json()["connected"] is False, 5)
    assert time.monotonic() - t0 < 3.5  # pong timeout 1.2 s + ping 0.3 s in tests (15 s / 5 s in v1)
    card = teacher.get(f"/api/teacher/students/{sid}").json()
    assert card["status"]["zone"] == "grey" and card["status"]["zone_source"] == "server_offline"
    assert s.wait_closed(3).rcvd.code == 4408


def test_stale_status_and_bad_camera_force_grey(server, teacher, session, students):
    s = students()
    sid = s.hello(join_code=session["join_code"])["student_id"]
    s.status(zone="green")
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}").json()["status"]["zone"] == "green")
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}").json()["status"]["zone_source"] == "server_stale", 3)
    s.status(zone="green", camera="off")
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}").json()["status"]["zone_source"] == "server_camera")


def test_preview_is_metadata_plus_url_unless_inline_requested(server, teacher, session, students):
    plain = TeacherStream(server, teacher.cookies.get("qorgau_teacher"))
    legacy = TeacherStream(server, teacher.cookies.get("qorgau_teacher"), "inline_previews=1")
    try:
        s = students()
        sid = s.hello(join_code=session["join_code"])["student_id"]
        s.preview()
        p = plain.wait_for(lambda x: x["type"] == "preview" and x["student_id"] == sid)
        assert "jpeg_b64" not in p and p["url"].startswith(f"/api/teacher/students/{sid}/preview.jpg")
        assert legacy.wait_for(lambda x: x["type"] == "preview" and x["student_id"] == sid)["jpeg_b64"]
        img = teacher.get(p["url"])
        assert img.status_code == 200 and img.content[:2] == b"\xff\xd8" and img.headers["cache-control"] == "no-store"
        s.send("preview", jpeg_b64="bm90IGEganBlZw==", frame_wall="2026-10-08T09:00:00+00:00")
        assert s.wait_for(lambda x: x["type"] == "error")["code"] == "invalid_preview"
    finally:
        plain.close()
        legacy.close()
