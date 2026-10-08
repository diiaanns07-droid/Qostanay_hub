"""Exhaustive state × action matrix for the session lifecycle (CONTRACTS.md §2), real process.

For every reachable state and every lifecycle action:
  * allowed by the contract     → 2xx and the documented next state
  * not allowed                 → 409 INVALID_STATE / PREFLIGHT_FAILED, and the state is UNCHANGED
  * contract silent (re-entry)  → 2xx or 409, never 5xx; recorded
"""

from __future__ import annotations

import pytest

from qorgau_qa import contract

STATES = ["created", "preflight", "calibrating", "ready", "running", "paused", "finished", "aborted"]
ACTIONS = {
    "preflight": ("/preflight", None),
    "calibration/start": ("/calibration/start", None),
    "calibration/target": ("/calibration/target", {"target": "center"}),
    "calibration/finish": ("/calibration/finish", None),
    "calibration/cancel": ("/calibration/cancel", None),
    "calibration/skip": ("/calibration/skip", {"reason": "qa matrix"}),
    "start": ("/start", None),
    "pause": ("/pause", {"reason": "qa matrix"}),
    "resume": ("/resume", None),
    "finish": ("/finish", None),
    "abort": ("/abort", {"reason": "qa matrix"}),
}

# Allowed transitions from the contract diagram → expected resulting state(s).
ALLOWED: dict[tuple[str, str], set[str]] = {
    ("created", "preflight"): {"preflight"},
    ("preflight", "calibration/start"): {"calibrating"},
    ("preflight", "calibration/skip"): {"ready"},
    ("calibrating", "calibration/target"): {"calibrating"},
    ("calibrating", "calibration/finish"): {"ready", "calibrating"},  # completed → ready; failed stays
    ("calibrating", "calibration/cancel"): {"preflight"},
    ("calibrating", "calibration/skip"): {"ready"},
    ("ready", "start"): {"running"},
    ("running", "pause"): {"paused"},
    ("paused", "resume"): {"running"},
    ("finished", "finish"): {"finished"},  # idempotent
}
for _s in ("created", "preflight", "calibrating", "ready", "running", "paused"):
    ALLOWED[(_s, "finish")] = {"finished"}
    ALLOWED[(_s, "abort")] = {"aborted"}

# Not drawn in the diagram but not forbidden by the text (re-entry / idempotent abort).
SILENT = {
    ("preflight", "preflight"),
    ("calibrating", "calibration/start"),
    ("ready", "calibration/start"),
    ("ready", "preflight"),
    ("aborted", "abort"),
}


def _post(http, sid, action):
    path, body = ACTIONS[action]
    return http.post(f"/sessions/{sid}{path}", json=body) if body is not None else http.post(f"/sessions/{sid}{path}")


def _reach(api, state: str) -> str:
    http = api.http
    sid = api.create()["session_id"]
    steps = {
        "created": [],
        "preflight": ["preflight"],
        "calibrating": ["preflight", "calibration/start"],
        "ready": ["preflight", "calibration/skip"],
        "running": ["preflight", "calibration/skip", "start"],
        "paused": ["preflight", "calibration/skip", "start", "pause"],
        "finished": ["preflight", "calibration/skip", "start", "finish"],
        "aborted": ["abort"],
    }[state]
    for step in steps:
        r = _post(http, sid, step)
        assert r.status_code == 200, f"setup {state}: {step} → {r.status_code} {r.text[:200]}"
    assert http.get(f"/sessions/{sid}").json()["state"] == state
    return sid


