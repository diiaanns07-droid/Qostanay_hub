"""Replay: manifest validation, recording timestamps, pacing, determinism, loops, trims."""

from __future__ import annotations

import hashlib
import json
import os
import time

import cv2
import numpy as np
import pytest

from proctor.capture.replay import load_replay, sha256_sequence, list_sequence
from proctor.capture.tests.helpers import frame_pattern, sha256, wait_until, write_manifest, write_video
from proctor_contracts.interfaces import CaptureError, SessionClock
from proctor_contracts.v1 import ErrorCode, HealthStatus, SourceConfig, SourceMode


def replay(replay_id: str | None, **kw) -> SourceConfig:
    return SourceConfig(mode=SourceMode.REPLAY, replay_id=replay_id, **kw)


def _collect(svc, replay_id: str, *, consumers=(("m", None),), timeout=10.0, sid="s-rep", **src):
    got: dict[str, list] = {name: [] for name, _ in consumers}
    for name, max_fps in consumers:
        svc.add_consumer(name, (lambda n: lambda p: got[n].append(p))(name), max_fps=max_fps)
    svc.open(sid, replay(replay_id, **src), SessionClock())
    assert wait_until(lambda: svc.health().code == "replay_ended", timeout=timeout), svc.health()
    start = svc.replay_start_t_ms()
    m = svc.metrics()
    svc.close()
    for name, _ in consumers:
        svc.remove_consumer(name)
    return got, start, m


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "replay_id,reason",
    [
        (None, "replay_id_missing"),
        ("../etc/passwd", "replay_id_invalid"),
        ("a/b", "replay_id_invalid"),
        ("..", "replay_id_invalid"),
        ("c:demo", "replay_id_invalid"),
        ("missing_clip", "manifest_missing"),
    ],
)
def test_bad_replay_ids(make_service, replay_id, reason, thread_baseline):
    from pydantic import ValidationError

    if replay_id is None or "/" in replay_id:
        with pytest.raises(ValidationError):  # first line of defence: the wire contract
            replay(replay_id)
    elif reason != "manifest_missing":
        replay(replay_id)  # valid contract Id ('..', ':'), but not a safe replay id: capture refuses it
    # second line: capture refuses even a config that bypassed validation
    config = SourceConfig.model_construct(mode=SourceMode.REPLAY, replay_id=replay_id, width=640, height=480, fps=30, camera_index=0)
    svc = make_service()
    with pytest.raises(CaptureError) as exc:
        svc.open("s-rep", config, SessionClock())
    assert exc.value.code == ErrorCode.REPLAY_INVALID and exc.value.details["reason"] == reason
    assert svc.health().status == HealthStatus.UNAVAILABLE and svc.health().code == reason
    assert not svc.is_open


def _expect_invalid(settings, replay_id: str, reason: str) -> None:
    with pytest.raises(CaptureError) as exc:
        load_replay(settings.replay_dir, replay_id)
    assert exc.value.code == ErrorCode.REPLAY_INVALID
    assert exc.value.details["reason"] == reason, exc.value.message


def test_manifest_errors(settings, clip):
    d = settings.replay_dir
    (d / "notjson.json").write_text("{nope", encoding="utf-8")
    _expect_invalid(settings, "notjson", "manifest_not_json")

    clip("good")
    doc = json.loads((d / "good.json").read_text())
    (d / "extra.json").write_text(json.dumps({**doc, "replay_id": "extra", "surprise": 1}))
    _expect_invalid(settings, "extra", "manifest_invalid")
    (d / "wrongfmt.json").write_text(json.dumps({**doc, "replay_id": "wrongfmt", "format": "other.v9"}))
    _expect_invalid(settings, "wrongfmt", "manifest_invalid")
    (d / "mismatch.json").write_text(json.dumps({**doc, "replay_id": "someone_else"}))
    _expect_invalid(settings, "mismatch", "replay_id_mismatch")
    (d / "toobig.json").write_text(" " * 300_000)
    _expect_invalid(settings, "toobig", "manifest_too_large")
    (d / "badtrim.json").write_text(json.dumps({**doc, "replay_id": "badtrim", "start_ms": 500, "end_ms": 100}))
    _expect_invalid(settings, "badtrim", "manifest_invalid")


