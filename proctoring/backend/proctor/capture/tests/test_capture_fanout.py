"""Fan-out: latest-frame mailboxes, slow consumers, rate limits, errors, array ownership."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from proctor.capture import PREVIEW_CONSUMER
from proctor.capture.tests.helpers import wait_until
from proctor_contracts.interfaces import SessionClock
from proctor_contracts.v1 import HealthStatus, SourceConfig, SourceMode

SYN = SourceConfig(mode=SourceMode.SYNTHETIC, fps=30)


def _metrics_by_name(svc):
    return {c.name: c for c in svc.metrics().consumers}


def test_slow_consumer_does_not_block_capture_or_fast_consumer(make_service, thread_baseline):
    svc = make_service()
    fast_ids: list[int] = []
    slow_ids: list[int] = []
    slow_ages: list[float] = []

    def slow(p):
        slow_ages.append((time.monotonic_ns() - p.meta.t_capture_mono_ns) / 1e6)
        time.sleep(0.25)  # "YOLO" at ~4 FPS
        slow_ids.append(p.frame_id)

    svc.add_consumer("fast", lambda p: fast_ids.append(p.frame_id))
    svc.add_consumer("slow", slow)
    svc.open("s-slow", SYN, SessionClock())
    time.sleep(2.0)
    m = svc.metrics()
    by = _metrics_by_name(svc)
    svc.close()

    assert m.capture_fps >= 24, m  # capture keeps its pace
    assert by["fast"].frames_processed >= 0.9 * (m.frames_captured - 2)
    assert by["slow"].frames_processed <= 10 and by["slow"].frames_skipped > 20
    # latest-frame policy: the slow consumer always starts on a FRESH frame (no backlog)
    assert max(slow_ages) < 120.0, slow_ages  # a backlog would be >= 250 ms
    assert slow_ids == sorted(set(slow_ids)) and len(slow_ids) >= 5
    gaps = np.diff(slow_ids)
    assert gaps.min() >= 5  # ~7 frames arrive during each 250 ms callback
    # preview (internal consumer) is not starved by the slow consumer
    assert by[PREVIEW_CONSUMER].processed_fps >= 10


def test_latest_frame_after_long_block_is_fresh(make_service):
    svc = make_service()
    seen: list[tuple[int, float]] = []
    release = threading.Event()

    def blocker(p):
        if not seen:
            seen.append((p.frame_id, 0.0))
            release.wait(2.0)
            return
        seen.append((p.frame_id, (time.monotonic_ns() - p.meta.t_capture_mono_ns) / 1e6))

    svc.add_consumer("blocker", blocker)
    svc.open("s-latest", SYN, SessionClock())
    time.sleep(1.0)
    captured_before_release = svc.metrics().frames_captured
    release.set()
    assert wait_until(lambda: len(seen) >= 3)
    svc.close()
    second_id, second_age = seen[1]
    assert second_id >= captured_before_release - 3  # not frame 1, the newest one
    assert second_age < 120.0


def test_max_fps_rate_limit(make_service):
    svc = make_service()
    svc.add_consumer("limited", lambda p: None, max_fps=5)
    svc.open("s-rate", SYN, SessionClock())
    time.sleep(2.2)
    by = _metrics_by_name(svc)
    svc.close()
    assert 3.5 <= by["limited"].processed_fps <= 6.0, by["limited"]
    assert by["limited"].frames_skipped > 20


def test_rate_limited_consumer_processes_fresh_frames(make_service):
    """The limit is waited out BEFORE taking the frame: waiting never ages the frame."""
    svc = make_service()
    svc.add_consumer("limited", lambda p: None, max_fps=4)
    svc.open("s-rate-age", SYN, SessionClock())
    time.sleep(2.0)
    c = _metrics_by_name(svc)["limited"]
    svc.close()
    # a frame taken after the wait is at most ~1 capture interval (33 ms) old; the bug this guards
    # against (take, then sleep out the limit) gives ~250 ms. 100 ms leaves room for a loaded CI box.
    assert c.frame_age_ms_p95 is not None and c.frame_age_ms_p95 < 100.0, c


def test_consumer_exceptions_are_counted_and_isolated(make_service):
    svc = make_service()
    ok_ids: list[int] = []

    def broken(p):
        raise RuntimeError("analyzer bug")

    svc.add_consumer("broken", broken)
    svc.add_consumer("ok", lambda p: ok_ids.append(p.frame_id))
    svc.open("s-err", SYN, SessionClock())
    time.sleep(0.6)
    by = _metrics_by_name(svc)
    health = svc.health()
    svc.close()
    assert by["broken"].errors > 5 and by["broken"].frames_processed == 0
    assert by["ok"].errors == 0 and len(ok_ids) > 5
    assert health.status == HealthStatus.OK


def test_callbacks_never_concurrent_and_ids_strictly_increasing(make_service):
    svc = make_service()
    active = {"n": 0, "max": 0}
    lock = threading.Lock()
    ids: dict[str, list[int]] = {"a": [], "b": []}

    def make(name, delay):
        def cb(p):
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(delay)
            ids[name].append(p.frame_id)
            with lock:
                active["n"] -= 1

        return cb

    svc.add_consumer("a", make("a", 0.0))
    svc.add_consumer("b", make("b", 0.07))
    svc.open("s-order", SYN, SessionClock())
    time.sleep(1.0)
    svc.close()
    for name, seq in ids.items():
        assert seq == sorted(seq) and len(seq) == len(set(seq)), name
    # two consumers may overlap with each other, but never with themselves
    assert active["max"] <= 2


def test_out_of_order_completion_is_attributed_by_frame_id(make_service):
    """A slow consumer finishing late must still be tied to ITS frame (overlay matching)."""
    svc = make_service()
    done: list[tuple[str, int, float]] = []

    def slow(p):
        time.sleep(0.3)
        done.append(("slow", p.frame_id, p.t_session_ms))

    svc.add_consumer("slow", slow)
    svc.add_consumer("fast", lambda p: done.append(("fast", p.frame_id, p.t_session_ms)))
    svc.open("s-ooo", SYN, SessionClock())
    time.sleep(1.0)
    snapshot = list(done)
    ring_hits = [svc.get_frame(fid) for name, fid, _ in snapshot[-5:]]
    svc.close()
    slow_done = [(i, fid) for i, (n, fid, _) in enumerate(snapshot) if n == "slow"]
    assert slow_done, "slow consumer produced nothing"
    i, fid = slow_done[0]
    later_fast = [f for n, f, _ in snapshot[:i] if n == "fast"]
    assert later_fast and max(later_fast) > fid  # completions arrive out of order across consumers
    assert all(p is not None for p in ring_hits)
    for (_, fid2, t), packet in zip(snapshot[-5:], ring_hits):
        assert packet.frame_id == fid2 and packet.t_session_ms == t


def test_add_after_open_and_remove_during_run(make_service, thread_baseline):
    svc = make_service()
    svc.open("s-dyn", SYN, SessionClock())
    got: list[int] = []
    svc.add_consumer("late", lambda p: got.append(p.frame_id))
    assert wait_until(lambda: len(got) >= 3)
    svc.remove_consumer("late")
    n = len(got)
    time.sleep(0.3)
    assert len(got) <= n + 1
    assert "late" not in _metrics_by_name(svc)
    svc.remove_consumer("never-registered")  # no-op
    svc.close()


def test_consumer_registration_validation(make_service):
    svc = make_service()
    svc.add_consumer("x", lambda p: None)
    with pytest.raises(ValueError):
        svc.add_consumer("x", lambda p: None)
    with pytest.raises(ValueError):
        svc.add_consumer(PREVIEW_CONSUMER, lambda p: None)
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            svc.add_consumer(f"bad{bad}", lambda p: None, max_fps=bad)
    with pytest.raises(ValueError):
        svc.add_consumer("", lambda p: None)
    with pytest.raises(ValueError):
        svc.add_consumer("y" * 65, lambda p: None)


def test_frames_are_shared_read_only_bgr(make_service):
    svc = make_service()
    packets: dict[str, dict[int, object]] = {"a": {}, "b": {}}
    write_errors: list[str] = []

    def make(name):
        def cb(p):
            packets[name][p.frame_id] = p
            if name == "a" and p.frame_id == 1:
                try:
                    p.image[0, 0, 0] = 1
                except ValueError:
                    write_errors.append("write")
                try:
                    p.image.flags.writeable = True
                except ValueError:
                    write_errors.append("flag")

        return cb

    svc.add_consumer("a", make("a"))
    svc.add_consumer("b", make("b"))
    svc.open("s-ro", SourceConfig(mode=SourceMode.SYNTHETIC, width=320, height=240, fps=30), SessionClock())
    assert wait_until(lambda: len(set(packets["a"]) & set(packets["b"])) >= 3)
    svc.close()
    common = sorted(set(packets["a"]) & set(packets["b"]))
    for fid in common:
        pa, pb = packets["a"][fid], packets["b"][fid]
        assert pa.image is pb.image  # shared, not copied
        img = pa.image
        assert img.dtype == np.uint8 and img.shape == (240, 320, 3) and img.flags.c_contiguous
        assert not img.flags.writeable
        assert pa.meta.color_format == "BGR" and pa.meta.mirrored is False
        assert (pa.meta.width, pa.meta.height) == (320, 240)
    assert write_errors == ["write", "flag"]
