"""Command lifecycle with a hand-driven client: delivery, client confirmation, expiry, idempotency,
timeouts, reconnection, duplicates, superseding, cancellation, journal."""

from __future__ import annotations

import pytest

from proctor_classctl.commands import CmdState, CommandConflict
from proctor_classctl.policies import PolicyError

from .conftest import CO_TEACHER, FULL_CAPS, TEACHER, ack, connect, disconnect, status


def only_cmd(result):
    [r] = result["results"]
    return r["command"]


def test_command_message_carries_id_expiry_and_ttl(raw_rig):
    connect(raw_rig, "s1")
    cmd = only_cmd(raw_rig.lock(["s1"], key="k-00000001"))
    [msg] = raw_rig.transport.to("s1")
    assert msg["type"] == "command" and msg["v"] == 1 and msg["kind"] == "lock"
    assert msg["command_id"] == cmd["command_id"] and msg["payload"] == {"reason_ru": "Проверка документов"}
    assert msg["expires_at"] == cmd["expires_at"] and msg["ttl_ms"] == 120_000 and msg["attempt"] == 1
    assert cmd["state"] == "sent" and cmd["group"] == "pending"


def test_done_only_after_client_ack_not_server_acceptance(raw_rig):
    connect(raw_rig, "s1")
    cmd = only_cmd(raw_rig.lock(["s1"]))
    view = raw_rig.views()["s1"]
    assert view["lock"]["state"] == "lock_pending"  # accepted and sent is NOT "locked"
    assert view["commands"][0]["group"] == "pending"
    ack(raw_rig, "s1", cmd["command_id"], result={"locked": True})
    view = raw_rig.views()["s1"]
    assert view["commands"][0]["state"] == "succeeded" and view["commands"][0]["group"] == "done"
    assert view["lock"] == {"state": "locked", "label_ru": "Заблокирован (подтверждено клиентом)", "source": "ack",
                            "command_id": cmd["command_id"]}
    status(raw_rig, "s1", locked=False)  # current status from the client wins
    assert raw_rig.views()["s1"]["lock"]["state"] == "unlocked"
    assert "расходится" in raw_rig.views()["s1"]["lock"]["label_ru"]


def test_progress_then_ack_shows_executing(raw_rig):
    connect(raw_rig, "s1")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    raw_rig.control.handle_student_message("s1", {"type": "command_progress", "command_id": cid, "state": "executing"})
    assert raw_rig.views()["s1"]["commands"][0]["group"] == "executing"
    raw_rig.advance(30)  # within the 60 s execution timeout
    assert raw_rig.control.commands.get(cid).state == CmdState.EXECUTING
    ack(raw_rig, "s1", cid)
    assert raw_rig.control.commands.get(cid).state == CmdState.SUCCEEDED


def test_ack_timeout_is_unknown_outcome_and_late_ack_finalizes(raw_rig):
    connect(raw_rig, "s1")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    raw_rig.advance(9.9)
    assert raw_rig.control.commands.get(cid).state == CmdState.SENT
    raw_rig.advance(0.2)
    v = raw_rig.views()["s1"]["commands"][0]
    assert v["state"] == "unconfirmed" and v["group"] == "no_connection" and "результат неизвестен" in v["label_ru"]
    ack(raw_rig, "s1", cid)
    v = raw_rig.views()["s1"]["commands"][0]
    assert v["state"] == "succeeded" and v["ack"]["late"] is True and "после восстановления связи" in v["label_ru"]


def test_client_failure_and_rejections(raw_rig):
    connect(raw_rig, "s1")
    connect(raw_rig, "s2")
    c1 = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    c2 = only_cmd(raw_rig.lock(["s2"]))["command_id"]
    ack(raw_rig, "s1", c1, ok=False, code="failed", error_ru="окно блокировки не открылось")
    ack(raw_rig, "s2", c2, ok=False, code="expired")
    v1 = raw_rig.views()["s1"]["commands"][0]
    v2 = raw_rig.views()["s2"]["commands"][0]
    assert v1["state"] == "failed" and v1["group"] == "error" and "окно блокировки не открылось" in v1["label_ru"]
    assert v2["state"] == "rejected" and v2["group"] == "error" and "срок действия истёк" in v2["label_ru"]


