"""Regression tests for findings of the A02 adversarial review (concurrency, lifecycle, replay, live)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import cv2
import pytest
from fastapi.testclient import TestClient

from proctor.capture import replay as replay_mod
from proctor.capture.replay import ReplaySource, sha256_file
from proctor.capture.service import _bounded_timeout
from proctor.capture.tests.helpers import camera_kwargs, frame_pattern, wait_until, write_manifest
from proctor_contracts.interfaces import CaptureError, SessionClock
from proctor_contracts.v1 import ErrorCode, SourceConfig, SourceMode

SYN = SourceConfig(mode=SourceMode.SYNTHETIC, fps=30)


def test_a01_flow_close_timeout_remove_add_never_runs_analyzer_concurrently(make_service):
    """A01: close() -> remove_consumer(name) -> end_session -> start_session -> add_consumer(name, new closure) -> open()."""
    svc = make_service()
    state = {"inside": 0, "max": 0, "ended": False, "processed_after_end": 0}
    lock = threading.Lock()

    class Analyzer:  # one process-wide analyzer, as in the real backend
        def process(self, frame):
            with lock:
                state["inside"] += 1
                state["max"] = max(state["max"], state["inside"])
                if state["ended"] and frame.session_id == "s-old":
                    state["processed_after_end"] += 1
            time.sleep(1.2 if frame.session_id == "s-old" else 0.01)
            with lock:
                state["inside"] -= 1

    analyzer = Analyzer()
    svc.add_consumer("phone", lambda f: analyzer.process(f))
    svc.open("s-old", SYN, SessionClock())
    assert wait_until(lambda: state["inside"] == 1)
    svc.close(timeout_s=0.2)  # times out: the worker is still inside process()
    t0 = time.monotonic()
    svc.remove_consumer("phone")  # must wait (bounded) for the leaked worker of that name
    assert state["inside"] == 0 and time.monotonic() - t0 < 2.0
    state["ended"] = True  # A01: analyzer.end_session()
    svc.add_consumer("phone", lambda f: analyzer.process(f))  # new Consumer object, same name
    svc.open("s-new", SYN, SessionClock())
    time.sleep(0.5)
    svc.close()
    assert state["max"] == 1
    assert state["processed_after_end"] == 0
    assert wait_until(lambda: svc.leaked_runs() == 0)


def test_same_name_lock_survives_reregistration_while_worker_is_leaked(make_service):
    """Even without remove_consumer waiting, a re-added name is serialized with the leaked worker."""
    svc = make_service()
    inside = {"n": 0, "max": 0}
    lock = threading.Lock()

    def cb(f):
        with lock:
            inside["n"] += 1
            inside["max"] = max(inside["max"], inside["n"])
        time.sleep(1.0 if f.session_id == "s-a" else 0.01)
        with lock:
            inside["n"] -= 1

    svc.add_consumer("att", cb)
    svc.open("s-a", SYN, SessionClock())
    assert wait_until(lambda: inside["n"] == 1)
    svc.close(timeout_s=0.1)
    svc.remove_consumer("att", timeout_s=0.1)  # gives up quickly: worker still leaked
    assert svc.leaked_runs() == 1 and svc.health().details.get("leaked_threads", 0) >= 1
    svc.add_consumer("att", cb)
    svc.open("s-b", SYN, SessionClock())
    time.sleep(1.3)
    svc.close()
    assert inside["max"] == 1
    assert wait_until(lambda: svc.leaked_runs() == 0)


def test_lockstep_waiting_for_a_slow_consumer_is_not_a_frame_stall(make_service, clip):
    rid = clip("lockslow", n=4, fps=30.0, pacing="lockstep")
    svc = make_service()
    events = []
    svc.set_health_listener(lambda h: events.append(h.code))
    svc.add_consumer("slow", lambda f: time.sleep(1.3))  # > stall threshold (1 s) per frame
    svc.open("s-ls", SourceConfig(mode=SourceMode.REPLAY, replay_id=rid), SessionClock())
    assert wait_until(lambda: svc.health().code == "replay_ended", timeout=10.0)
    svc.close()
    assert "frame_stall" not in events, events


def test_open_is_refused_while_close_is_still_stopping(make_service):
    svc = make_service()
    release = threading.Event()
    svc.add_consumer("slow", lambda f: release.wait(3.0))
    svc.open("s-c1", SYN, SessionClock())
    time.sleep(0.2)
    closer = threading.Thread(target=svc.close, kwargs={"timeout_s": 2.0})
    closer.start()
    assert wait_until(lambda: not svc.is_open)
    with pytest.raises(CaptureError) as exc:
        svc.open("s-c2", SYN, SessionClock())
    assert exc.value.code == ErrorCode.SESSION_ACTIVE and exc.value.details["reason"] == "capture_closing"
    release.set()
    closer.join(3.0)
    assert svc.health().code == "closed"
    svc.open("s-c2", SYN, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 3)
    assert svc.health().code == "running"  # the earlier close() did not overwrite the new run
    svc.close()


@pytest.mark.parametrize("timeout", [None, float("inf"), float("nan"), "bogus", -5])
def test_close_with_odd_timeouts_always_releases(make_service, timeout, thread_baseline):
    svc = make_service()
    svc.add_consumer("c", lambda f: None)
    svc.open("s-to", SYN, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 2)
    svc.close(timeout_s=timeout)
    assert svc.health().code == "closed" and not svc.is_open


def test_bounded_timeout():
    assert _bounded_timeout(None) == 30.0 and _bounded_timeout(float("inf")) == 30.0
    assert _bounded_timeout("x") == 3.0 and _bounded_timeout(-1) == 0.1 and _bounded_timeout(1.5) == 1.5


def test_stopped_run_never_gets_new_workers(make_service):
    svc = make_service()
    svc.open("s-w", SYN, SessionClock())
    run = svc._run
    svc.close()
    from proctor.capture.fanout import Consumer

    svc._start_worker_locked(run, Consumer(name="late", callback=lambda f: None, max_fps=None))
    assert "late" not in run.workers and all(not w.alive for w in run.all_workers)


def test_os_errors_while_preparing_replay_become_replay_invalid(make_service, monkeypatch, clip):
    rid = clip("oserr", n=5)
    svc = make_service()

    def boom(*a, **k):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(replay_mod, "_load_replay", boom)
    with pytest.raises(CaptureError) as exc:
        svc.open("s-os", SourceConfig(mode=SourceMode.REPLAY, replay_id=rid), SessionClock())
    assert exc.value.code == ErrorCode.REPLAY_INVALID and exc.value.details["reason"] == "media_unreadable"
    assert svc.health().code == "media_unreadable"

    def weird(self):
        raise KeyError("unexpected")

    monkeypatch.setattr(ReplaySource, "prepare", weird)
    with pytest.raises(CaptureError) as exc:
        svc.open("s-os2", SourceConfig(mode=SourceMode.REPLAY, replay_id=rid), SessionClock())
    assert exc.value.details["reason"] == "open_error" and svc.health().code == "open_error"
    assert not svc.is_open


def test_os_error_in_replay_fails_preflight_without_500_and_preflight_can_retry(tmp_path, monkeypatch):
    from proctor.app import create_app
    from proctor.settings import Settings

    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    write_manifest(replay_dir, "locked", {"kind": "video", "path": "media/x.avi"})
    monkeypatch.setattr(replay_mod, "_load_replay", lambda *a, **k: (_ for _ in ()).throw(OSError("EBUSY")))
    token = "r" * 48
    app = create_app(Settings(data_dir=tmp_path / "d", replay_dir=replay_dir, exam_path=tmp_path / "x.json"), token)
    with TestClient(app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {token}"}) as c:
        consent = {"accepted": True, "text_version": "v1", "accepted_at": "2026-10-08T09:00:00Z"}
        sid = c.post("/v1/sessions", json={"source": {"mode": "replay", "replay_id": "locked"}, "exam_id": "demo-exam-1", "consent": consent}).json()["session_id"]
        for _ in range(2):  # a retry must not hit "consumer already registered"
            r = c.post(f"/v1/sessions/{sid}/preflight")
            assert r.status_code == 200, r.text
            cam = {ch["check_id"]: ch for ch in r.json()["checks"]}["camera"]
            assert cam["status"] == "fail" and cam["message_code"] == "replay_invalid"
        c.post(f"/v1/sessions/{sid}/abort", json={"reason": "t"})


def test_long_trim_is_bounded_and_interruptible(make_service, settings, monkeypatch, thread_baseline):
    seq = settings.replay_dir / "media" / "longtrim"
    seq.mkdir(parents=True)
    for i in range(120):
        cv2.imwrite(str(seq / f"f{i:04d}.png"), frame_pattern(i, 64, 48))
    write_manifest(settings.replay_dir, "longtrim", {"kind": "image_sequence", "path": "media/longtrim", "timestamps": "fps", "fps": 10.0}, start_ms=11_000)
    original = replay_mod._SequenceReader.grab

    def slow_grab(self):
        time.sleep(0.03)  # ~3.3 s to skip 110 frames
        return original(self)

    monkeypatch.setattr(replay_mod._SequenceReader, "grab", slow_grab)
    svc = make_service(open_timeout_s=1.0)
    t0 = time.monotonic()
    with pytest.raises(CaptureError) as exc:
        svc.open("s-trim", SourceConfig(mode=SourceMode.REPLAY, replay_id="longtrim"), SessionClock())
    assert exc.value.code == ErrorCode.REPLAY_INVALID and exc.value.details["reason"] == "trim_too_slow"
    assert time.monotonic() - t0 < 2.5
    assert svc.leaked_runs() == 0


def test_busy_camera_on_two_backends_is_busy_not_timeout(make_service, device):
    device.delivers = False
    svc = make_service(camera_kwargs=camera_kwargs(device, backends=[101, 202], probe_timeout_s=3.0), open_timeout_s=2.0)
    t0 = time.monotonic()
    with pytest.raises(CaptureError) as exc:
        svc.open("s-busy", SourceConfig(mode=SourceMode.LIVE), SessionClock())
    assert exc.value.code == ErrorCode.CAMERA_BUSY and exc.value.details["reason"] == "camera_no_frames"
    assert "101:no_frames" in exc.value.details["backends_tried"] and "202:no_frames" in exc.value.details["backends_tried"]
    assert time.monotonic() - t0 < 3.0
    assert device.active_handles == 0


def test_shutdown_waits_for_zombies(make_service, device):
    svc = make_service(camera_kwargs=camera_kwargs(device))
    svc.open("s-sd", SourceConfig(mode=SourceMode.LIVE), SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 2)
    device.unblocked.clear()
    time.sleep(0.1)
    svc.close(timeout_s=0.1)
    assert svc.leaked_runs() == 1
    threading.Timer(0.3, device.unblocked.set).start()
    svc.shutdown(timeout_s=2.0)
    assert svc.leaked_runs() == 0 and device.active_handles == 0


def test_sha256_cache_invalidates_on_change(tmp_path):
    p = tmp_path / "m.bin"
    p.write_bytes(b"a" * 1000)
    h1 = sha256_file(p)
    assert sha256_file(p) == h1
    time.sleep(0.01)
    p.write_bytes(b"b" * 1001)
    os.utime(p)
    assert sha256_file(p) != h1
