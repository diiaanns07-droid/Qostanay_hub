"""A02 tools on fakes/synthetic: soak report, verify-live flow (FAKE camera), recorder, CLI.

verify-live is meant for a REAL camera on the target machine; here its success path runs on
an emulated device only so the script itself is exercised before anyone takes it to Windows.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from proctor.capture.__main__ import main
from proctor.capture.measure import SimulatedConsumer, parse_source, run_soak, summarize
from proctor.capture.record import record
from proctor.capture.replay import load_replay
from proctor.capture.tests.helpers import FakeDevice, camera_kwargs
from proctor.capture.verify_live import verify_live
from proctor_contracts.v1 import SourceMode


def test_soak_report_is_measured_and_labelled(settings):
    source = parse_source("synthetic", 320, 240, 30)
    consumers = [SimulatedConsumer("slow", 5.0, 40.0), SimulatedConsumer("fast", None, 2.0)]
    report = run_soak(settings, source, 3.0, consumers=consumers)
    assert report["label"].startswith("SYNTHETIC") and "SIMULATED" in report["label"]
    assert 25 <= report["capture_fps_mean"] <= 32
    assert report["consumers"]["slow"]["processed_fps_mean"] <= 5.5
    assert report["consumers"]["fast"]["ids_strictly_increasing"]
    assert report["consumers"]["slow"]["e2e_ms"]["p50"] >= 40.0
    assert report["evaluation"]["mode"] == "SYNTHETIC (not a camera)"
    assert report["evaluation"]["memory_growth"].startswith("NOT_RUN")  # too short to judge
    assert report["threads"]["after"] <= report["threads"]["before"] and report["leaked_runs"] == 0
    assert "A02 soak" in summarize(report)


def _args(**kw):
    base = dict(camera=0, backend="auto", width=640, height=480, fps=30, seconds=2.0, max_index=1, interactive=False, snapshot=False, out=None)
    base.update(kw)
    return Namespace(**base)


def test_verify_live_flow_on_fake_device(settings, tmp_path):
    device = FakeDevice()
    kw = camera_kwargs(device)
    kw.pop("backends")
    report = verify_live(settings, _args(snapshot=True, out=str(tmp_path / "r.json")), camera_kwargs=kw)
    checks = {c["check"]: c["status"] for c in report["checks"]}
    assert checks["camera_enumeration"] == "PASS" and checks["open"] == "PASS"
    assert checks["frame_format"] == "PASS" and checks["preview_jpeg"] == "PASS"
    assert checks["restart_x3"] == "PASS" and checks["threads_released"] == "PASS"
    for manual in ("unmirrored_view", "disconnect_detected", "reconnected", "busy_camera", "privacy_denied"):
        assert checks[manual] == "NOT_RUN"  # never reported as passed without the operator
    assert report["overall"] == "INCOMPLETE"
    assert Path(report["snapshot"]).is_file()
    assert device.active_handles == 0


def test_verify_live_reports_missing_camera(settings):
    device = FakeDevice()
    device.connected = False
    kw = camera_kwargs(device, device_path_probe=lambda i: (False, False), platform="linux")
    kw.pop("backends")
    report = verify_live(settings, _args(), camera_kwargs=kw)
    assert report["overall"] == "FAIL"
    assert report["cameras"][0]["reason"] == "camera_not_found"


def test_record_then_replay_roundtrip(settings):
    out = record(settings, parse_source("synthetic", 320, 240, 20), "rec_test", 1.5, consent="synthetic frames, nobody recorded")
    assert out["frames"] >= 20
    resolved = load_replay(settings.replay_dir, "rec_test")
    m = resolved.manifest
    assert m.media.timestamps == "sidecar" and m.provenance["consent"].startswith("synthetic")
    assert len(resolved.sidecar) == out["frames"] and resolved.sidecar[0] == 0.0
    assert Path(out["media"]).suffix == ".avi"  # git-ignored media type


def test_cli_replay_check_and_hash(settings, clip, capsys):
    rid = clip("cli_clip", n=12, fps=24.0)
    assert main(["replay-check", rid, "--replay-dir", str(settings.replay_dir)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["frames"] == 12 and summary["sha256_verified"] is True
    media = settings.replay_dir / "media" / f"{rid}.avi"
    assert main(["replay-hash", str(media)]) == 0
    assert capsys.readouterr().out.strip() == load_replay(settings.replay_dir, rid).manifest.media.sha256


def test_parse_source():
    assert parse_source("live:2", 640, 480, 30).camera_index == 2
    assert parse_source("replay:x", 640, 480, 30).mode == SourceMode.REPLAY


def test_replay_schema_file_is_in_sync():
    """capture/replay.schema.json is generated from ReplayManifest (regenerate: see capture/README.md)."""
    from proctor.capture.replay import ReplayManifest

    path = Path(__file__).resolve().parents[1] / "replay.schema.json"
    committed = json.loads(path.read_text(encoding="utf-8"))
    expected = ReplayManifest.model_json_schema()
    for key in ("$schema", "$id", "title"):
        committed.pop(key, None)
        expected.pop(key, None)
    assert committed == expected


def test_example_manifest_validates(tmp_path):
    from proctor.capture.replay import ReplayManifest

    doc = json.loads((Path(__file__).resolve().parents[1] / "examples" / "replay_manifest.example.json").read_text(encoding="utf-8"))
    ReplayManifest.model_validate(doc)
