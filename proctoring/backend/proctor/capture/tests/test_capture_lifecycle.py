"""Lifecycle: open/close/restart, ordering of health events, preview, ring, metrics, timeline."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import cv2
import numpy as np
import pytest

from proctor.capture import create_capture_service
from proctor.capture.tests.helpers import FakeDevice, camera_kwargs, wait_until
from proctor_contracts import interfaces as itf
from proctor_contracts.interfaces import CaptureError, SessionClock
from proctor_contracts.v1 import (
    ErrorCode,
    FramePacketMeta,
    HealthObservation,
    HealthStatus,
    PreviewFrameMeta,
    RuntimeMetrics,
    SourceConfig,
    SourceMode,
)

SYN = SourceConfig(mode=SourceMode.SYNTHETIC, fps=30)


def test_factory_satisfies_protocol(settings):
    svc = create_capture_service(settings)
    assert isinstance(svc, itf.CaptureService)
    h = svc.health()
    assert h.status == HealthStatus.STOPPED and h.code == "idle"
    assert svc.latest_preview() is None and svc.get_frame(0) is None
    m = svc.metrics()
    assert m.frames_captured == 0 and m.capture_fps == 0.0 and m.consumers == []
    svc.close()  # close before open: no-op


def test_restart_many_times_without_leaks(make_service, thread_baseline):
    svc = make_service()
    events: list[tuple[str, str]] = []
    svc.set_health_listener(lambda h: events.append((h.status.value, h.code)))
    firsts: list[tuple[str, int]] = []
    svc.add_consumer("c", lambda p: firsts.append((p.session_id, p.frame_id)))
    base = threading.active_count()
    for i in range(6):
        sid = f"s-restart-{i}"
        svc.open(sid, SYN, SessionClock())
        assert wait_until(lambda: any(s == sid for s, _ in firsts))
        svc.close()
        assert wait_until(lambda: threading.active_count() <= base)
    for i in range(6):
        ids = [f for s, f in firsts if s == f"s-restart-{i}"]
        assert ids[0] == 0 and ids == sorted(ids)  # frame_id restarts at 0 per session
    assert events == [("starting", "opening"), ("ok", "running"), ("stopped", "closed")] * 6
    assert svc.health().code == "closed"
    assert svc.leaked_runs() == 0


def test_open_while_open_is_rejected(make_service):
    svc = make_service()
    svc.open("s-a", SYN, SessionClock())
    with pytest.raises(CaptureError) as exc:
        svc.open("s-b", SYN, SessionClock())
    assert exc.value.code == ErrorCode.SESSION_ACTIVE
    svc.close()
    svc.close()  # idempotent
    svc.open("s-b", SYN, SessionClock())
    svc.close()


def test_invalid_session_id_rejected(make_service):
    svc = make_service()
    with pytest.raises(CaptureError) as exc:
        svc.open("../etc", SYN, SessionClock())
    assert exc.value.code == ErrorCode.INVALID_ARGUMENT
    assert not svc.is_open


def test_close_during_slow_callback_and_restart_never_runs_callback_concurrently(make_service):
    svc = make_service()
    inside = {"n": 0, "max": 0}
    lock = threading.Lock()
    calls: list[str] = []

    def slow(p):
        with lock:
            inside["n"] += 1
            inside["max"] = max(inside["max"], inside["n"])
        calls.append(p.session_id)
        time.sleep(1.5 if p.session_id == "s-slow-1" else 0.01)
        with lock:
            inside["n"] -= 1

    svc.add_consumer("slow", slow)
    svc.open("s-slow-1", SYN, SessionClock())
    assert wait_until(lambda: "s-slow-1" in calls)
    t0 = time.monotonic()
    svc.close(timeout_s=0.3)
    assert time.monotonic() - t0 < 1.0  # close honours its timeout even if a callback hangs
    assert svc.health().details.get("leaked_threads", 0) >= 1
    svc.open("s-slow-2", SYN, SessionClock())  # restart while the old callback still runs
    assert wait_until(lambda: "s-slow-2" in calls, timeout=4.0)
    svc.close()
    assert inside["max"] == 1  # never concurrent with itself across the restart
    assert wait_until(lambda: svc.leaked_runs() == 0, timeout=3.0)


def test_listener_errors_do_not_break_capture(make_service):
    svc = make_service()

    def bad(h):
        raise RuntimeError("listener bug")

    svc.set_health_listener(bad)
    svc.open("s-listener", SYN, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 3)
    svc.close()
    assert svc.health().code == "closed"


def test_health_events_are_ordered_under_concurrent_transitions(make_service, device):
    """Watchdog (stall) and capture thread (recovery) both change state: listener order = state order."""
    svc = make_service(camera_kwargs=camera_kwargs(device))
    events: list[tuple[str, str]] = []
    svc.set_health_listener(lambda h: events.append((h.status.value, h.code)))
    svc.open("s-order", SourceConfig(mode=SourceMode.LIVE), SessionClock())
    for _ in range(2):
        device.unblocked.clear()
        assert wait_until(lambda: svc.health().code == "frame_stall", timeout=3.0)
        device.unblocked.set()
        assert wait_until(lambda: svc.health().code == "running", timeout=3.0)
    svc.close()
    assert events == [
        ("starting", "opening"),
        ("ok", "running"),
        ("degraded", "frame_stall"),
        ("ok", "running"),
        ("degraded", "frame_stall"),
        ("ok", "running"),
        ("stopped", "closed"),
    ]


def test_health_listener_payload_maps_to_health_observation(make_service):
    """A01 wraps every capture Health into a HealthObservation: payloads must validate."""
    svc = make_service()
    got = []
    clock = SessionClock()
    svc.set_health_listener(got.append)
    svc.open("s-hobs", SYN, clock)
    svc.close()
    for h in got:
        HealthObservation(
            observation_id="h-1",
            session_id="s-hobs",
            frame_id=None,
            t_session_ms=h.since_t_session_ms or 0.0,
            wall_time=clock.wall_at(h.since_t_session_ms or 0.0),
            source_mode=SourceMode.SYNTHETIC,
            producer={"module": "capture", "version": "test"},
            status="ok",
            health=h,
        )
        assert h.since_t_session_ms is not None


def test_preview_jpeg_and_meta(make_service, settings):
    svc = make_service()
    svc.open("s-prev", SourceConfig(mode=SourceMode.SYNTHETIC, width=640, height=480, fps=30), SessionClock())
    assert wait_until(lambda: svc.latest_preview() is not None)
    time.sleep(1.0)
    meta, data = svc.latest_preview()
    PreviewFrameMeta.model_validate(meta.model_dump())
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (480, 640, 3)
    assert meta.byte_length == len(data) and meta.mirrored is False and meta.source_mode == SourceMode.SYNTHETIC
    frame = svc.get_frame(meta.frame_id)
    assert frame is not None and frame.t_session_ms == meta.t_session_ms  # overlay can match frame_id
    prev = {c.name: c for c in svc.metrics().consumers}["preview"]
    assert prev.processed_fps <= settings.preview_fps + 1.5
    svc.close()
    assert svc.latest_preview() is None


def test_preview_is_downscaled_for_large_frames(make_service):
    svc = make_service()
    svc.open("s-prev-big", SourceConfig(mode=SourceMode.SYNTHETIC, width=1920, height=1080, fps=10), SessionClock())
    assert wait_until(lambda: svc.latest_preview() is not None)
    meta, data = svc.latest_preview()
    svc.close()
    assert (meta.width, meta.height) == (960, 540)
    assert cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).shape == (540, 960, 3)


def test_ring_buffer_eviction(make_service, settings):
    from dataclasses import replace

    svc = make_service(settings=replace(settings, frame_ring_seconds=0.5))
    svc.open("s-ring", SYN, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 40)
    last = svc.health().details["last_frame_id"]
    assert svc.get_frame(0) is None  # evicted (older than 0.5 s)
    recent = svc.get_frame(last)
    assert recent is not None and recent.frame_id == last
    svc.close()
    assert svc.get_frame(last) is None


def test_metrics_are_measured_not_nominal(make_service):
    svc = make_service()
    svc.add_consumer("work", lambda p: time.sleep(0.01))
    svc.open("s-fps", SourceConfig(mode=SourceMode.SYNTHETIC, fps=12), SessionClock())
    time.sleep(2.0)
    m = svc.metrics()
    svc.close()
    RuntimeMetrics.model_validate(m.model_dump())
    assert 10.0 <= m.capture_fps <= 13.5, m.capture_fps  # measured ~12, never a painted 30
    assert m.session_id == "s-fps" and 0 < m.window_s <= 5.0
    assert m.e2e_latency_ms_p50 is not None and m.e2e_latency_ms_p50 >= 10.0
    assert m.e2e_latency_ms_p95 >= m.e2e_latency_ms_p50
    work = {c.name: c for c in m.consumers}["work"]
    assert work.process_ms_p50 >= 10.0 and work.frame_age_ms_p50 is not None


def test_no_consumers_means_no_e2e_latency(make_service):
    svc = make_service()
    svc.open("s-none", SYN, SessionClock())
    time.sleep(0.4)
    m = svc.metrics()
    svc.close()
    assert m.e2e_latency_ms_p50 is None and m.e2e_latency_ms_p95 is None  # preview does not count


def test_synthetic_timeline_and_meta(make_service):
    svc = make_service()
    clock = SessionClock()
    time.sleep(0.2)  # session created before capture opens (as in the real lifecycle)
    metas: list[FramePacketMeta] = []
    svc.add_consumer("m", lambda p: metas.append(p.meta))
    svc.open("s-time", SYN, clock)
    assert wait_until(lambda: len(metas) >= 10)
    now = clock.now_ms()
    svc.close()
    ts = [m.t_session_ms for m in metas]
    assert ts == sorted(ts) and ts[0] >= 200.0 and ts[-1] <= now
    for m in metas:
        FramePacketMeta.model_validate(m.model_dump())
        assert m.wall_time == clock.wall_at(m.t_session_ms)
        assert m.source_mode == SourceMode.SYNTHETIC and m.source_id.startswith("synthetic:")
    assert abs((metas[1].wall_time - metas[0].wall_time) - timedelta(milliseconds=ts[1] - ts[0])) < timedelta(milliseconds=1)


def test_settings_defaults_apply_when_source_fields_not_given(settings, make_service):
    from dataclasses import replace

    svc = make_service(settings=replace(settings, synthetic_fps=10.0, capture_width=320, capture_height=240))
    metas = []
    svc.add_consumer("m", lambda p: metas.append(p.meta))
    svc.open("s-defaults", SourceConfig(mode=SourceMode.SYNTHETIC), SessionClock())
    time.sleep(1.5)
    fps = svc.metrics().capture_fps
    svc.close()
    assert (metas[0].width, metas[0].height) == (320, 240)
    assert 8.0 <= fps <= 11.5


def test_api_calls_do_not_block_while_consumers_are_busy(make_service):
    """Inference never blocks callers of preview/metrics/health (e.g. the asyncio loop)."""
    svc = make_service()
    svc.add_consumer("hog", lambda p: time.sleep(0.5))
    svc.open("s-nonblock", SYN, SessionClock())
    time.sleep(0.6)
    worst = 0.0
    for _ in range(50):
        t0 = time.perf_counter()
        svc.latest_preview()
        svc.metrics()
        svc.health()
        worst = max(worst, time.perf_counter() - t0)
    svc.close()
    assert worst < 0.05, worst


def test_live_zombie_blocks_reopen_until_released(make_service, device):
    """Stop during a hung read: close() returns on time, the device stays owned until read returns."""
    svc = make_service(camera_kwargs=camera_kwargs(device))
    live = SourceConfig(mode=SourceMode.LIVE)
    svc.open("s-z1", live, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 2)
    device.unblocked.clear()  # next read() hangs
    time.sleep(0.2)
    t0 = time.monotonic()
    svc.close(timeout_s=0.4)
    assert time.monotonic() - t0 < 1.0
    with pytest.raises(CaptureError) as exc:
        svc.open("s-z2", live, SessionClock())
    assert exc.value.code == ErrorCode.CAMERA_BUSY and exc.value.details["reason"] == "previous_capture_releasing"
    device.unblocked.set()
    assert wait_until(lambda: svc.leaked_runs() == 0)
    assert wait_until(lambda: device.active_handles == 0)
    svc.open("s-z2", live, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 2)
    svc.close()
    assert device.active_handles == 0


def test_synthetic_open_does_not_wait_for_live_zombie(make_service, device):
    svc = make_service(camera_kwargs=camera_kwargs(device))
    svc.open("s-z3", SourceConfig(mode=SourceMode.LIVE), SessionClock())
    device.unblocked.clear()
    time.sleep(0.2)
    svc.close(timeout_s=0.2)
    svc.open("s-z4", SYN, SessionClock())  # synthetic does not need the device
    assert wait_until(lambda: svc.metrics().frames_captured > 2)
    svc.close()
    device.unblocked.set()


def test_fake_device_helper_sanity():
    d = FakeDevice()
    cap = d.opener(0, 1)
    ok, img = cap.read()
    assert ok and img.shape == (480, 640, 3)
    cap.release()
    assert d.active_handles == 0
