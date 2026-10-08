"""AudioHub unit tests: authorization, one session, relay isolation, state machine, timeouts, fail-closed endings."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from qorgau_class_audio.hub import AudioHub

SDP = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"


class FakeLink:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, msg: dict[str, Any]) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(msg)

    def of(self, type_: str, kind: str | None = None) -> list[dict[str, Any]]:
        return [m for m in self.sent if m["type"] == type_ and (kind is None or m.get("kind") == kind)]

    def last_state(self) -> dict[str, Any]:
        return self.of("audio_state")[-1]


def run(coro):
    return asyncio.run(coro)


async def setup(**kw):
    hub = AudioHub(**kw)
    t = FakeLink("teacher")
    a = FakeLink("A")
    b = FakeLink("B")
    hub.teacher_online("teacher-1", t)
    hub.student_online("st-a", a)
    hub.student_online("st-b", b)
    return hub, t, a, b


async def connected_session(hub, t, a, listen=True, talk=False):
    await hub.on_teacher_message(t, {"type": "audio_request", "student_id": "st-a", "listen": listen, "talk": talk})
    start = a.of("command", None)[-1]
    assert start["kind"] == "audio_start"
    sid = start["payload"]["audio_session_id"]
    await hub.on_student_ack("st-a", {"type": "ack", "command_id": start["command_id"], "ok": True})
    await hub.on_teacher_message(t, {"type": "audio_media", "audio_session_id": sid, "connection": "connected", "receiving": True, "sending": False})
    return sid, start


def test_request_accept_connect_flow():
    async def go():
        hub, t, a, _ = await setup()
        sid, start = await connected_session(hub, t, a, listen=True, talk=True)
        states = [m["state"] for m in t.of("audio_state")]
        assert states == ["requested", "accepted", "connected"]
        assert start["payload"] == {"audio_session_id": sid, "listen": True, "talk": True, "direction": "both"}

    run(go())


def test_one_active_session_per_teacher_and_other_student_untouched():
    async def go():
        hub, t, a, b = await setup()
        await connected_session(hub, t, a)
        await hub.on_teacher_message(t, {"type": "audio_request", "student_id": "st-b", "listen": True, "talk": False})
        assert t.of("audio_error")[-1]["code"] == "teacher_busy"
        assert b.sent == []  # student B never received anything

    run(go())


def test_signals_only_between_session_participants():
    async def go():
        hub, t, a, b = await setup()
        sid, _ = await connected_session(hub, t, a)
        # student B forges a signal for A's session → dropped, B told, teacher gets nothing
        before = len(t.sent)
        await hub.on_student_message("st-b", {"type": "audio_signal", "audio_session_id": sid, "kind": "answer", "sdp": SDP})
        assert len(t.sent) == before
        assert b.of("audio_error")[-1]["code"] == "session_mismatch"
        # A's real answer is relayed, with only whitelisted fields
        await hub.on_student_message("st-a", {"type": "audio_signal", "audio_session_id": sid, "kind": "answer", "sdp": SDP, "evil": 1})
        relayed = t.of("audio_signal")[-1]
        assert relayed["kind"] == "answer" and "evil" not in relayed
        # student cannot send an offer (teacher always offers)
        await hub.on_student_message("st-a", {"type": "audio_signal", "audio_session_id": sid, "kind": "offer", "sdp": SDP})
        assert a.of("audio_error")[-1]["code"] == "bad_request"

    run(go())


def test_teacher_cannot_touch_unknown_or_foreign_session():
    async def go():
        hub, t, a, _ = await setup()
        other = FakeLink("teacher-tab-2")
        hub.teacher_online("teacher-1", other)
        sid, _ = await connected_session(hub, t, a)
        await hub.on_teacher_message(other, {"type": "audio_stop", "audio_session_id": sid})
        assert other.of("audio_error")[-1]["code"] == "session_mismatch"
        assert t.last_state()["state"] == "connected"
        await hub.on_teacher_message(t, {"type": "audio_signal", "audio_session_id": "as-" + "0" * 32, "kind": "ice", "ice": None})
        assert t.of("audio_error")[-1]["code"] == "session_mismatch"

    run(go())


def test_unauthenticated_link_rejected():
    async def go():
        hub, _, a, _ = await setup()
        intruder = FakeLink("intruder")
        await hub.on_teacher_message(intruder, {"type": "audio_request", "student_id": "st-a", "listen": True, "talk": False})
        assert intruder.of("audio_error")[-1]["code"] == "not_authorized"
        assert a.sent == []

    run(go())


def test_reject_by_student_mic_denied():
    async def go():
        hub, t, a, _ = await setup()
        await hub.on_teacher_message(t, {"type": "audio_request", "student_id": "st-a", "listen": True, "talk": False})
        start = a.of("command")[-1]
        await hub.on_student_ack("st-a", {"type": "ack", "command_id": start["command_id"], "ok": False, "error_code": "mic_denied"})
        st = t.last_state()
        assert st["state"] == "rejected" and st["reason"] == "mic_denied"
        # new request is possible afterwards
        await hub.on_teacher_message(t, {"type": "audio_request", "student_id": "st-a", "listen": True, "talk": False})
        assert t.last_state()["state"] == "requested"

    run(go())


@pytest.mark.parametrize(
    "ender,reason,student_told",
    [
        ("teacher_stop", "teacher_stop", True),
        ("teacher_offline", "teacher_disconnected", True),
        ("teacher_auth_lost", "teacher_auth_lost", True),
        ("student_offline", "student_disconnected", False),
        ("shutdown", "server_shutdown", True),
    ],
)
def test_endings_close_audio_on_both_sides(ender, reason, student_told):
    async def go():
        hub, t, a, _ = await setup()
        sid, _ = await connected_session(hub, t, a)
        if ender == "teacher_stop":
            await hub.on_teacher_message(t, {"type": "audio_stop", "audio_session_id": sid})
        elif ender == "teacher_offline":
            await hub.teacher_offline(t)
        elif ender == "teacher_auth_lost":
            await hub.teacher_auth_lost("teacher-1")
        elif ender == "student_offline":
            await hub.student_offline("st-a", a)
        else:
            await hub.shutdown()
        stops = [m for m in a.of("command") if m["kind"] == "audio_stop"]
        assert bool(stops) == student_told
        if stops:
            assert stops[-1]["payload"] == {"audio_session_id": sid, "reason": reason}
        assert hub.by_teacher == {} and hub.by_student == {}
        assert hub.sessions[sid].state == "ended" and hub.sessions[sid].reason == reason
        # no further relays after the end
        n = len(t.sent)
        await hub.on_student_message("st-a", {"type": "audio_signal", "audio_session_id": sid, "kind": "ice", "ice": None})
        assert len(t.sent) == n

    run(go())


def test_accept_timeout_and_connect_timeout():
    async def go():
        hub, t, a, _ = await setup(accept_timeout_s=0.05, connect_timeout_s=0.05)
        await hub.on_teacher_message(t, {"type": "audio_request", "student_id": "st-a", "listen": True, "talk": False})
        await asyncio.sleep(0.15)
        assert t.last_state()["reason"] == "accept_timeout"
        assert a.of("command")[-1]["kind"] == "audio_stop"
        await hub.on_teacher_message(t, {"type": "audio_request", "student_id": "st-a", "listen": True, "talk": False})
        start = a.of("command")[-1]
        await hub.on_student_ack("st-a", {"type": "ack", "command_id": start["command_id"], "ok": True})
        await asyncio.sleep(0.15)
        assert t.last_state()["reason"] == "connect_timeout"

    run(go())


def test_student_reconnect_old_link_does_not_end_new_session():
    async def go():
        hub, t, a, _ = await setup()
        a2 = FakeLink("A-new")
        hub.student_online("st-a", a2)  # resume_token reconnect replaced the link
        await hub.student_offline("st-a", a)  # late close of the old socket
        await hub.on_teacher_message(t, {"type": "audio_request", "student_id": "st-a", "listen": True, "talk": False})
        assert a2.of("command")[-1]["kind"] == "audio_start"

    run(go())


def test_update_changes_direction_and_validates():
    async def go():
        hub, t, a, _ = await setup()
        sid, _ = await connected_session(hub, t, a, listen=True, talk=False)
        await hub.on_teacher_message(t, {"type": "audio_update", "audio_session_id": sid, "listen": False, "talk": True})
        upd = a.of("command")[-1]
        assert upd["kind"] == "audio_update" and upd["payload"]["direction"] == "talk"
        await hub.on_teacher_message(t, {"type": "audio_update", "audio_session_id": sid, "listen": False, "talk": False})
        assert t.of("audio_error")[-1]["code"] == "bad_request"

    run(go())


def test_student_media_report_and_student_stop():
    async def go():
        hub, t, a, _ = await setup()
        sid, _ = await connected_session(hub, t, a)
        await hub.on_student_message("st-a", {"type": "audio_media", "audio_session_id": sid, "mic_live": True, "indicator_shown": True, "teacher_audio_playing": False, "connection": "connected"})
        assert t.last_state()["student_media"]["mic_live"] is True
        await hub.on_student_message("st-a", {"type": "audio_media", "audio_session_id": sid, "mic_live": False, "indicator_shown": False, "stopped": True})
        assert t.last_state()["state"] == "ended" and t.last_state()["reason"] == "student_stop"

    run(go())


def test_malformed_signals_rejected():
    async def go():
        hub, t, a, _ = await setup()
        sid, _ = await connected_session(hub, t, a)
        for bad in (
            {"kind": "offer", "sdp": "not sdp"},
            {"kind": "offer", "sdp": "v=0" + "x" * 70000},
            {"kind": "ice", "ice": {"candidate": 5}},
            {"kind": "ice", "ice": "x"},
            {"kind": "bye"},
        ):
            await hub.on_teacher_message(t, {"type": "audio_signal", "audio_session_id": sid, **bad})
            assert t.of("audio_error")[-1]["code"] == "bad_request"
        assert not a.of("audio_signal")

    run(go())
