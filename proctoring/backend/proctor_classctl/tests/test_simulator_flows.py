"""End-to-end flows through the SIMULATOR (success, delay, error, disconnects, offline, v1, unsupported)."""

from __future__ import annotations

from proctor_classctl.simulator import LABEL_PREFIX

from .conftest import TEACHER


def lock_one(rig, sid, reason="Проверка документов", **kw):
    res = rig.lock([sid], reason=reason, **kw)
    return res["results"][0]


def last_cmd(rig, sid):
    return rig.views()[sid]["commands"][0]


def test_simulated_students_are_labelled(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "success")
    v = sim_rig.views()["s1"]
    assert v["simulated"] is True and v["label"].startswith(LABEL_PREFIX)


def test_success_flow_and_status_based_lock(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "success")
    r = lock_one(sim_rig, "s1")
    assert r["command"]["group"] == "pending"
    assert sim_rig.views()["s1"]["lock"]["state"] == "lock_pending"
    sim_rig.advance(0.07)
    assert last_cmd(sim_rig, "s1")["group"] == "executing"
    sim_rig.advance(1.0)
    v = sim_rig.views()["s1"]
    assert v["commands"][0]["group"] == "done" and v["lock"]["state"] == "locked" and v["lock"]["source"] == "status"
    assert sim_rig.sim.students["s1"].locked is True
    sim_rig.control.send_command(TEACHER, sim_rig.exam_id, kind="unlock", student_ids=["s1"], payload={}, idempotency_key=None)
    sim_rig.advance(1.0)
    assert sim_rig.views()["s1"]["lock"]["state"] == "unlocked"


def test_delay_with_progress_stays_executing_then_done(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "delay")
    lock_one(sim_rig, "s1")
    sim_rig.advance(11)
    assert last_cmd(sim_rig, "s1")["group"] == "executing"
    sim_rig.advance(2)
    assert last_cmd(sim_rig, "s1")["group"] == "done"


def test_delay_without_progress_is_unknown_then_late_done(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "v1")
    sim_rig.sim.students["s1"].delay_s = 12.0  # a v1 client sends no progress messages
    lock_one(sim_rig, "s1")
    sim_rig.advance(10.5)
    c = last_cmd(sim_rig, "s1")
    assert c["state"] == "unconfirmed" and c["group"] == "no_connection"
    sim_rig.advance(2)
    c = last_cmd(sim_rig, "s1")
    assert c["state"] == "succeeded" and c["ack"]["late"] is True


def test_error_flow(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "error")
    lock_one(sim_rig, "s1")
    sim_rig.advance(1.5)
    v = sim_rig.views()["s1"]
    assert v["commands"][0]["group"] == "error" and "СИМУЛЯТОР" in v["commands"][0]["label_ru"]
    assert v["lock"]["state"] == "unlocked"  # never shown as locked


def test_disconnect_before_exec_redelivers_same_command(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "disconnect_before_exec", reconnect_s=8.0)
    cid = lock_one(sim_rig, "s1")["command"]["command_id"]
    sim_rig.advance(0.2)
    v = sim_rig.views()["s1"]
    assert v["connected"] is False and v["commands"][0]["state"] == "lost" and v["commands"][0]["group"] == "no_connection"
    assert v["lock"]["state"] == "lock_pending"
    sim_rig.advance(9.5)
    v = sim_rig.views()["s1"]
    assert v["connected"] and v["commands"][0]["command_id"] == cid and v["commands"][0]["state"] == "succeeded"
    assert v["commands"][0]["attempts"] == 2 and v["lock"]["state"] == "locked"


def test_disconnect_after_exec_late_ack_and_no_double_execution(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "disconnect_after_exec", reconnect_s=5.0)
    cid = lock_one(sim_rig, "s1")["command"]["command_id"]
    sim_rig.advance(0.2)
    assert sim_rig.views()["s1"]["commands"][0]["state"] == "lost"
    sim_rig.advance(6)
    v = sim_rig.views()["s1"]
    assert v["commands"][0]["state"] == "succeeded" and v["commands"][0]["ack"]["late"] is True
    executed = [line for line in sim_rig.sim.students["s1"].log if "executed lock" in line]
    assert len(executed) == 1  # re-delivery after reconnect was de-duplicated by command_id


def test_lock_sent_to_offline_student_expires_and_never_runs(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "success")
    sim_rig.sim.set_online("s1", False)
    r = lock_one(sim_rig, "s1")
    assert r["command"]["group"] == "no_connection"
    sim_rig.advance(130, step=1.0)
    sim_rig.sim.set_online("s1", True)
    sim_rig.advance(2)
    v = sim_rig.views()["s1"]
    assert v["commands"][0]["state"] == "expired" and v["lock"]["state"] == "unlocked"
    assert sim_rig.sim.students["s1"].locked is False
    assert not any("got lock" in line for line in sim_rig.sim.students["s1"].log)


def test_client_refuses_command_received_after_its_ttl(sim_rig):
    """Protection on the client side too: a command that reaches execution after ttl_ms is refused."""
    sim_rig.sim.add_student("s1", "Студент", "success")
    sim_rig.sim.students["s1"].delay_s = 30.0  # the client is slow to execute
    lock_one(sim_rig, "s1", ttl_s=10)
    sim_rig.advance(31, step=0.5)
    c = last_cmd(sim_rig, "s1")
    assert c["state"] == "rejected" and c["ack"]["code"] == "expired"
    assert sim_rig.sim.students["s1"].locked is False


def test_unsupported_client_buttons_disabled(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "unsupported")
    v = sim_rig.views()["s1"]
    assert v["actions"]["lock"] == {"available": False, "reason_ru": "Клиент студента не поддерживает действие «Заблокировать»"}
    r = lock_one(sim_rig, "s1")
    assert r["command"] is None and r["unavailable_ru"]


def test_reconnect_does_not_resurrect_superseded_lock(sim_rig):
    sim_rig.sim.add_student("s1", "Студент", "success")
    sim_rig.sim.set_online("s1", False)
    lock_one(sim_rig, "s1")
    sim_rig.control.send_command(TEACHER, sim_rig.exam_id, kind="unlock", student_ids=["s1"], payload={}, idempotency_key=None)
    sim_rig.sim.set_online("s1", True)
    sim_rig.advance(2)
    log = sim_rig.sim.students["s1"].log
    assert any("got unlock" in line for line in log) and not any("got lock" in line for line in log)
    assert sim_rig.views()["s1"]["lock"]["state"] == "unlocked"
