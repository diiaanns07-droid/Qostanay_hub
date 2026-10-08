"""Class-mode incident clips (proctor.capture.clips + FrameCaptureService.export_clip), synthetic frames only."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from proctor.capture import CLIPS_CONSUMER, ClipError
from proctor.capture.clips import CLIP_MAX_FILE_BYTES, ClipBuffer, fit_size
from proctor_contracts.interfaces import SessionClock
from proctor_contracts.v1 import SourceConfig, SourceMode

RNG = np.random.default_rng(7)


def noisy(w=640, h=480, seed=0) -> np.ndarray:
    """High-entropy frame: the worst case for JPEG size (memory bound tests)."""
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


def patterned(i: int, w=640, h=480) -> np.ndarray:
    img = np.full((h, w, 3), 70, np.uint8)
    x = (i * 11) % (w - 40)
    img[:, x : x + 40] = (40, 200, 240)
    cv2.putText(img, f"#{i}", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
    return img


def fill(buf: ClipBuffer, seconds: float, fps: float = 15.0, t0: float = 0.0, make=patterned) -> int:
    n = int(seconds * fps)
    for i in range(n):
        buf.add(make(i), t0 + i * 1000.0 / fps, i)
    return n


def frames_in(path: Path) -> int:
    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


def test_fit_size_keeps_aspect_and_never_upscales():
    assert fit_size(1280, 720) == (640, 360)
    assert fit_size(640, 480) == (480, 360)
    assert fit_size(320, 240) == (320, 240)


def test_buffer_keeps_only_the_last_10_s_downscaled():
    buf = ClipBuffer(ring_s=10.0)
    buf.add(patterned(0, 1280, 720), 0.0, 0)
    fill(buf, 25.0, t0=1000.0)
    st = buf.stats()
    assert st["t_last_ms"] - st["t_first_ms"] <= 10_000.0 and st["frames"] <= 151
    f = buf.window(0, 1e9)[-1]
    assert (f.width, f.height) == (480, 360)


def test_memory_is_bounded_even_for_worst_case_frames():
    buf = ClipBuffer(ring_s=10.0, max_bytes=2 * 1024 * 1024)
    fill(buf, 10.0, make=lambda i: noisy(seed=i))
    st = buf.stats()
    assert st["bytes"] <= 2 * 1024 * 1024 and st["evicted_by_size"] > 0
    # default cap: 24 MB, measured worst case (pure noise 480x360 q70) stays below it for 10 s x 15 fps
    big = ClipBuffer()
    fill(big, 10.0, make=lambda i: noisy(seed=i))
    assert big.stats()["bytes"] <= big.max_bytes and big.stats()["frames"] == 150


def test_export_window_writes_a_decodable_avi_under_8_mb(tmp_path):
    buf = ClipBuffer()
    fill(buf, 10.0)  # t = 0 .. 9933 ms
    res = buf.export(5000.0, 5.0, 4.9, out_dir=tmp_path, name="inc-1", wait=False)
    assert res.path.parent == tmp_path and res.path.suffix == ".avi" and res.content_type == "video/x-msvideo"
    assert res.path.stat().st_size == res.bytes <= CLIP_MAX_FILE_BYTES
    assert res.t_first_ms == 0.0 and res.t_last_ms >= 9800.0 and not res.partial
    assert frames_in(res.path) == res.frames == 149  # t = 0 .. 9866 ms within [0, 9900]
    assert (res.width, res.height) == (480, 360) and 14.0 <= res.fps <= 16.0
    assert not list(tmp_path.glob("*.part*"))


def test_size_limit_falls_back_to_lower_quality_and_half_fps(tmp_path):
    buf = ClipBuffer(max_bytes=64 * 1024 * 1024)
    fill(buf, 10.0, make=lambda i: noisy(seed=i))
    full = buf.export(5000.0, 5.0, 5.0, out_dir=tmp_path, wait=False, max_file_bytes=64 * 1024 * 1024)
    limit = full.bytes // 5
    small = buf.export(5000.0, 5.0, 5.0, out_dir=tmp_path, wait=False, max_file_bytes=limit)
    assert small.bytes <= limit and small.frames < full.frames
    with pytest.raises(ClipError) as exc:
        buf.export(5000.0, 5.0, 5.0, out_dir=tmp_path, wait=False, max_file_bytes=10_000)
    assert exc.value.code == "too_large"
    assert not list(tmp_path.glob("*.part*"))


def test_explicit_errors(tmp_path):
    empty = ClipBuffer()
    with pytest.raises(ClipError) as exc:
        empty.export(1000.0, out_dir=tmp_path, wait=False)
    assert exc.value.code == "no_frames"
    buf = ClipBuffer()
    fill(buf, 10.0, t0=60_000.0)
    with pytest.raises(ClipError) as exc:
        buf.export(10_000.0, out_dir=tmp_path, wait=False)  # older than the buffer
    assert exc.value.code == "no_frames"
    for bad in ((float("nan"), 5, 5), (1000.0, -1, 5), (1000.0, 8, 8)):
        with pytest.raises(ClipError) as exc:
            buf.export(*bad, out_dir=tmp_path, wait=False)
        assert exc.value.code == "invalid_argument"
    with pytest.raises(ClipError) as exc:
        buf.export(65_000.0, out_dir=tmp_path, wait=False, min_free_bytes=1 << 62)
    assert exc.value.code == "disk_full"
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x")
    with pytest.raises(ClipError) as exc:
        buf.export(65_000.0, out_dir=not_a_dir, wait=False)
    assert exc.value.code == "disk_error"


def test_partial_window_is_flagged(tmp_path):
    buf = ClipBuffer()
    fill(buf, 4.0, t0=3000.0)  # frames 3000 .. 6933 only
    res = buf.export(5000.0, 5.0, 5.0, out_dir=tmp_path, wait=False)
    assert res.partial and res.t_first_ms == 3000.0


def _synthetic(fps=30):
    return SourceConfig(mode=SourceMode.SYNTHETIC, width=640, height=480, fps=fps)


def test_service_export_clip_waits_for_after_s_and_returns_path(make_service, tmp_path):
    svc = make_service()
    clock = SessionClock()
    svc.open("s-clip", _synthetic(), clock)
    time.sleep(1.2)
    t_center = clock.now_ms() if hasattr(clock, "now_ms") else clock.mono_to_session_ms(time.monotonic_ns())
    t0 = time.monotonic()
    res = svc.export_clip_result(t_center, before_s=1.0, after_s=1.0, out_dir=tmp_path)
    waited = time.monotonic() - t0
    assert 0.9 <= waited <= 4.0  # waited for the "after" second, bounded
    assert res.path.is_file() and not res.partial and res.t_last_ms >= t_center + 900.0
    assert 20 <= res.frames <= 34 and (res.width, res.height) == (480, 360)
    path = svc.export_clip(t_center, before_s=1.0, after_s=1.0, out_dir=tmp_path)
    assert isinstance(path, Path) and path.is_file()
    stats = svc.clip_buffer_stats()
    assert stats is not None and stats["bytes"] < 24 * 1024 * 1024
    assert CLIPS_CONSUMER in {c.name for c in svc.metrics().consumers}
    svc.close()


def test_close_during_export_returns_partial_quickly(make_service, tmp_path):
    svc = make_service()
    clock = SessionClock()
    svc.open("s-clip2", _synthetic(), clock)
    time.sleep(0.8)
    t_center = clock.mono_to_session_ms(time.monotonic_ns())
    out: dict = {}

    def run():
        out["res"] = svc.export_clip_result(t_center, before_s=0.5, after_s=5.0, out_dir=tmp_path)

    th = threading.Thread(target=run)
    th.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    svc.close()
    th.join(5.0)
    assert not th.is_alive() and time.monotonic() - t0 < 4.0
    assert out["res"].partial and out["res"].path.is_file()
    # the last run's buffer is still exportable right after close (the uplink may be a bit late)
    assert svc.export_clip(t_center, before_s=0.5, after_s=0.1, out_dir=tmp_path).is_file()


def test_no_run_and_reserved_name(make_service, tmp_path):
    svc = make_service()
    with pytest.raises(ClipError) as exc:
        svc.export_clip(1000.0, out_dir=tmp_path)
    assert exc.value.code == "no_frames"
    with pytest.raises(ValueError):
        svc.add_consumer(CLIPS_CONSUMER, lambda p: None)


def test_clips_can_be_disabled(make_service, tmp_path):
    svc = make_service(clip_ring_s=0)
    svc.open("s-noclip", _synthetic(), SessionClock())
    time.sleep(0.3)
    assert CLIPS_CONSUMER not in {c.name for c in svc.metrics().consumers}
    with pytest.raises(ClipError):
        svc.export_clip(100.0, out_dir=tmp_path)
    svc.close()
