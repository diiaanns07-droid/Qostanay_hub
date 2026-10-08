"""Regression coverage for C2 rejoining and renderer class-state recovery.

No camera, microphone, Electron or native hooks are used.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from proctor.app import StreamHub
from proctor.uplink.client import ClassStateMsg, Uplink
from proctor.uplink.tests.test_uplink import FakeView, make_cfg


class HandshakeSocket:
    def __init__(self, response):
        self.response = response
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        return json.dumps(self.response)


@pytest.mark.parametrize("error_code", ["resume_rejected", "join_rejected"])
def test_stale_resume_token_retries_current_code_once(tmp_path, error_code):
    up = Uplink(make_cfg(tmp_path, "127.0.0.1:8765", code="654321"), FakeView(tmp_path))
    up.outbox.set_meta("resume_token", "a" * 64)
    try:
        async def check():
            rejected = HandshakeSocket({"type": "error", "code": error_code})
            assert await up._handshake(rejected) == "retry_with_code"
            assert rejected.sent[0]["resume_token"] == "a" * 64
            assert "join_code" not in rejected.sent[0]
            assert up.outbox.get_meta("resume_token") is None

            # The next connection sends exactly the current code, with no old token.
            accepted = HandshakeSocket({"type": "welcome", "student_id": "new-student", "resume_token": "b" * 64})
            assert await up._handshake(accepted, force_code=True) == "ok"
            assert accepted.sent[0]["join_code"] == "654321"
            assert "resume_token" not in accepted.sent[0]
            assert up.student_id == "new-student"
            assert up.outbox.get_meta("resume_token") == "b" * 64

            # A rejected join code terminates attempts rather than retrying forever.
            wrong_code = HandshakeSocket({"type": "error", "code": "join_rejected"})
            assert await up._handshake(wrong_code, force_code=True) == "rejected"

        asyncio.run(check())
    finally:
        up.stop()


def test_rate_limit_does_not_discard_resume_identity(tmp_path):
    up = Uplink(make_cfg(tmp_path, "127.0.0.1:8765"), FakeView(tmp_path))
    up.outbox.set_meta("resume_token", "a" * 64)
    try:
        socket = HandshakeSocket({"type": "error", "code": "join_rate_limited"})
        assert asyncio.run(up._handshake(socket)) == "error"
        assert up.outbox.get_meta("resume_token") == "a" * 64
    finally:
        up.stop()


class StreamSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, text):
        self.outgoing.put_nowait(json.loads(text))


def class_state(**changes):
    return ClassStateMsg(connection="connected", server="127.0.0.1:8765", student_id="st-1", **changes)


async def drain_callbacks():
    """Deterministic barrier after thread-safe publications, without timing sleeps."""
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    loop.call_soon(done.set_result, None)
    await done


async def read_initial(hub, count):
    socket = StreamSocket()
    # class_state is global: even a session-filtered subscriber must receive it.
    serving = asyncio.create_task(hub.serve(socket, "some-local-session"))
    try:
        return [await asyncio.wait_for(socket.outgoing.get(), 1) for _ in range(count)]
    finally:
        socket.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(serving, 2)


def test_late_subscriber_gets_current_class_state_after_hello():
    async def check():
        hub = StreamHub()
        hub.bind_loop(asyncio.get_running_loop())
        expected = class_state(locked=True, lock_reason_ru="Проверка преподавателя")
        # Match uplink's worker-thread publication before Electron connects.
        await asyncio.to_thread(hub.publish, expected, None)
        await drain_callbacks()
        frames = await read_initial(hub, 2)
        assert [f["message"]["type"] for f in frames] == ["hello", "class_state"]
        assert [f["seq"] for f in frames] == [1, 2]
        assert frames[1]["session_id"] is None
        assert frames[1]["message"] == expected.model_dump(mode="json")
        assert all(f["contract"] == "qorgau.v1" for f in frames)

    asyncio.run(check())


def test_refresh_replays_only_latest_state_and_live_changes_still_arrive():
    async def check():
        hub = StreamHub()
        hub.bind_loop(asyncio.get_running_loop())
        hub.publish(class_state(locked=True, lock_reason_ru="Причина"), None)
        await drain_callbacks()
        assert (await read_initial(hub, 2))[1]["message"]["locked"] is True

        latest = class_state(locked=False)
        hub.publish(latest, None)  # no subscribers during renderer refresh
        await drain_callbacks()
        socket = StreamSocket()
        serving = asyncio.create_task(hub.serve(socket, None))
        try:
            hello = await asyncio.wait_for(socket.outgoing.get(), 1)
            snapshot = await asyncio.wait_for(socket.outgoing.get(), 1)
            assert hello["seq"] == 1
            assert snapshot["seq"] == 2 and snapshot["message"] == latest.model_dump(mode="json")
            hub.publish(class_state(locked=True, lock_reason_ru="Новая причина"), None)
            update = await asyncio.wait_for(socket.outgoing.get(), 1)
            assert update["seq"] == 3 and update["message"]["lock_reason_ru"] == "Новая причина"
        finally:
            socket.incoming.put_nowait({"type": "websocket.disconnect"})
            await asyncio.wait_for(serving, 2)

    asyncio.run(check())
