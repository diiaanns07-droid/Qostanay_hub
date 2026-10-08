"""Scenario 1 (synthetic): preflight → calibration → exam → phone + environment episodes → review
→ summary/report → finish → restart, through a real backend process.

SYNTHETIC wiring only: proves the public API/stream/lifecycle contract, not CV quality.
"""

from __future__ import annotations

import time

from qorgau_qa import contract
from qorgau_qa.backend import env_event, wait_for
from qorgau_qa.scenario import run_full_flow
from qorgau_qa.stream import StreamRecorder, incidents


def test_full_synthetic_flow(backend, api, record_property):
    rows = run_full_flow(backend)
    for name, status, detail in rows.rows:
        record_property(f"{status}:{name}", detail)
    assert not rows.failed, "\n".join(f"FAIL {n}: {d}" for n, _, d in rows.failed)


def test_finish_during_open_phone_episode_closes_it_with_session_finished(backend, api):
    """Failure scenario 'finish during an event': the open episode must be closed, not lost or left open."""
    with StreamRecorder(backend.ws_url("/stream"), headers=backend.auth) as stream:
        sid = api.running_session()
        opened = stream.wait(lambda ms: [c for c in incidents(ms, "phone_visible") if c["change"] == "opened"], 45)
        assert opened, "synthetic phone episode did not open within 45 s"
        iid = opened[0]["incident"]["incident_id"]
        assert not [c for c in incidents(stream.snapshot(), "phone_visible") if c["change"] == "closed" and c["incident"]["incident_id"] == iid], "episode closed before finish; timing assumption broken"
        info = contract.ok(backend.http.post(f"/sessions/{sid}/finish"), "SessionInfo")
        assert info.state.value == "finished"
        closed = stream.wait(lambda ms: [c for c in incidents(ms, "phone_visible") if c["change"] == "closed" and c["incident"]["incident_id"] == iid], 5)
    assert closed, "open episode was not closed on finish"
    inc = closed[0]["incident"]
    assert inc["end_reason"] == "session_finished" and inc["state"] == "closed"
    assert inc["t_end_ms"] >= inc["t_start_ms"] and inc["update_seq"] > opened[0]["incident"]["update_seq"]
    stored = contract.ok(backend.http.get(f"/sessions/{sid}/incidents/{iid}"), "IncidentDetail")
    assert stored.incident.state.value == "closed" and stored.incident.end_reason.value == "session_finished"


def test_abort_during_open_episode_closes_it_with_session_aborted(backend, api):
    with StreamRecorder(backend.ws_url("/stream"), headers=backend.auth) as stream:
        sid = api.running_session()
        opened = stream.wait(lambda ms: [c for c in incidents(ms, "phone_visible") if c["change"] == "opened"], 45)
        assert opened
        iid = opened[0]["incident"]["incident_id"]
        info = contract.ok(backend.http.post(f"/sessions/{sid}/abort", json={"reason": "qa emergency exit"}), "SessionInfo")
        assert info.state.value == "aborted"
        closed = stream.wait(lambda ms: [c for c in incidents(ms) if c["change"] == "closed" and c["incident"]["incident_id"] == iid], 5)
    assert closed and closed[0]["incident"]["end_reason"] == "session_aborted"


def test_pause_closes_open_episode_rejects_answers_and_counts_gap(backend, api):
    with StreamRecorder(backend.ws_url("/stream"), headers=backend.auth) as stream:
        sid = api.running_session()
        opened = stream.wait(lambda ms: [c for c in incidents(ms, "phone_visible") if c["change"] == "opened"], 45)
        assert opened
        iid = opened[0]["incident"]["incident_id"]
        paused = contract.ok(backend.http.post(f"/sessions/{sid}/pause", json={"reason": "operator check"}), "SessionInfo")
        assert paused.state.value == "paused"
        closed = stream.wait(lambda ms: [c for c in incidents(ms) if c["change"] == "closed" and c["incident"]["incident_id"] == iid], 5)
        assert closed and closed[0]["incident"]["end_reason"] == "session_paused"
        contract.api_error(backend.http.put(f"/sessions/{sid}/answers/q1", json={"value": ["a"], "client_seq": 1}), 409)
        opened_before = len([c for c in incidents(stream.snapshot()) if c["change"] == "opened"])
        time.sleep(0.6)
        opened_while_paused = len([c for c in incidents(stream.snapshot()) if c["change"] == "opened"]) - opened_before
        resumed = contract.ok(backend.http.post(f"/sessions/{sid}/resume"), "SessionInfo")
        assert resumed.state.value == "running" and resumed.paused_total_ms >= 500
        assert opened_while_paused == 0, "an episode opened from data observed while paused"
        fin = contract.ok(backend.http.post(f"/sessions/{sid}/finish"), "SessionInfo")
        assert fin.paused_total_ms >= resumed.paused_total_ms


def test_environment_events_accepted_while_paused(backend, api):
    """Paused is non-terminal: the shell may still report events (contract: accepted, engine paused)."""
    sid = api.running_session()
    backend.http.post(f"/sessions/{sid}/pause", json={"reason": "operator"})
    r = backend.http.post(f"/sessions/{sid}/environment/events", json={"session_id": sid, "events": [env_event("focus_lost", 1)]})
    # Contract: events are accepted for every non-terminal session; the engine is paused.
    ack = contract.ok(r, "EnvironmentEventAck")
    assert ack.accepted == 1
    assert backend.http.post(f"/sessions/{sid}/finish").json()["state"] == "finished"


def test_restart_after_finish_reuses_camera_owner_cleanly(backend, api):
    for _ in range(3):
        sid = api.running_session()
        m = contract.ok(backend.http.get(f"/sessions/{sid}/metrics"), "RuntimeMetrics")
        assert wait_for(lambda: backend.http.get(f"/sessions/{sid}/metrics").json()["frames_captured"] > 0, 5)
        assert m.session_id == sid
        assert backend.http.post(f"/sessions/{sid}/finish").json()["state"] == "finished"
        # after finish the capture is closed: metrics/preview report nothing for this session
        assert backend.http.get(f"/sessions/{sid}/preview.jpg").status_code == 204
    history = contract.validate_list("SessionInfo", backend.http.get("/sessions").json())
    created = [h.created_at for h in history]
    assert created == sorted(created, reverse=True), "GET /sessions must be newest first"
