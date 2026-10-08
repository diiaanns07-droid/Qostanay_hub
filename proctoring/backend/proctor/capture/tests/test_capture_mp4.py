"""H.264 evidence export, bounded retries and offline AVI fallback. Synthetic frames only."""
from __future__ import annotations

import inspect
import io
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import pytest

from proctor.capture import clips
from proctor.capture.clips import ClipBuffer, ClipError, ClipFrame, write_clip


def make_frames(count=101, fps=10, noise=False):
    buffer = ClipBuffer(max_bytes=24 * 1024 * 1024)
    rng = np.random.default_rng(123)
    for i in range(count):
        image = rng.integers(0, 256, (240, 320, 3), dtype=np.uint8) if noise else np.full((240, 320, 3), i, np.uint8)
        cv2.putText(image, f"SIMULATED {i}", (12, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2)
        buffer.add(image, i * 1000 / fps, i)
    return buffer


def decode(path):
    cap = cv2.VideoCapture(str(path))
    images = []
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                break
            images.append(image)
    finally:
        cap.release()
    return images


@pytest.fixture
def ffmpeg():
    module = pytest.importorskip("imageio_ffmpeg", reason="A01 must provision imageio-ffmpeg")
    try:
        return module.get_ffmpeg_exe()
    except RuntimeError:
        pytest.skip("No locally provisioned FFmpeg executable")


def test_mp4_is_h264_yuv420p_decodable_and_preserves_5s_context(tmp_path, ffmpeg):
    result = make_frames().export(5000, out_dir=tmp_path, wait=False)
    assert result.path.suffix == ".mp4" and result.content_type == "video/mp4"
    assert result.t_first_ms == 0 and result.t_last_ms == 10_000 and not result.partial
    assert result.frames == 101 and result.fps == 10
    assert 0 < result.bytes == result.path.stat().st_size <= 8 * 1024 * 1024
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(result.path), "-f", "null", "-"],
                           capture_output=True, timeout=30)
    info = probe.stderr.decode(errors="replace")
    assert probe.returncode == 0, info
    assert "Video: h264" in info and "yuv420p" in info and "mov,mp4" in info
    images = decode(result.path)
    assert len(images) == result.frames
    # Frame order and both ends of the window survive JPEG/H.264 compression.
    assert images[0][0:20, 0:20].mean() < 8
    assert 90 < images[-1][0:20, 0:20].mean() < 110
    data = result.path.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")  # faststart
    assert not list(tmp_path.glob("*.part*"))


def test_real_high_entropy_clip_stays_under_8_mib(tmp_path, ffmpeg):
    result = make_frames(noise=True).export(5000, out_dir=tmp_path, wait=False)
    assert result.content_type == "video/mp4"
    assert result.bytes <= 8 * 1024 * 1024 and result.frames == 101
    assert len(decode(result.path)) == 101


@pytest.mark.parametrize("failure", ["missing_package", "startup", "writing", "finalization"])
def test_encoder_failure_falls_back_to_real_avi_and_removes_part(tmp_path, monkeypatch, caplog, failure):
    if failure == "missing_package":
        monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    elif failure == "startup":
        module = pytest.importorskip("imageio_ffmpeg")
        monkeypatch.setattr(module, "get_ffmpeg_exe", lambda: str(tmp_path / "absent.exe"))
    else:
        class FailedEncoder:
            def __init__(self, command, **kwargs):
                Path(command[-1]).write_bytes(b"incomplete MP4")
                self.stdin = io.BytesIO()
                self.code = None
                if failure == "writing":
                    self.stdin.write = lambda data: (_ for _ in ()).throw(BrokenPipeError("test pipe failure"))

            def poll(self):
                return self.code

            def kill(self):
                self.code = -1

            def wait(self, timeout=None):
                self.code = 1
                return self.code

        monkeypatch.setattr(clips.subprocess, "Popen", FailedEncoder)
    result = make_frames().export(5000, out_dir=tmp_path, wait=False)
    assert result.path.suffix == ".avi" and result.content_type == "video/x-msvideo"
    assert result.path.read_bytes().startswith(b"RIFF") and len(decode(result.path)) == result.frames
    assert result.bytes <= 8 * 1024 * 1024
    assert result.t_first_ms == 0 and result.t_last_ms == 10_000
    assert "falling back" in caplog.text and not list(tmp_path.glob("*.part*"))
    assert not list(tmp_path.glob("*.mp4"))


def test_retry_recompresses_whole_window_without_truncation(tmp_path, monkeypatch, ffmpeg):
    real_writer = clips._write_mp4
    calls = []

    def oversized_once(frames, path, fps, crf, scale, limit):
        calls.append((len(frames), frames[0].t_ms, frames[-1].t_ms, crf))
        if len(calls) == 1:
            path.write_bytes(b"x" * (limit + 1))
            return 320, 240
        return real_writer(frames, path, fps, crf, scale, limit)

    monkeypatch.setattr(clips, "_write_mp4", oversized_once)
    result = make_frames().export(5000, out_dir=tmp_path, wait=False)
    assert result.path.suffix == ".mp4" and result.bytes <= 8 * 1024 * 1024
    assert len(calls) == 2 and calls[1][3] > calls[0][3]
    assert all(c[:3] == (101, 0, 10_000) for c in calls)