def test_offline_command_waits_then_is_delivered_on_reconnect_within_ttl(raw_rig):
    connect(raw_rig, "s1")
    disconnect(raw_rig, "s1")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    v = raw_rig.views()["s1"]["commands"][0]
    assert v["state"] == "queued" and v["group"] == "no_connection" and "будет доставлена" in v["label_ru"]
    assert raw_rig.transport.to("s1") == []
    raw_rig.advance(60)
    connect(raw_rig, "s1")
    [msg] = raw_rig.transport.to("s1")
    assert msg["command_id"] == cid and msg["ttl_ms"] == 60_000  # time LEFT, not the original ttl


def test_expired_lock_is_never_delivered_after_long_disconnect(raw_rig):
    connect(raw_rig, "s1")
    disconnect(raw_rig, "s1")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    raw_rig.advance(121)
    assert raw_rig.control.commands.get(cid).state == CmdState.EXPIRED
    connect(raw_rig, "s1")  # back after 2 minutes: nothing is sent
    assert raw_rig.transport.to("s1") == []
    v = raw_rig.views()["s1"]
    assert v["commands"][0]["group"] == "error" and "не будет выполнена" in v["commands"][0]["label_ru"]
    assert v["lock"]["state"] != "locked"


def test_expiry_is_checked_at_send_time_even_without_ticks(raw_rig):
    connect(raw_rig, "s1")
    disconnect(raw_rig, "s1")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    raw_rig.clock.advance(500)  # no tick ran in between
    connect(raw_rig, "s1")
    assert raw_rig.transport.to("s1") == [] and raw_rig.control.commands.get(cid).state == CmdState.EXPIRED


def test_lost_after_send_is_resent_with_same_id_and_duplicate_acks_ignored(raw_rig):
    connect(raw_rig, "s1")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    disconnect(raw_rig, "s1")
    v = raw_rig.views()["s1"]["commands"][0]
    assert v["state"] == "lost" and v["group"] == "no_connection" and "результат неизвестен" in v["label_ru"]
    raw_rig.advance(30)
    connect(raw_rig, "s1")
    first, second = raw_rig.transport.to("s1")
    assert second["command_id"] == cid and second["attempt"] == 2
    ack(raw_rig, "s1", cid)
    ack(raw_rig, "s1", cid)  # the client re-acks the re-delivery
    assert raw_rig.control.commands.get(cid).state == CmdState.SUCCEEDED
    actions = [e.action for e in raw_rig.control.journal.query(command_id=cid)]
    assert "ack_duplicate" in actions


def test_lost_and_expired_is_not_resent_and_stays_unknown(raw_rig):
    connect(raw_rig, "s1")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    disconnect(raw_rig, "s1")
    raw_rig.advance(200)
    connect(raw_rig, "s1")
    assert len(raw_rig.transport.to("s1")) == 1  # only the original delivery
    v = raw_rig.views()["s1"]["commands"][0]
    assert v["state"] == "lost" and "срок истёк" in v["label_ru"]
    ack(raw_rig, "s1", cid, ok=True)  # the client's queued ack still arrives later
    assert raw_rig.control.commands.get(cid).state == CmdState.SUCCEEDED


def test_teacher_request_is_idempotent_and_conflicting_reuse_rejected(raw_rig):
    connect(raw_rig, "s1")
    a = only_cmd(raw_rig.lock(["s1"], key="click-00000001"))
    b_res = raw_rig.lock(["s1"], key="click-00000001")
    assert b_res["results"][0]["created"] is False and b_res["results"][0]["command"]["command_id"] == a["command_id"]
    assert len(raw_rig.transport.to("s1")) == 1
    with pytest.raises(CommandConflict):
        raw_rig.lock(["s1"], key="click-00000001", reason="другая причина")
    # another teacher may reuse the same key string: keys are per teacher
    other = raw_rig.control.send_command(CO_TEACHER, raw_rig.exam_id, kind="unlock", student_ids=["s1"], payload={},
                                         idempotency_key="click-00000001")
    assert other["results"][0]["created"] is True


def test_newer_lock_or_unlock_replaces_undelivered_older_one(raw_rig):
    connect(raw_rig, "s1")
    disconnect(raw_rig, "s1")
    lock = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    unlock = only_cmd(raw_rig.control.send_command(TEACHER, raw_rig.exam_id, kind="unlock", student_ids=["s1"],
                                                    payload={}, idempotency_key=None))["command_id"]
    assert raw_rig.control.commands.get(lock).state == CmdState.SUPERSEDED
    connect(raw_rig, "s1")
    assert [m["command_id"] for m in raw_rig.transport.to("s1")] == [unlock]  # the stale lock is never sent
    v = {c["command_id"]: c for c in raw_rig.views()["s1"]["commands"]}
    assert v[lock]["group"] == "cancelled" and "Заменена" in v[lock]["label_ru"]


