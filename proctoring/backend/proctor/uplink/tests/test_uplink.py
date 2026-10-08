"""C2 uplink against the fake class server (tests/fake_server.py) with a fake backend view."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from proctor.uplink.backend_view import Snapshot
from proctor.uplink.client import ClassStateMsg, Uplink
from proctor.uplink.config import UplinkConfig, config_from_env
from proctor.uplink.outbox import Outbox
from proctor.uplink.lock import LockReceipt
from proctor.uplink.tests.fake_server import FakeClassServer, free_port


def wait_for(pred, timeout=8.0, step=0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


class FakeView:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.exam_state = "running"
        self.incidents: list[dict[str, Any]] = []
        self.clip_calls: list[tuple[float, float, float, float]] = []  # (t_start_ms, before, after, wall time)
        self.lock = threading.Lock()

    def add_incident(self, iid: str, state: str = "open", t_start_ms: float = 12_000.0, rule: str = "phone_visible", priority: str = "medium") -> None:
        with self.lock:
            self.incidents = [i for i in self.incidents if i["incident_id"] != iid] + [{
                "incident_id": iid, "rule_id": rule, "category": "phone", "priority": priority, "state": state,
                "t_start_ms": t_start_ms, "t_end_ms": None if state == "open" else t_start_ms + 3000.0,
                "t_start_wall": "2026-10-08T10:00:12+00:00", "duration_ms": 3000.0, "explanation_ru": "Телефон виден 3,0 с",
            }]

    def snapshot(self) -> Snapshot:
        with self.lock:
            inc = list(self.incidents)
        by = {"low": 0, "medium": 0, "high": 0}
        for i in inc:
            by[i["priority"]] += 1
        return Snapshot(session_id="s-1", exam_state=self.exam_state, camera="ok", monitoring="ok", zone="yellow" if inc else "green",
                        zone_reasons_ru=["Телефон в кадре — 00:12, 3 с"] if inc else [], incidents=inc, incidents_total=len(inc), incidents_by_priority=by)

    def preview_jpeg(self) -> bytes:
        return b"\xff\xd8" + b"0" * 1000 + b"\xff\xd9"

    def export_clip(self, t_start_ms: float, before_s: float, after_s: float) -> Path:
        self.clip_calls.append((t_start_ms, before_s, after_s, time.monotonic()))
        time.sleep(0.3)
        p = self.tmp / f"clip-{len(self.clip_calls)}.avi"
        p.write_bytes(b"RIFF" + b"x" * 5000)
        return p

    def start_exam(self):
        self.exam_state = "running"
        return True, None

    def finish_exam(self):
        self.exam_state = "finished"
        return True, None


def make_cfg(tmp: Path, server: str, code: str = "123456") -> UplinkConfig:
    return UplinkConfig(server=server, join_code=code, student_label="Студент 1", computer_name="pc-1", state_dir=tmp / "uplink",
                        status_interval_s=0.5, preview_interval_s=0.5, poll_interval_s=0.1, backoff_max_s=2.0, welcome_timeout_s=3.0)


@pytest.fixture()
def server():
    s = FakeClassServer().start()
    yield s
    s.stop()


@pytest.fixture()
def run(tmp_path):
    created: list[Uplink] = []

    def factory(cfg: UplinkConfig, view: Any = None, events: list | None = None, ui_receipts: bool = False) -> tuple[Uplink, FakeView]:
        v = view or FakeView(tmp_path)
        def publish(message):
            if events is not None:
                events.append(message)
            request = getattr(message, "lock_request", None)
            if ui_receipts and request:
                up.confirm_lock(LockReceipt(**{k: v for k, v in request.items() if k not in ("expires_at", "recovery")}, applied=True))
        up = Uplink(cfg, v, publish=publish)
        up.start()
        created.append(up)
        return up, v

    yield factory
    for up in created:
        up.stop()


def test_config_from_env_is_optional_and_validated(tmp_path):
    assert config_from_env(tmp_path, {}) is None
    assert config_from_env(tmp_path, {"QORGAU_CLASS_SERVER": "10.0.0.5:8765"}) is None
    cfg = config_from_env(tmp_path, {"QORGAU_CLASS_SERVER": "10.0.0.5:8765", "QORGAU_CLASS_CODE": "123456"})
    assert cfg is not None and cfg.ws_url == "ws://10.0.0.5:8765/ws/student" and cfg.state_dir == tmp_path / "class_uplink"
    for bad in ({"QORGAU_CLASS_SERVER": "10.0.0.5", "QORGAU_CLASS_CODE": "123456"}, {"QORGAU_CLASS_SERVER": "a:1", "QORGAU_CLASS_CODE": "12ab56"}):
        with pytest.raises(ValueError):
            config_from_env(tmp_path, bad)


def test_hello_welcome_status_preview_pong_and_token_not_logged(server, run, tmp_path, caplog):
    caplog.set_level("DEBUG", logger="proctor.uplink")
    events: list[ClassStateMsg] = []
    up, view = run(make_cfg(tmp_path, server.address), events=events)
    assert wait_for(lambda: up.connection == "connected")
    hello = server.hellos[0]
    assert hello["type"] == "hello" and hello["protocol"] == "qorgau.class.v1" and hello["join_code"] == "123456"
    assert {"msg_id", "sent_at", "computer_name", "student_label", "app_version"} <= set(hello) and hello["v"] == 1
    assert wait_for(lambda: len(server.of_type("status")) >= 2 and len(server.of_type("preview")) >= 1)
    st = server.of_type("status")[-1]
    assert st["exam_state"] == "running" and st["camera"] == "ok" and st["locked"] is False and st["mic_active"] is False
    server.send_ping()
    assert wait_for(lambda: len(server.of_type("pong")) >= 1)
    token = up.outbox.get_meta("resume_token")
    assert token and len(token) == 64 and token not in caplog.text and "123456" not in caplog.text
    assert any(e.connection == "connected" and e.exam and e.exam["exam_id"] == "demo-exam-1" for e in events)


def test_wrong_code_is_rejected_and_not_retried(server, run, tmp_path):
    events: list[ClassStateMsg] = []
    up, _ = run(make_cfg(tmp_path, server.address, code="999999"), events=events)
    assert wait_for(lambda: up.connection == "rejected")
    time.sleep(2.5)
    assert len(server.hellos) == 1  # no hammering the server with a wrong code
    assert events[-1].connection == "rejected" and "локально" in (events[-1].message_ru or "")


def test_server_down_at_start_then_up_connects_with_backoff(run, tmp_path):
    port = free_port()
    up, view = run(make_cfg(tmp_path, f"127.0.0.1:{port}"))
    assert wait_for(lambda: up.connection == "reconnecting", timeout=5)
    view.add_incident("inc-early", state="closed")  # queued while offline
    s = FakeClassServer(port=port).start()
    try:
        assert wait_for(lambda: up.connection == "connected", timeout=10)
        assert wait_for(lambda: any(m["incident_id"] == "inc-early" for m in s.accepted if m["type"] == "incident"))
    finally:
        s.stop()


def test_disconnect_reconnect_resumes_and_resends_in_seq_order(server, run, tmp_path):
    up, view = run(make_cfg(tmp_path, server.address))
    assert wait_for(lambda: up.connection == "connected")
    view.add_incident("inc-1")
    assert wait_for(lambda: len([m for m in server.accepted if m["type"] == "incident"]) == 1)
    server.stop()  # network gone
    assert wait_for(lambda: up.connection == "reconnecting", timeout=5)
    view.add_incident("inc-2")
    view.add_incident("inc-1", state="closed")
    view.add_incident("inc-3", state="closed", priority="high")
    time.sleep(0.5)
    assert len(up.outbox) >= 3  # persisted while offline
    restarted = FakeClassServer(port=server.port)
    restarted.tokens = server.tokens  # same server state: resume tokens survive
    restarted.start()
    try:
        assert wait_for(lambda: up.connection == "connected", timeout=10)
        assert "resume_token" in restarted.hellos[-1] and "join_code" not in restarted.hellos[-1]
        assert wait_for(lambda: len([m for m in restarted.accepted if m["type"] == "incident"]) >= 3)
        got = [m for m in restarted.accepted if m["type"] == "incident"]
        seqs = [m["seq"] for m in got]
        assert seqs == sorted(seqs) and seqs[0] > 1  # seq keeps growing across the reconnect
        # inc-1 may also be re-sent as "open" with clip_available=true (clip finished after the first send)
        order = [(m["incident_id"], m["state"]) for m in got if not (m["incident_id"] == "inc-1" and m["state"] == "open")]
        assert order[:3] == [("inc-2", "open"), ("inc-1", "closed"), ("inc-3", "closed")]
        assert len(up.outbox) == 0
    finally:
        restarted.stop()


def test_duplicate_seq_after_unconfirmed_send_is_resent_with_same_seq_and_dropped_by_server(server, run, tmp_path):
    up, view = run(make_cfg(tmp_path, server.address))
    assert wait_for(lambda: up.connection == "connected")
    real_done = up.outbox.done
    lost = {"n": 0}

    def done_lost_once(seq: int) -> None:  # the send happened but was not confirmed (e.g. the process died)
        if lost["n"] == 0:
            lost["n"] += 1
            return  # stays in the outbox -> sent again with the SAME seq and msg_id
        real_done(seq)

    up.outbox.done = done_lost_once  # type: ignore[method-assign]
    view.add_incident("inc-dup")
    assert wait_for(lambda: server.duplicates >= 1, timeout=10)
    incs = [m for m in server.accepted if m["type"] == "incident" and m["incident_id"] == "inc-dup"]
    raw = [m for m in server.of_type("incident") if m["incident_id"] == "inc-dup"]
    assert len({m["seq"] for m in incs}) == len(incs)  # the server kept one copy per seq
    assert raw[0]["seq"] == raw[1]["seq"] and raw[0]["msg_id"] == raw[1]["msg_id"]
    assert wait_for(lambda: len(up.outbox) == 0)


def test_outbox_seq_survives_restart_and_is_bounded(tmp_path):
    ob = Outbox(tmp_path / "o.sqlite", max_items=5)
    for i in range(7):
        ob.put({"type": "incident", "n": i})
    assert len(ob) == 5 and ob.dropped == 2 and [m["seq"] for m in ob.pending()] == [3, 4, 5, 6, 7]
    ob.close()
    ob2 = Outbox(tmp_path / "o.sqlite", max_items=5)
    assert ob2.put({"type": "ack"}) == 8
    ob2.close()


def test_lock_unlock_audio_publish_class_state_and_ack(server, run, tmp_path):
    events: list[ClassStateMsg] = []
    up, _ = run(make_cfg(tmp_path, server.address), events=events, ui_receipts=True)
    assert wait_for(lambda: up.connection == "connected")
    cid = server.send_command("lock", {"reason_ru": "Телефон на столе"})
    assert wait_for(lambda: cid in server.acks())
    assert server.acks()[cid]["ok"] is True
    ev = [e for e in events if e.locked]
    assert ev and ev[-1].lock_reason_ru == "Телефон на столе" and ev[-1].type == "class_state" and ev[-1].last_command["kind"] == "lock"
    assert wait_for(lambda: server.of_type("status")[-1]["locked"] is True)
    bad = server.send_command("lock", {"reason_ru": ""})
    assert wait_for(lambda: bad in server.acks()) and server.acks()[bad]["ok"] is False and server.acks()[bad]["error_ru"]
    a = server.send_command("audio_start", {"direction": "listen"})
    assert wait_for(lambda: a in server.acks()) and server.acks()[a]["ok"] is False
    assert events[-1].mic_active is False and events[-1].audio_direction is None
    for kind in ("audio_stop", "unlock"):
        c = server.send_command(kind)
        assert wait_for(lambda: c in server.acks()) and server.acks()[c]["ok"]
    assert events[-1].locked is False and events[-1].mic_active is False
    u = server.send_command("format_disk")
    assert wait_for(lambda: u in server.acks()) and server.acks()[u]["ok"] is False


@pytest.mark.parametrize("kind", ["audio_start", "audio_update"])
@pytest.mark.parametrize("direction", ["listen", "talk", "both"])
def test_audio_refused_until_media_endpoint_exists(server, run, tmp_path, kind, direction):
    """A teacher command cannot turn a boolean into proof of microphone capture."""
    events: list[ClassStateMsg] = []
    up, _ = run(make_cfg(tmp_path, server.address), events=events)
    assert wait_for(lambda: up.connection == "connected")
    payload = {"direction": direction}
    cid = server.send_command(kind, payload)
    assert wait_for(lambda: cid in server.acks())
    ack = server.acks()[cid]
    assert ack["ok"] is False
    assert ack["code"] == "unsupported" and ack["error_code"] == "not_supported"
    assert "не подключена" in ack["error_ru"]
    assert up.mic_active is False and up.audio_direction is None
    assert all(not e.mic_active and e.audio_direction is None for e in events)

    # A fresh status after refusal stays honest, including talk-only requests.
    statuses = len(server.of_type("status"))
    assert wait_for(lambda: len(server.of_type("status")) > statuses)
    assert all(m["mic_active"] is False for m in server.of_type("status"))

    # A redelivered command repeats the refusal without starting anything.
    server.send_command(kind, payload, command_id=cid)
    assert wait_for(lambda: len(server.all_acks(cid)) == 2)
    repeated = server.all_acks(cid)[-1]
    assert all(repeated[k] == ack[k] for k in ("ok", "code", "error_code", "error_ru"))
    assert up.mic_active is False and all(not e.mic_active for e in events)


def test_clip_exported_at_incident_open_and_uploaded_on_request(server, run, tmp_path):
    up, view = run(make_cfg(tmp_path, server.address))
    assert wait_for(lambda: up.connection == "connected")
    t_open = time.monotonic()
    view.add_incident("inc-clip", t_start_ms=42_000.0)
    assert wait_for(lambda: view.clip_calls, timeout=3)
    t_start, before, after, called_at = view.clip_calls[0]
    assert (t_start, before, after) == (42_000.0, 5.0, 5.0) and called_at - t_open < 1.0  # at OPEN, not at request
    assert wait_for(lambda: any(m["incident_id"] == "inc-clip" and m["clip_available"] for m in server.accepted if m["type"] == "incident"))
    first = [m for m in server.accepted if m["type"] == "incident" and m["incident_id"] == "inc-clip"][0]
    assert first["clip_available"] is False and first["state"] == "open"
    cid = server.send_command("request_clip", {"incident_id": "inc-clip"})
    assert wait_for(lambda: cid in server.acks()) and server.acks()[cid]["ok"] is True
    clip = server.clips["inc-clip"]
    assert clip["bytes"] == 5004 and clip["content_type"] == "video/x-msvideo"
    missing = server.send_command("request_clip", {"incident_id": "nope"})
    assert wait_for(lambda: missing in server.acks()) and server.acks()[missing]["ok"] is False


def test_start_and_finish_exam_commands_use_the_lifecycle(server, run, tmp_path):
    up, view = run(make_cfg(tmp_path, server.address))
    view.exam_state = "preflight"
    assert wait_for(lambda: up.connection == "connected")
    cid = server.send_command("finish_exam")
    assert wait_for(lambda: cid in server.acks()) and view.exam_state == "finished"
    cid = server.send_command("start_exam")
    assert wait_for(lambda: cid in server.acks()) and server.acks()[cid]["ok"] and view.exam_state == "running"


def test_redelivered_command_is_not_executed_twice_and_ack_has_code_and_result(server, run, tmp_path):
    """T04 re-delivers a command with the same command_id after a reconnect; the client must de-duplicate."""
    events: list[ClassStateMsg] = []
    up, view = run(make_cfg(tmp_path, server.address), events=events, ui_receipts=True)
    assert wait_for(lambda: up.connection == "connected")
    cid = server.send_command("lock", {"reason_ru": "Первая причина"})
    assert wait_for(lambda: cid in server.acks())
    first = server.acks()[cid]
    assert first["ok"] is True and first["result"]["locked"] is True and "code" not in first
    n_events = len([e for e in events if e.locked])
    server.send_command("lock", {"reason_ru": "Другая причина"}, command_id=cid)  # same id, re-delivery
    assert wait_for(lambda: len(server.all_acks(cid)) >= 2)
    assert len([e for e in events if e.locked]) == n_events and up.lock_reason_ru == "Первая причина"
    assert server.all_acks(cid)[1]["ok"] is True
    u = server.send_command("apply_policy", {"policy_id": "p1"})  # T04 kind outside qorgau.class.v1
    assert wait_for(lambda: u in server.acks())
    assert server.acks()[u]["ok"] is False and server.acks()[u]["code"] == "unsupported"
    view.exam_state = "preflight"
    f = server.send_command("request_clip", {"incident_id": "none"})
    assert wait_for(lambda: f in server.acks()) and server.acks()[f]["code"] == "failed"