def test_impossible_size_limit_is_explicit_and_leaves_no_artifact(tmp_path, monkeypatch):
    mp4_calls, avi_calls = [], []

    def oversized(frames, path, fps, quality, scale=1, limit=None):
        (mp4_calls if path.suffix == ".mp4" else avi_calls).append((frames[0].t_ms, frames[-1].t_ms))
        path.write_bytes(b"x" * 1024)
        return 320, 240

    monkeypatch.setattr(clips, "_write_mp4", oversized)
    monkeypatch.setattr(clips, "_write_avi", oversized)
    with pytest.raises(ClipError, match="too_large"):
        make_frames(count=100).export(5000, out_dir=tmp_path, wait=False, max_file_bytes=100)
    assert len(mp4_calls) == 3 and len(avi_calls) == 4
    assert all(c == (0, 9900) for c in mp4_calls + avi_calls)
    assert not list(tmp_path.iterdir())


def test_runtime_encoding_does_not_connect_or_download(tmp_path, monkeypatch, ffmpeg):
    def blocked(*args, **kwargs):
        pytest.fail("runtime attempted network access")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    result = make_frames(count=11).export(500, 0.5, 0.5, out_dir=tmp_path, wait=False)
    assert result.path.suffix == ".mp4" and result.frames == 11


def test_public_signatures_and_result_fields_are_unchanged():
    assert list(inspect.signature(ClipBuffer.export).parameters) == [
        "self", "t_center_ms", "before_s", "after_s", "out_dir", "name", "wait", "max_file_bytes", "min_free_bytes"]
    assert list(inspect.signature(write_clip).parameters) == [
        "frames", "partial", "out_dir", "name", "max_file_bytes", "min_free_bytes"]
    assert list(clips.ClipResult.__dataclass_fields__) == [
        "path", "frames", "t_first_ms", "t_last_ms", "bytes", "width", "height", "fps", "partial", "content_type"]


def test_bad_jpeg_cannot_be_advertised_as_success(tmp_path):
    with pytest.raises(ClipError, match="encoder_failed"):
        write_clip([ClipFrame(0, 0, b"bad JPEG", 320, 240)], False, tmp_path)
    assert not list(tmp_path.iterdir())


def test_larger_caller_limit_cannot_exceed_protocol_limit(tmp_path, monkeypatch):
    seen_limits = []
    def fail(frames, path, fps, quality, scale, limit):
        seen_limits.append(limit)
        raise ClipError("encoder_failed", "test fallback")
    monkeypatch.setattr(clips, "_write_mp4", fail)
    result = make_frames().export(5000, out_dir=tmp_path, wait=False, max_file_bytes=64 * 1024 * 1024)
    assert seen_limits == [8 * 1024 * 1024] and result.bytes <= 8 * 1024 * 1024


def test_stalled_pipe_is_bounded_and_falls_back(tmp_path, monkeypatch, ffmpeg):
    killed = threading.Event()
    class StalledPipe:
        closed = False
        def write(self, data):
            assert killed.wait(2), "watchdog did not terminate the stalled encoder"
            raise BrokenPipeError("encoder killed")
        def close(self):
            self.closed = True
    class StalledEncoder:
        def __init__(self, command, **kwargs):
            self.stdin = StalledPipe()
        def poll(self):
            return -1 if killed.is_set() else None
        def kill(self):
            killed.set()
        def wait(self, timeout=None):
            return -1
    monkeypatch.setattr(clips.subprocess, "Popen", StalledEncoder)
    monkeypatch.setattr(clips, "CLIP_ENCODER_TIMEOUT_S", 0.05)
    started = time.monotonic()
    result = make_frames(count=11).export(500, 0.5, 0.5, out_dir=tmp_path, wait=False)
    assert killed.is_set() and time.monotonic() - started < 3
    assert result.path.suffix == ".avi" and not list(tmp_path.glob("*.part*"))


def test_mp4_passes_existing_authorized_evidence_endpoint_and_range(tmp_path, ffmpeg, monkeypatch):
    # Use T03's existing router/auth/parser unchanged; this is an A02 integration check.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4]))
    from classreview import ReviewConfig, ReviewStore
    from classreview.devserver import DevClassServer
    from fastapi.testclient import TestClient

    result = make_frames().export(5000, out_dir=tmp_path, wait=False)
    store = ReviewStore(ReviewConfig(data_dir=tmp_path / "review"))
    store.open()
    try:
        store.open_class_session("cls-test")
        store.ingest_incident("stu-test", {
            "seq": 1, "incident_id": "inc-test", "rule_id": "phone_visible", "category": "phone",
            "priority": "medium", "state": "open", "t_start_wall": "2026-10-08T13:00:00Z",
            "duration_ms": 1000, "explanation_ru": "SYNTHETIC TEST", "clip_available": True,
        }, class_session_id="cls-test")
        store.note_clip_requested("stu-test", "inc-test", "cmd-test")
        server = DevClassServer(store, pin="123456")
        server.tokens["synthetic-student-token"] = "stu-test"
        with TestClient(server.build(), base_url="http://127.0.0.1", client=("127.0.0.1", 54321)) as client:
            assert client.get("/api/teacher/clips/inc-test").status_code == 401
            body = result.path.read_bytes()
            uploaded = client.post("/api/student/clips/inc-test", content=body, headers={
                "Authorization": "Bearer synthetic-student-token", "Content-Type": result.content_type})
            assert uploaded.status_code in (200, 201), uploaded.text
            assert client.post("/api/teacher/login", json={"pin": "123456"}).status_code == 200
            response = client.get("/api/teacher/clips/inc-test")
            assert response.status_code == 200 and response.headers["content-type"] == "video/mp4"
            assert response.content == body
            ranged = client.get("/api/teacher/clips/inc-test", headers={"Range": "bytes=0-99"})
            assert ranged.status_code == 206 and ranged.content == body[:100]
            meta, path = store.clip_file("inc-test")
            assert meta["codec"] == "avc1" and meta["duration_s"] == pytest.approx(10.1, abs=0.1)
    finally:
        store.close()