def test_cancel_only_before_delivery(raw_rig):
    connect(raw_rig, "s1")
    disconnect(raw_rig, "s1")
    queued = only_cmd(raw_rig.lock(["s1"]))
    raw_rig.control.cancel_command(TEACHER, raw_rig.exam_id, queued["command_id"])
    connect(raw_rig, "s1")
    assert raw_rig.transport.to("s1") == []
    sent = only_cmd(raw_rig.lock(["s1"], reason="ещё раз"))
    with pytest.raises(CommandConflict):
        raw_rig.control.cancel_command(TEACHER, raw_rig.exam_id, sent["command_id"])


def test_ack_from_wrong_student_or_unknown_command_is_ignored(raw_rig):
    connect(raw_rig, "s1")
    connect(raw_rig, "s2")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    ack(raw_rig, "s2", cid)  # s2 cannot confirm s1's command
    ack(raw_rig, "s2", "cmd-does-not-exist")
    assert raw_rig.control.commands.get(cid).state == CmdState.SENT
    unknown = [e for e in raw_rig.control.journal.query() if e.action == "ack_unknown"]
    assert len(unknown) == 2


def test_lock_reason_validation(raw_rig):
    connect(raw_rig, "s1")
    for bad in ({}, {"reason_ru": ""}, {"reason_ru": "   "}, {"reason_ru": "x" * 201}, {"reason_ru": "ok", "extra": 1}):
        with pytest.raises(PolicyError):
            raw_rig.control.send_command(TEACHER, raw_rig.exam_id, kind="lock", student_ids=["s1"], payload=bad, idempotency_key=None)
    cmd = only_cmd(raw_rig.lock(["s1"], reason="  Проверка\x07 паспорта  "))
    assert cmd["payload"] == {"reason_ru": "Проверка паспорта"}
    with pytest.raises(PolicyError):
        raw_rig.control.send_command(TEACHER, raw_rig.exam_id, kind="unlock", student_ids=["s1"], payload={"x": 1}, idempotency_key=None)
    with pytest.raises(PolicyError):
        raw_rig.control.send_command(TEACHER, raw_rig.exam_id, kind="format_disk", student_ids=["s1"], payload={}, idempotency_key=None)


def test_custom_ttl_is_bounded(raw_rig):
    connect(raw_rig, "s1")
    cmd = only_cmd(raw_rig.lock(["s1"], ttl_s=1))
    assert raw_rig.transport.to("s1")[0]["ttl_ms"] == 10_000  # minimum 10 s


def test_unsupported_capability_is_explicitly_unavailable(raw_rig):
    connect(raw_rig, "s1", caps={**FULL_CAPS, "commands": ["start_exam", "finish_exam"]})
    res = raw_rig.lock(["s1"])
    [r] = res["results"]
    assert r["command"] is None and "не поддерживает" in r["unavailable_ru"]
    actions = raw_rig.views()["s1"]["actions"]
    assert actions["lock"]["available"] is False and actions["start_exam"]["available"] is True
    assert raw_rig.transport.to("s1") == []
    assert any(e.action == "command_unavailable" for e in raw_rig.control.journal.query())


def test_v1_client_gets_mandatory_set_only(raw_rig):
    connect(raw_rig, "s1", caps=None)
    v = raw_rig.views()["s1"]
    assert v["capabilities"]["declared"] is False
    assert all(v["actions"][k]["available"] for k in ("start_exam", "lock", "unlock", "finish_exam"))


def test_journal_records_who_whom_when_what(raw_rig):
    connect(raw_rig, "s1")
    cid = only_cmd(raw_rig.lock(["s1"]))["command_id"]
    ack(raw_rig, "s1", cid)
    entries = raw_rig.control.journal_view(TEACHER, raw_rig.exam_id, student_id="s1")
    issued = next(e for e in entries if e["action"] == "command_issued")
    assert issued["actor_id"] == TEACHER.teacher_id and issued["actor_name"] == TEACHER.display_name
    assert issued["student_ids"] == ["s1"] and issued["student_labels"] == ["Студент s1"]
    assert issued["kind"] == "lock" and issued["details"]["reason_ru"] == "Проверка документов" and issued["at"].endswith("Z")
    states = [e["details"].get("state") for e in entries if e["action"] == "command_state"]
    assert "succeeded" in states and "sent" in states
