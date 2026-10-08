"""Live camera path on a FAKE device: open failures, busy, disconnect/reconnect, stalls, formats.

These tests exercise the real CameraSource/FrameCaptureService code with an emulated OpenCV
VideoCapture. They do NOT prove anything about a physical camera (see verify_live.py).
"""

from __future__ import annotations

import time

import pytest

from proctor.capture.sources import CameraSource
from proctor.capture.tests.helpers import camera_kwargs, wait_until
from proctor_contracts.interfaces import CaptureError, SessionClock
from proctor_contracts.v1 import ErrorCode, HealthStatus, SourceConfig, SourceMode

LIVE = SourceConfig(mode=SourceMode.LIVE)


def _open_error(svc) -> CaptureError:
    with pytest.raises(CaptureError) as exc:
        svc.open("s-cam", LIVE, SessionClock())
    return exc.value


def test_no_camera_device(make_service, device, thread_baseline):
    device.connected = False
    svc = make_service(camera_kwargs=camera_kwargs(device, device_path_probe=lambda i: (False, False), platform="linux"))
    err = _open_error(svc)
    assert err.code == ErrorCode.CAMERA_UNAVAILABLE and err.details["reason"] == "camera_not_found"
    h = svc.health()
    assert h.status == HealthStatus.UNAVAILABLE and h.code == "camera_not_found"
    assert not svc.is_open and device.active_handles == 0


def test_permission_denied_linux(make_service, device):
    device.connected = False
    svc = make_service(camera_kwargs=camera_kwargs(device, device_path_probe=lambda i: (True, False), platform="linux"))
    err = _open_error(svc)
    assert err.code == ErrorCode.CAMERA_DENIED and err.details["reason"] == "camera_permission_denied"
    assert svc.health().code == "camera_permission_denied"


def test_windows_privacy_denied(make_service, device):
    device.connected = False
    svc = make_service(camera_kwargs=camera_kwargs(device, platform="win32", consent_probe=lambda: "Deny"))
    err = _open_error(svc)
    assert err.code == ErrorCode.CAMERA_DENIED and err.details["reason"] == "camera_privacy_denied"


def test_windows_open_failed_when_consent_unknown(make_service, device):
    device.connected = False
    svc = make_service(camera_kwargs=camera_kwargs(device, platform="win32", consent_probe=lambda: None))
    err = _open_error(svc)
    assert err.code == ErrorCode.CAMERA_UNAVAILABLE and err.details["reason"] == "camera_open_failed" and err.retryable


def test_opened_but_no_frames_is_busy(make_service, device, thread_baseline):
    device.delivers = False
    svc = make_service(camera_kwargs=camera_kwargs(device, probe_timeout_s=0.4))
    err = _open_error(svc)
    assert err.code == ErrorCode.CAMERA_BUSY and err.details["reason"] == "camera_no_frames" and err.retryable
    assert device.active_handles == 0  # released after the failed probe


def test_backend_fallback(make_service, device):
    device.fail_backends = {101}
    svc = make_service(camera_kwargs=camera_kwargs(device, backends=[101, 202]))
    svc.open("s-cam", LIVE, SessionClock())
    assert device.backends_seen[:2] == [101, 202]
    d = svc.health().details
    assert d["backend"] == "FAKE" and d["requested"] == "640x480@30" and d["actual_width"] == 640
    svc.close()


def test_requested_properties_are_set(make_service, device):
    import cv2

    svc = make_service(camera_kwargs=camera_kwargs(device))
    svc.open("s-cam", SourceConfig(mode=SourceMode.LIVE, width=800, height=600, fps=15), SessionClock())
    svc.close()
    assert device.props[cv2.CAP_PROP_FRAME_WIDTH] == 800
    assert device.props[cv2.CAP_PROP_FRAME_HEIGHT] == 600
    assert device.props[cv2.CAP_PROP_FPS] == 15


def test_disconnect_then_reconnect_keeps_frame_ids(make_service, device, thread_baseline):
    svc = make_service(camera_kwargs=camera_kwargs(device))
    events: list[tuple[str, str]] = []
    svc.set_health_listener(lambda h: events.append((h.status.value, h.code)))
    metas = []
    svc.add_consumer("m", lambda p: metas.append(p.meta))
    svc.open("s-cam", LIVE, SessionClock())
    assert wait_until(lambda: len(metas) > 10)
    device.connected = False  # unplug
    assert wait_until(lambda: svc.health().code == "camera_disconnected", timeout=3.0)
    h = svc.health()
    assert h.status == HealthStatus.UNAVAILABLE and h.details["reconnecting"] is True
    n_at_unplug = len(metas)
    time.sleep(0.8)
    assert len(metas) <= n_at_unplug + 1
    assert device.active_handles == 0  # the dead handle is released while waiting
    device.connected = True  # plug back
    assert wait_until(lambda: svc.health().code == "running", timeout=5.0)
    assert wait_until(lambda: len(metas) > n_at_unplug + 10)
    details = svc.health().details
    svc.close()
    assert details["reconnects"] == 1 and details["reconnect_attempts"] >= 1
    ids = [m.frame_id for m in metas]
    ts = [m.t_session_ms for m in metas]
    assert ids == list(range(len(ids)))  # stable within the session, no restart at 0
    assert ts == sorted(ts)
    assert ("unavailable", "camera_disconnected") in events
    assert events[-2:] == [("ok", "running"), ("stopped", "closed")]
    assert "face_missing" not in {c for _, c in events}
    assert device.active_handles == 0