def test_media_errors(settings, clip, tmp_path):
    d = settings.replay_dir
    write_manifest(d, "traversal", {"kind": "video", "path": "../outside.avi"})
    _expect_invalid(settings, "traversal", "manifest_invalid")  # rejected by the path pattern
    write_manifest(d, "absolute", {"kind": "video", "path": "/etc/passwd"})
    _expect_invalid(settings, "absolute", "manifest_invalid")
    write_manifest(d, "nomedia", {"kind": "video", "path": "media/none.avi"})
    _expect_invalid(settings, "nomedia", "media_missing")
    (d / "media").mkdir(exist_ok=True)
    (d / "media" / "empty.avi").write_bytes(b"")
    write_manifest(d, "emptymedia", {"kind": "video", "path": "media/empty.avi"})
    _expect_invalid(settings, "emptymedia", "media_empty")
    clip("shaclip")
    doc = json.loads((d / "shaclip.json").read_text())
    doc["media"]["sha256"] = "0" * 64
    doc["replay_id"] = "shabad"
    (d / "shabad.json").write_text(json.dumps(doc))
    _expect_invalid(settings, "shabad", "media_sha256_mismatch")
    write_manifest(d, "seqnofps", {"kind": "image_sequence", "path": "media/seq", "timestamps": "fps"})
    _expect_invalid(settings, "seqnofps", "manifest_invalid")
    write_manifest(d, "sidecarless", {"kind": "video", "path": "media/shaclip.avi", "timestamps": "sidecar"})
    _expect_invalid(settings, "sidecarless", "manifest_invalid")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlink_escape_is_refused(settings, tmp_path):
    outside = write_video(tmp_path / "outside.avi", n=5)
    (settings.replay_dir / "media").mkdir()
    try:
        os.symlink(outside, settings.replay_dir / "media" / "link.avi")
    except OSError:
        pytest.skip("symlink not permitted")
    write_manifest(settings.replay_dir, "escape", {"kind": "video", "path": "media/link.avi"})
    _expect_invalid(settings, "escape", "path_outside_replay_dir")


def test_undecodable_media_fails_open_not_mid_exam(make_service, settings, thread_baseline):
    (settings.replay_dir / "media").mkdir()
    (settings.replay_dir / "media" / "garbage.avi").write_bytes(os.urandom(4096))
    write_manifest(settings.replay_dir, "garbage", {"kind": "video", "path": "media/garbage.avi"})
    svc = make_service()
    with pytest.raises(CaptureError) as exc:
        svc.open("s-rep", replay("garbage"), SessionClock())
    assert exc.value.code == ErrorCode.REPLAY_INVALID
    assert exc.value.details["reason"] in {"media_unreadable", "media_no_frames"}
    assert not svc.is_open


def test_trim_beyond_media_is_invalid(make_service, clip):
    rid = clip("short", n=10, fps=25.0, start_ms=5000)
    svc = make_service()
    with pytest.raises(CaptureError) as exc:
        svc.open("s-rep", replay(rid), SessionClock())
    assert exc.value.details["reason"] == "media_no_frames"


def test_sidecar_validation(settings, clip):
    d = settings.replay_dir
    clip("sc")
    (d / "media" / "sc.ts.json").write_text(json.dumps({"pts_ms": [0, 40, 40, 80]}))
    doc = json.loads((d / "sc.json").read_text())
    doc["media"].update(timestamps="sidecar", timestamps_path="media/sc.ts.json")
    (d / "sc.json").write_text(json.dumps(doc))
    _expect_invalid(settings, "sc", "sidecar_invalid")


# ------------------------------------------------------------------ playback


def test_realtime_replay_uses_container_timestamps(make_service, clip, thread_baseline):
    rid = clip("rt", n=25, fps=25.0)  # 1.0 s
    svc = make_service()
    t0 = time.monotonic()
    got, start, m = _collect(svc, rid)
    elapsed = time.monotonic() - t0
    frames = got["m"]
    assert [p.frame_id for p in frames] == list(range(25))
    rel = [round(p.t_session_ms - start, 3) for p in frames]
    assert rel == [round(i * 40.0, 3) for i in range(25)]  # recording timestamps, not replay wall time
    assert all(p.meta.source_mode == SourceMode.REPLAY and p.meta.source_id == "replay:rt" for p in frames)
    assert 0.85 <= elapsed <= 2.5, elapsed  # paced in real time (~0.96 s of media)
    assert m.frames_captured == 25 and m.frames_dropped == 0


