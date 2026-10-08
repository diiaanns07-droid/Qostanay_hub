"""Preview cadence/backpressure with in-memory frames and sockets; no devices or native guards."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import time
from types import SimpleNamespace as NS

import pytest

from proctor.uplink.backend_view import BackendView, Snapshot
from proctor.uplink.client import Uplink
from proctor.uplink.config import config_from_env
from proctor.uplink.tests.test_uplink import make_cfg


@pytest.mark.parametrize("fps, interval", [(None, 2.0), ("0.5", 2.0), ("1", 1.0), ("2.5", 0.4), ("5", 0.2)])
def test_preview_fps_env_is_bounded_and_keeps_large_class_default(tmp_path, fps, interval):
    env = {"QORGAU_CLASS_SERVER": "127.0.0.1:8765", "QORGAU_CLASS_CODE": "123456"}
    if fps is not None:
        env["QORGAU_CLASS_PREVIEW_FPS"] = fps
    cfg = config_from_env(tmp_path, env)
    assert cfg.preview_interval_s == interval
    assert cfg.status_interval_s == 2.0 and cfg.poll_interval_s == 0.5


@pytest.mark.parametrize("fps", ["0", "-1", "0.49", "5.01", "nan", "inf", "-inf", "5,0", "", "bad"])
def test_invalid_preview_fps_refused(tmp_path, fps):
    with pytest.raises(ValueError, match="QORGAU_CLASS_PREVIEW_FPS"):
        config_from_env(tmp_path, {"QORGAU_CLASS_SERVER": "a:1", "QORGAU_CLASS_CODE": "123456", "QORGAU_CLASS_PREVIEW_FPS": fps})


class View:
    def __init__(self, advance=False):
        self.frame = 1
        self.calls = 0
        self.advance = advance

    def preview_packet(self):
        self.calls += 1
        if self.advance:
            self.frame += 1
        return bytes([self.frame]), {"source_session_id": "s-1", "source_mode": "synthetic",
                                    "frame_wall": (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self.frame)).isoformat()}


class Socket:
    def __init__(self):
        self.sent = []
        self.buffered = 0
        self.transport = NS(get_write_buffer_size=lambda: self.buffered)

    async def send(self, raw):
        self.sent.append((time.monotonic(), json.loads(raw)))

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Future()


def initialized(tmp_path, view):
    cfg = replace(make_cfg(tmp_path, "127.0.0.1:8765"), preview_interval_s=0.2, poll_interval_s=0.5, status_interval_s=2.0)
    up = Uplink(cfg, view)
    up._snap = Snapshot(session_id="s-1", exam_state="running")
    up._send_lock = asyncio.Lock()
    up._flush_lock = asyncio.Lock()
    up._stop = asyncio.Event()
    up._outbox_signal = asyncio.Event()
    return up


def test_five_fps_runs_independently_of_half_second_status_poll(tmp_path):
    async def scenario():
        up, socket = initialized(tmp_path, View(advance=True)), Socket()
        try:
            loop = asyncio.get_running_loop()
            timer = loop.call_later(0.75, up._stop.set)
            await up._session(socket)
            timer.cancel()
            frames = [t for t, msg in socket.sent if msg["type"] == "preview"]
            statuses = [msg for _, msg in socket.sent if msg["type"] == "status"]
            assert 3 <= len(frames) <= 4
            assert all(b - a >= 0.17 for a, b in zip(frames, frames[1:])), frames
            assert len(statuses) == 1
            assert all("jpeg_b64" not in msg for msg in up.sent)
        finally:
            up.outbox.close()
    asyncio.run(scenario())


def test_no_duplicates_and_no_frame_queued_while_control_or_transport_is_busy(tmp_path):
    async def scenario():
        view, socket = View(), Socket()
        up = initialized(tmp_path, view)
        try:
            key = await up._preview_once(socket, None)
            assert await up._preview_once(socket, key) == key
            assert len(socket.sent) == 1
            async with up._send_lock:
                view.frame = 2
                calls = view.calls
                assert await up._preview_once(socket, key) == key
                assert view.calls == calls
            socket.buffered = 1
            view.frame = 3
            assert await up._preview_once(socket, key) == key
            assert view.calls == calls
            socket.buffered = 0
            view.frame = 4
            await up._preview_once(socket, key)
            assert [msg["jpeg_b64"] for _, msg in socket.sent] == ["AQ==", "BA=="]
            assert up.outbox.pending() == []
        finally:
            up.outbox.close()
    asyncio.run(scenario())


def test_slow_send_has_one_in_flight_frame_and_next_is_newest(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        view = View()
        class SlowSocket(Socket):
            async def send(self, raw):
                if not self.sent:
                    entered.set()
                    await release.wait()
                await super().send(raw)
        up, socket = initialized(tmp_path, view), SlowSocket()
        try:
            task = asyncio.create_task(up._preview_once(socket, None))
            await asyncio.wait_for(entered.wait(), 1)
            view.frame = 2
            assert await up._preview_once(socket, None) is None
            view.frame = 3
            assert view.calls == 1
            release.set()
            key = await task
            await up._preview_once(socket, key)
            assert [msg["jpeg_b64"] for _, msg in socket.sent] == ["AQ==", "Aw=="]
        finally:
            up.outbox.close()
    asyncio.run(scenario())


def test_stalled_send_times_out_and_session_cancels_workers(tmp_path, monkeypatch):
    monkeypatch.setattr("proctor.uplink.client.PREVIEW_SEND_TIMEOUT_S", 0.03)
    async def scenario():
        class StalledSocket(Socket):
            async def send(self, raw):
                if json.loads(raw)["type"] == "preview":
                    await asyncio.Future()
                await super().send(raw)
        up, socket = initialized(tmp_path, View()), StalledSocket()
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(up._session(socket), 1)
            assert not up._send_lock.locked()
            assert not [m for _, m in socket.sent if m["type"] == "preview"]
            assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        finally:
            up.outbox.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("new_state,new_sid", [("paused", "s-1"), ("running", "s-2")])
def test_transition_during_encode_drops_old_frame(tmp_path, new_state, new_sid):
    async def scenario():
        class ChangingView(View):
            def preview_packet(self):
                packet = super().preview_packet()
                up._snap = Snapshot(session_id=new_sid, exam_state=new_state)
                return packet
        up, socket = initialized(tmp_path, ChangingView()), Socket()
        try:
            assert await up._preview_once(socket, None) is None
            assert socket.sent == []
        finally:
            up.outbox.close()
    asyncio.run(scenario())


def test_backend_encodes_each_frame_once_and_cache_is_session_scoped(monkeypatch):
    encoded = []
    def shrink(data, *_):
        encoded.append(data)
        return data
    monkeypatch.setattr("proctor.uplink.backend_view.shrink_jpeg", shrink)
    frame = NS(session_id="s-1", frame_id=1, source_mode="synthetic", wall_time=datetime.now(timezone.utc))
    runtime = NS(preview=lambda: (frame, b"jpeg"))
    view = BackendView(lambda: NS(active_session_id=lambda: frame.session_id, runtime=lambda _: runtime))
    first = view.preview_packet()
    assert view.preview_packet() is first and len(encoded) == 1
    frame.frame_id = 2
    view.preview_packet()
    frame.session_id = "s-2"
    packet = view.preview_packet()
    assert len(encoded) == 3 and packet[1]["source_session_id"] == "s-2"