@pytest.mark.parametrize("action", list(ACTIONS))
@pytest.mark.parametrize("state", STATES)
def test_transition(backend, api, record_property, state, action):
    sid = _reach(api, state)
    r = _post(backend.http, sid, action)
    after = backend.http.get(f"/sessions/{sid}").json()["state"]
    record_property("result", f"{state} --{action}--> HTTP {r.status_code}, state {after}")
    key = (state, action)
    assert r.status_code < 500, f"{key}: server error {r.status_code} {r.text[:200]}"
    if key in ALLOWED:
        assert r.status_code == 200, f"{key} must be allowed: {r.status_code} {r.text[:200]}"
        assert after in ALLOWED[key], f"{key}: state {after}, expected {ALLOWED[key]}"
        contract.validate("CalibrationState" if action.startswith("calibration/") else ("PreflightReport" if action == "preflight" else "SessionInfo"), r.json())
    elif key in SILENT:
        assert r.status_code in (200, 409), r.status_code
        if r.status_code == 409:
            contract.api_error(r, 409)
            assert after == state
    else:
        contract.api_error(r, 409, {"INVALID_STATE", "PREFLIGHT_FAILED"})
        assert after == state, f"{key}: rejected action changed state {state} → {after}"


@pytest.mark.parametrize("state", STATES)
def test_read_routes_valid_in_every_state(backend, api, state):
    sid = _reach(api, state)
    contract.ok(backend.http.get(f"/sessions/{sid}"), "SessionInfo")
    contract.ok(backend.http.get(f"/sessions/{sid}/calibration"), "CalibrationState")
    contract.ok(backend.http.get(f"/sessions/{sid}/exam"), "ExamDefinition")
    contract.ok(backend.http.get(f"/sessions/{sid}/metrics"), "RuntimeMetrics")
    contract.validate_list("Incident", backend.http.get(f"/sessions/{sid}/incidents").json())
    contract.validate_list("AnswerRecord", backend.http.get(f"/sessions/{sid}/answers").json())
    contract.ok(backend.http.get(f"/sessions/{sid}/summary"), "SessionSummary")
    pv = backend.http.get(f"/sessions/{sid}/preview.jpg")
    assert pv.status_code in (200, 204)
    if state in ("created", "finished", "aborted"):
        assert pv.status_code == 204, "no preview without an open capture"
    health = contract.ok(backend.http.get("/health"), "HealthReport")
    expected_active = sid if state not in ("finished", "aborted") else None
    assert health.active_session_id == expected_active


def test_start_requires_calibration_completed_or_skipped(backend, api):
    sid = _reach(api, "preflight")
    contract.api_error(_post(backend.http, sid, "start"), 409, "INVALID_STATE")
    _post(backend.http, sid, "calibration/start")
    contract.api_error(_post(backend.http, sid, "start"), 409, "INVALID_STATE")
    fin = _post(backend.http, sid, "calibration/finish")  # no targets collected
    assert fin.status_code == 200 and fin.json()["phase"] == "failed", fin.text[:200]
    assert backend.http.get(f"/sessions/{sid}").json()["state"] == "calibrating"
    contract.api_error(_post(backend.http, sid, "start"), 409, "INVALID_STATE")


def test_calibration_cancel_then_restart(backend, api):
    sid = _reach(api, "calibrating")
    cancel = contract.ok(_post(backend.http, sid, "calibration/cancel"), "CalibrationState")
    assert cancel.phase.value == "cancelled"
    assert backend.http.get(f"/sessions/{sid}").json()["state"] == "preflight"
    api.calibrate(sid)
    assert backend.http.get(f"/sessions/{sid}").json()["state"] == "ready"


def test_concurrent_finish_requests_are_idempotent(backend, api):
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    sid = _reach(api, "running")

    def finish(_):
        with httpx.Client(base_url=backend.base, headers=backend.auth, timeout=30) as c:
            return c.post(f"/sessions/{sid}/finish")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(finish, range(8)))
    assert all(r.status_code == 200 and r.json()["state"] == "finished" for r in results), [r.status_code for r in results]
    finished_at = {r.json()["finished_at"] for r in results}
    assert len(finished_at) == 1, f"finish rewrote finished_at: {finished_at}"


def test_concurrent_create_yields_exactly_one_session(backend, api):
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    from qorgau_qa.backend import session_create_body

    def create(_):
        with httpx.Client(base_url=backend.base, headers=backend.auth, timeout=30) as c:
            return c.post("/sessions", json=session_create_body())

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(create, range(8)))
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and codes.count(409) == 7, codes
    for r in results:
        if r.status_code == 409:
            contract.api_error(r, 409, "SESSION_ACTIVE")
