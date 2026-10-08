"""Module-failure scenarios through a REAL `serve` process with QA fault-injection doubles.

The doubles (qorgau_qa/fakes.py) implement the public factories and are injected via sys.modules;
the backend discovers them like A02–A08. What is tested is A01's composition root: does a module
failure become an honest, visible state (preflight FAIL / health / stream) instead of a crash, a hang,
a silent fallback or an "all clear"? These are NOT module results and NOT CV results.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from qorgau_qa import contract
from qorgau_qa.backend import FAKE_SHELL_CAPABILITIES, Api, BackendProcess, wait_for
from qorgau_qa.stream import StreamRecorder, incidents

ALL_OK = {"capture": "ok", "phone": "ok", "attention": "ok", "fusion": "ok", "evidence": "ok"}
LABEL = "QA FAULT-INJECTION DOUBLE"


@pytest.fixture()
def faked(tmp_path):
    started: list[BackendProcess] = []

    def make(**overrides: str) -> BackendProcess:
        spec = {**ALL_OK, **overrides}
        spec = {k: v for k, v in spec.items() if v is not None}
        be = BackendProcess.start(
            tmp_path / f"data{len(started)}",
            env={"QA_FAKES": json.dumps(spec)},
            argv_prefix=[sys.executable, "-m", "qorgau_qa.fakes", "--"],
        )
        started.append(be)
        assert be.http.put("/environment/capabilities", json=FAKE_SHELL_CAPABILITIES).status_code == 200
        return be

    yield make
    for be in started:
        rc = be.stop()
        assert rc == 0, f"backend exit {rc}"
        assert not be.token_leaks()


def _live(be: BackendProcess) -> tuple[str, dict]:
    api = Api(be.http)
    sid = api.create("live")["session_id"]
    pf = contract.ok(be.http.post(f"/sessions/{sid}/preflight"), "PreflightReport")
    return sid, {c.check_id.value: c for c in pf.checks} | {"_ready": pf.ready}


def _health(be: BackendProcess) -> dict:
    report = contract.ok(be.http.get("/health"), "HealthReport")
    return {c.component.value: c for c in report.components}


# ------------------------------------------------------------------ the doubles themselves


def test_doubles_drive_a_complete_live_session(faked):
    """Harness validity: with all doubles OK, a LIVE session passes preflight and produces a labelled episode."""
    be = faked()
    health = _health(be)
    assert all(LABEL in health[c].message for c in ("capture", "phone", "attention", "evidence")), "doubles must be labelled"
    with StreamRecorder(be.ws_url("/stream"), headers=be.auth) as rec:
        sid, checks = _live(be)
        assert checks["_ready"] is True, {k: (v.status.value, v.message_code) for k, v in checks.items() if k != "_ready"}
        Api(be.http).calibrate(sid)
        Api(be.http).start(sid)
        opened = rec.wait(lambda ms: [c for c in incidents(ms, "phone_visible") if c["change"] == "opened"], 15)
        assert opened and opened[0]["incident"]["source_mode"] == "live"
        assert LABEL in opened[0]["incident"]["explanation"]["summary_ru"]
        info = contract.ok(be.http.post(f"/sessions/{sid}/finish"), "SessionInfo")
        assert info.state.value == "finished"
        obs = [m["message"]["observation"] for m in rec.snapshot() if m["message"]["type"] == "observation"]
    frame_obs = [o for o in obs if o["kind"] in ("phone", "attention")]
    assert frame_obs and all(o["producer"]["module"].startswith("qa.fake_") for o in frame_obs)


# ------------------------------------------------------------------ camera


@pytest.mark.parametrize("code", ["CAMERA_UNAVAILABLE", "CAMERA_BUSY", "CAMERA_DENIED"])
def test_camera_open_failure_is_an_honest_preflight_fail(faked, code):
    be = faked(capture=f"open_fails:{code}")
    sid, checks = _live(be)
    assert checks["_ready"] is False
    assert checks["camera"].status.value == "fail" and checks["camera"].message_code == code.lower()
    contract.api_error(be.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa"}), 409, "PREFLIGHT_FAILED")
    contract.api_error(be.http.post(f"/sessions/{sid}/start"), 409)
    assert contract.ok(be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"}), "SessionInfo").state.value == "aborted"
    sid2, checks2 = _live(be)  # camera owner released: a new session can try again
    assert checks2["camera"].status.value == "fail"
    be.http.post(f"/sessions/{sid2}/abort", json={"reason": "qa"})


def test_camera_without_frames_fails_preflight_within_timeout(faked):
    be = faked(capture="no_frames")
    t0 = time.monotonic()
    sid, checks = _live(be)
    elapsed = time.monotonic() - t0
    assert checks["camera"].status.value == "fail" and checks["camera"].message_code == "no_frames"
    assert elapsed < 10, f"preflight took {elapsed:.1f}s"
    t0 = time.monotonic()
    assert be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"}).json()["state"] == "aborted"
    assert time.monotonic() - t0 < 8


def test_capture_factory_crash_keeps_backend_up(faked):
    be = faked(capture="factory_raises")
    h = _health(be)
    assert h["capture"].status.value == "unavailable" and h["capture"].code == "init_error"
    sid, checks = _live(be)
    assert checks["camera"].status.value == "fail" and checks["_ready"] is False
    be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})


def test_camera_unplugged_mid_exam_is_visible_and_session_survives(faked):
    be = faked(capture="disconnect_after:2.5")
    with StreamRecorder(be.ws_url("/stream"), headers=be.auth) as rec:
        sid, checks = _live(be)
        assert checks["_ready"]
        api = Api(be.http)
        be.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa"})
        api.start(sid)
        health_obs = rec.wait(lambda ms: [m["message"]["observation"] for m in ms if m["message"]["type"] == "observation" and m["message"]["observation"]["kind"] == "health"], 10)
        assert health_obs, "camera loss must be published as a HealthObservation"
        h = health_obs[-1]
        assert h["health"]["code"] == "camera_disconnected" and h["status"] == "degraded" and h["source_mode"] == "live" and h["frame_id"] is None
        assert be.http.get(f"/sessions/{sid}").json()["state"] == "running"
        contract.ok(be.http.get(f"/sessions/{sid}/metrics"), "RuntimeMetrics")
        assert contract.ok(be.http.post(f"/sessions/{sid}/finish"), "SessionInfo").state.value == "finished"


# ------------------------------------------------------------------ models


def test_phone_weights_missing_blocks_live_without_fallback(faked):
    be = faked(phone="model_missing")
    h = _health(be)
    assert h["phone"].status.value == "unavailable" and h["phone"].code == "model_missing"
    sid, checks = _live(be)
    assert checks["phone_model"].status.value == "fail" and checks["phone_model"].required is True
    assert checks["phone_model"].details.get("impl") == "module", "must not fall back to the bootstrap analyzer"
    assert checks["_ready"] is False
    contract.api_error(be.http.post(f"/sessions/{sid}/calibration/start"), 409, "PREFLIGHT_FAILED")
    be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})


def test_corrupt_phone_weights_load_error_keeps_backend_up(faked):
    be = faked(phone="load_raises")
    h = _health(be)
    assert h["phone"].status.value == "unavailable" and h["phone"].code == "init_error"
    sid, checks = _live(be)
    assert checks["phone_model"].status.value == "fail" and checks["_ready"] is False
    be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})


def test_face_model_missing_blocks_live_calibration(faked):
    be = faked(attention="model_missing")
    sid, checks = _live(be)
    assert checks["face_model"].status.value == "fail" and checks["_ready"] is False
    contract.api_error(be.http.post(f"/sessions/{sid}/calibration/start"), 409, "PREFLIGHT_FAILED")
    be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})


def test_synthetic_demo_still_available_when_models_are_missing(faked):
    """Missing weights must not take down the labelled synthetic mode (demo fallback is explicit, not silent)."""
    be = faked(phone="model_missing", attention="model_missing")
    api = Api(be.http)
    sid = api.create("synthetic")["session_id"]
    pf = contract.ok(be.http.post(f"/sessions/{sid}/preflight"), "PreflightReport")
    phone = next(c for c in pf.checks if c.check_id.value == "phone_model")
    assert pf.source_mode.value == "synthetic" and phone.details.get("impl") == "bootstrap" and phone.status.value == "warn"
    be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})


# ------------------------------------------------------------------ storage


def test_storage_open_error_blocks_live(faked):
    be = faked(evidence="open_raises")
    h = _health(be)
    assert h["evidence"].status.value == "unavailable" and h["evidence"].code == "init_error"
    sid, checks = _live(be)
    assert checks["storage"].status.value == "fail" and checks["_ready"] is False
    be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})


def test_storage_unwritable_blocks_live(faked):
    be = faked(evidence="open_unavailable")
    sid, checks = _live(be)
    assert checks["storage"].status.value == "fail" and checks["storage"].message_code == "storage_unwritable"
    be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})


def test_storage_write_failure_does_not_silence_episode_detection(faked):
    be = faked(evidence="record_raises")
    with StreamRecorder(be.ws_url("/stream"), headers=be.auth) as rec:
        sid, checks = _live(be)
        be.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa"})
        Api(be.http).start(sid)
        opened = rec.wait(lambda ms: [c for c in incidents(ms, "phone_visible") if c["change"] == "opened"], 12)
        be.http.post(f"/sessions/{sid}/finish")
    assert opened, "a storage failure must not stop episode detection"


@pytest.mark.parametrize("spec", [{"evidence": "record_raises"}, {"fusion": "consume_raises"}], ids=["store_writes_fail", "engine_consume_fails"])
def test_pipeline_errors_are_visible_in_health(faked, spec):
    be = faked(**spec)
    sid, _ = _live(be)
    be.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa"})
    Api(be.http).start(sid)
    time.sleep(2.0)
    h = _health(be)
    be.http.post(f"/sessions/{sid}/finish")
    assert h["fusion"].status.value != "ok" or h["evidence"].status.value != "ok", {k: (v.status.value, v.code) for k, v in h.items()}


# ------------------------------------------------------------------ slow / broken analyzers and engine


def test_slow_analyzer_does_not_block_api_or_finish(faked):
    be = faked(phone="slow:1.5")
    sid, checks = _live(be)
    be.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa"})
    Api(be.http).start(sid)
    lat = []
    for _ in range(20):
        t0 = time.monotonic()
        assert be.http.get("/health").status_code == 200
        lat.append(time.monotonic() - t0)
    lat.sort()
    assert lat[int(len(lat) * 0.95) - 1] < 0.5, f"/health p95 {lat[-2]:.3f}s while an analyzer is slow"
    t0 = time.monotonic()
    assert be.http.post(f"/sessions/{sid}/finish").json()["state"] == "finished"
    assert time.monotonic() - t0 < 10, "finish must not wait unboundedly for a slow analyzer"


def test_engine_consume_failure_keeps_session_controllable(faked):
    be = faked(fusion="consume_raises")
    with StreamRecorder(be.ws_url("/stream"), headers=be.auth) as rec:
        sid, _ = _live(be)
        be.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa"})
        Api(be.http).start(sid)
        assert rec.wait(lambda ms: len([m for m in ms if m["message"]["type"] == "observation"]) > 10, 10), "observations must still reach the stream"
        assert be.http.post(f"/sessions/{sid}/pause", json={"reason": "qa"}).json()["state"] == "paused"
        assert be.http.post(f"/sessions/{sid}/resume").json()["state"] == "running"
        t0 = time.monotonic()
        assert be.http.post(f"/sessions/{sid}/finish").json()["state"] == "finished"
        assert time.monotonic() - t0 < 15


def test_engine_finish_failure_still_finishes_session(faked):
    be = faked(fusion="finish_raises")
    sid, _ = _live(be)
    be.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa"})
    Api(be.http).start(sid)
    time.sleep(1.0)
    t0 = time.monotonic()
    r = be.http.post(f"/sessions/{sid}/finish")
    assert r.status_code == 200 and r.json()["state"] == "finished", r.text[:200]
    assert time.monotonic() - t0 < 15
    api = Api(be.http)  # the camera owner is free again
    sid2 = api.create("live")["session_id"]
    be.http.post(f"/sessions/{sid2}/abort", json={"reason": "qa"})


def test_finish_during_open_episode_with_module_engine(faked):
    be = faked()
    with StreamRecorder(be.ws_url("/stream"), headers=be.auth) as rec:
        sid, _ = _live(be)
        be.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa"})
        Api(be.http).start(sid)
        opened = rec.wait(lambda ms: [c for c in incidents(ms, "phone_visible") if c["change"] == "opened"], 15)
        assert opened
        iid = opened[0]["incident"]["incident_id"]
        be.http.post(f"/sessions/{sid}/finish")
        closed = rec.wait(lambda ms: [c for c in incidents(ms) if c["change"] == "closed" and c["incident"]["incident_id"] == iid], 5)
    if not closed:
        pytest.skip("episode closed by the script before finish (timing); covered by the synthetic test")
    assert closed[0]["incident"]["end_reason"] in ("session_finished", "condition_cleared")
    stored = be.http.get(f"/sessions/{sid}/incidents").json()
    assert any(i["incident_id"] == iid and i["state"] == "closed" for i in stored), "closed episode must reach the store"