def test_replay_frames_reach_consumers_unmodified(make_service, clip):
    rid = clip("pix", n=8, fps=25.0)
    svc = make_service()
    got, _, _ = _collect(svc, rid)
    for p in got["m"]:
        expected = frame_pattern(p.frame_id, 320, 240).astype(np.int16)
        assert np.abs(p.image.astype(np.int16) - expected).mean() < 6.0  # MJPG is lossy; content is the right frame


def _fingerprint(frames, start):
    return [(p.frame_id, round(p.t_session_ms - start, 3), hashlib.sha1(p.image.tobytes()).hexdigest()) for p in frames]


def test_lockstep_replay_is_deterministic(make_service, clip):
    rid = clip("det", n=60, fps=30.0, pacing="lockstep")
    runs = []
    for i in range(2):
        svc = make_service()
        t0 = time.monotonic()

        got, start, m = _collect(svc, rid, consumers=(("all", None), ("phone", 8.0)), sid=f"s-det-{i}")
        runs.append((_fingerprint(got["all"], start), _fingerprint(got["phone"], start), m, time.monotonic() - t0))
    (all1, ph1, m1, _), (all2, ph2, m2, dt2) = runs
    assert all1 == all2 and ph1 == ph2  # identical frames, timestamps and pixels
    assert len(all1) == 60  # nothing skipped in lockstep
    assert m1.consumers and {c.name: c.frames_skipped for c in m1.consumers}["all"] == 0
    # max_fps=8 is decimated in MEDIA time: one frame per >= 125 ms of the recording
    ph_t = [t for _, t, _ in ph1]
    assert np.diff(ph_t).min() >= 125.0 - 1e-6 and 14 <= len(ph1) <= 16
    assert dt2 < 2.0  # faster than real time (2 s of media) when consumers are fast


def test_lockstep_waits_for_slow_consumer(make_service, clip):
    rid = clip("slowlock", n=10, fps=30.0, pacing="lockstep")
    svc = make_service()
    got, _, m = _collect(svc, rid, consumers=(("slow", None),))
    assert [p.frame_id for p in got["slow"]] == list(range(10))


def test_pacing_override_from_service(make_service, clip):
    rid = clip("override", n=30, fps=30.0)  # manifest says realtime
    svc = make_service(replay_pacing="lockstep")
    t0 = time.monotonic()
    got, _, _ = _collect(svc, rid)
    assert len(got["m"]) == 30 and time.monotonic() - t0 < 0.9
    assert svc.health().code == "closed"


def test_fps_and_sidecar_timestamps(make_service, clip, settings):
    rid = clip("fpsmode", n=10, fps=25.0, media={"timestamps": "fps", "fps": 10.0}, pacing="lockstep")
    svc = make_service()
    got, start, _ = _collect(svc, rid)
    assert [round(p.t_session_ms - start, 3) for p in got["m"]] == [i * 100.0 for i in range(10)]

    pts = [0.0, 30.0, 75.0, 140.0, 141.0, 400.0]
    rid2 = clip("sidecar", n=6, fps=25.0, pacing="lockstep", media={"timestamps": "sidecar", "timestamps_path": "media/sidecar.ts.json"})
    (settings.replay_dir / "media" / "sidecar.ts.json").write_text(json.dumps({"pts_ms": pts}))
    got, start, _ = _collect(svc, rid2, sid="s-rep2")
    assert [round(p.t_session_ms - start, 3) for p in got["m"]] == pts