def test_camera_taken_by_other_app_during_reconnect(make_service, device):
    svc = make_service(camera_kwargs=camera_kwargs(device, probe_timeout_s=0.2))
    svc.open("s-cam", LIVE, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 3)
    device.delivers = False  # another app grabbed it: reads fail, reopen gives no frames
    assert wait_until(lambda: svc.health().code == "camera_disconnected", timeout=3.0)
    assert wait_until(lambda: svc.health().details.get("last_open_error") == "camera_no_frames", timeout=3.0)
    device.delivers = True
    assert wait_until(lambda: svc.health().code == "running", timeout=6.0)
    svc.close()


def test_frame_stall_is_capture_health_not_absence(make_service, device):
    svc = make_service(camera_kwargs=camera_kwargs(device))
    svc.open("s-cam", LIVE, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 3)
    device.unblocked.clear()  # driver hangs inside read()
    assert wait_until(lambda: svc.health().code == "frame_stall", timeout=2.5)
    h = svc.health()
    assert h.status == HealthStatus.DEGRADED and h.details["stall_threshold_s"] >= 1.0
    device.unblocked.set()
    assert wait_until(lambda: svc.health().code == "running", timeout=2.0)
    svc.close()


def test_close_during_blocking_read_returns_on_time(make_service, device):
    svc = make_service(camera_kwargs=camera_kwargs(device))
    svc.open("s-cam", LIVE, SessionClock())
    assert wait_until(lambda: svc.metrics().frames_captured > 2)
    device.unblocked.clear()
    time.sleep(0.1)
    t0 = time.monotonic()
    svc.close(timeout_s=0.5)
    assert time.monotonic() - t0 < 1.0
    assert svc.health().code == "closed"
    device.unblocked.set()
    assert wait_until(lambda: device.active_handles == 0)


@pytest.mark.parametrize("mode", ["gray", "bgra"])
def test_pixel_formats_are_normalized_to_bgr(make_service, device, mode):
    device.mode = mode
    svc = make_service(camera_kwargs=camera_kwargs(device))
    shapes = []
    svc.add_consumer("m", lambda p: shapes.append((p.image.shape, p.image.dtype.name, p.image.flags.c_contiguous)))
    svc.open("s-cam", LIVE, SessionClock())
    assert wait_until(lambda: len(shapes) > 3)
    svc.close()
    assert set(shapes) == {((480, 640, 3), "uint8", True)}


def test_unusable_frames_lead_to_disconnect_not_garbage(make_service, device):
    svc = make_service(camera_kwargs=camera_kwargs(device))
    seen = []
    svc.add_consumer("m", lambda p: seen.append(p.image.shape))
    svc.open("s-cam", LIVE, SessionClock())
    assert wait_until(lambda: len(seen) > 2)
    device.mode = "empty"
    assert wait_until(lambda: svc.health().code == "camera_disconnected", timeout=3.0)
    assert svc.metrics().frames_dropped >= 1
    svc.close()
    assert all(s == (480, 640, 3) for s in seen)


def test_open_timeout(make_service, device):
    device.open_delay_s = 1.5
    svc = make_service(camera_kwargs=camera_kwargs(device), open_timeout_s=0.5)
    t0 = time.monotonic()
    err = _open_error(svc)
    assert time.monotonic() - t0 < 2.0
    assert err.code == ErrorCode.CAMERA_UNAVAILABLE and err.details["reason"] == "open_timeout"
    assert svc.health().code == "open_timeout"
    assert wait_until(lambda: device.active_handles == 0, timeout=3.0)  # late handle released by its thread
    assert wait_until(lambda: svc.leaked_runs() == 0, timeout=3.0)


def test_camera_index_from_settings_and_source(make_service, device, settings):
    from dataclasses import replace

    seen = []

    def opener(index, backend):
        seen.append(index)
        return device.opener(index, backend)

    svc = make_service(settings=replace(settings, camera_index=2), camera_kwargs=camera_kwargs(device, opener=opener))
    svc.open("s-cam", LIVE, SessionClock())
    assert svc.health().details["source_id"] == "camera:2"
    svc.close()
    svc.open("s-cam2", SourceConfig(mode=SourceMode.LIVE, camera_index=1), SessionClock())
    svc.close()
    assert seen == [2, 1]


def test_camera_source_is_the_only_videocapture_user():
    """Static guard: no other A02 file opens a device (replay opens FILES via cv2.VideoCapture)."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    users = sorted(
        p.name for p in root.glob("*.py") if "cv2.VideoCapture(" in p.read_text(encoding="utf-8")
    )
    assert users == ["replay.py", "sources.py"], users
    assert "cv2.VideoCapture(index, backend)" in (root / "sources.py").read_text(encoding="utf-8")
    assert CameraSource.mode == SourceMode.LIVE