def test_trim_window(make_service, clip):
    rid = clip("trim", n=50, fps=25.0, start_ms=400, end_ms=799, pacing="lockstep")
    svc = make_service()
    got, start, _ = _collect(svc, rid)
    frames = got["m"]
    assert [p.frame_id for p in frames] == list(range(10))  # frames 10..19 of the media -> ids from 0
    assert [round(p.t_session_ms - start, 3) for p in frames] == [i * 40.0 for i in range(10)]
    expected = frame_pattern(10, 320, 240).astype(np.int16)
    assert np.abs(frames[0].image.astype(np.int16) - expected).mean() < 6.0


def test_loop_keeps_ids_and_timeline_increasing(make_service, clip):
    rid = clip("looped", n=10, fps=25.0, loop=True, pacing="lockstep")
    svc = make_service()
    got = []
    svc.add_consumer("m", lambda p: got.append(p))
    svc.open("s-loop", replay(rid), SessionClock())
    assert wait_until(lambda: len(got) >= 35, timeout=5.0)
    loops = svc.health().details["loops"]
    svc.close()
    ids = [p.frame_id for p in got]
    ts = [p.t_session_ms for p in got]
    assert ids == list(range(len(ids))) and np.all(np.diff(ts) > 0)
    assert loops >= 3
    assert round(ts[10] - ts[9], 3) == 40.0  # loop period = last pts + one frame interval


def test_image_sequence_mirror_and_downscale(make_service, settings):
    seq = settings.replay_dir / "media" / "seq"
    seq.mkdir(parents=True)
    for i in range(6):
        cv2.imwrite(str(seq / f"f{i:04d}.png"), frame_pattern(i, 640, 480))
    files = list_sequence(seq)
    write_manifest(
        settings.replay_dir,
        "seq",
        {"kind": "image_sequence", "path": "media/seq", "timestamps": "fps", "fps": 5.0, "mirrored": True, "sha256": sha256_sequence(files)},
        pacing="lockstep",
    )
    svc = make_service()
    got, start, _ = _collect(svc, "seq", width=320, height=240)
    frames = got["m"]
    assert len(frames) == 6
    assert [round(p.t_session_ms - start, 3) for p in frames] == [i * 200.0 for i in range(6)]
    img = frames[0].image
    assert img.shape == (240, 320, 3)  # fit into the requested box, aspect kept
    # media had a red block top-LEFT; mirrored=True means it is flipped back -> top-RIGHT
    assert img[5, -5, 2] > 200 and img[5, -5, 0] < 60
    assert img[5, 5, 2] < 120


def test_undecodable_image_in_sequence_is_dropped_and_counted(make_service, settings):
    seq = settings.replay_dir / "media" / "seq2"
    seq.mkdir(parents=True)
    for i in range(4):
        cv2.imwrite(str(seq / f"f{i:04d}.png"), frame_pattern(i, 160, 120))
    (seq / "f0002.png").write_bytes(b"not a png")
    write_manifest(settings.replay_dir, "seq2", {"kind": "image_sequence", "path": "media/seq2", "timestamps": "fps", "fps": 10.0}, pacing="lockstep")
    svc = make_service()
    got, _, m = _collect(svc, "seq2")
    assert [p.frame_id for p in got["m"]] == [0, 1, 3]  # media index kept: the gap is visible
    assert m.frames_dropped == 1


def test_realtime_speed_accounts_for_every_media_frame(make_service, clip):
    """At a speed the machine may not keep up with, every frame is either emitted or counted as dropped."""
    rid = clip("fast", n=120, fps=30.0, width=640, height=480, speed=16.0)
    svc = make_service()
    got, _, m = _collect(svc, rid, consumers=(("hog", None),))
    assert m.frames_captured + m.frames_dropped == 120
    ids = [p.frame_id for p in got["hog"]]
    assert ids == sorted(ids) and ids[0] == 0


def test_replay_end_is_health_not_crash(make_service, clip):
    rid = clip("ending", n=5, fps=25.0)
    svc = make_service()
    events = []
    svc.set_health_listener(lambda h: events.append((h.status.value, h.code)))
    _collect(svc, rid)
    assert events == [("starting", "opening"), ("ok", "running"), ("stopped", "replay_ended"), ("stopped", "closed")]


def test_sha256_helper_matches_file(tmp_path):
    p = write_video(tmp_path / "x.avi", n=3)
    assert sha256(p) == hashlib.sha256(p.read_bytes()).hexdigest()
