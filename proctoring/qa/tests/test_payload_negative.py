"""Malformed / hostile payloads against the public API (real process).

Rule under test (CONTRACTS.md §1 Errors): every non-2xx body is a contract ApiError with the right
code; bad input is 4xx (422/404/409), never 500, never a stack trace, and the backend stays usable.
"""

from __future__ import annotations

import copy

import httpx
import pytest

from qorgau_qa import contract
from qorgau_qa.backend import CONSENT, EXAM_ID, env_event, session_create_body

# ------------------------------------------------------------------ session create


BAD_CREATE = {
    "not_json": b"{not json",
    "json_array": b"[]",
    "json_null": b"null",
    "empty_object": b"{}",
    "utf16_bom": "\ufeff{}".encode("utf-16"),
}


@pytest.mark.parametrize("name", list(BAD_CREATE))
def test_create_rejects_malformed_json(backend, api, name):
    r = backend.http.post("/sessions", content=BAD_CREATE[name], headers={"Content-Type": "application/json"})
    contract.api_error(r, 422, "INVALID_ARGUMENT")


def _body(**patch):
    body = copy.deepcopy(session_create_body())
    for key, value in patch.items():
        target = body
        parts = key.split("__")
        for p in parts[:-1]:
            target = target[p]
        if value is _DROP:
            target.pop(parts[-1], None)
        else:
            target[parts[-1]] = value
    return body


_DROP = object()

INVALID_CREATE = {
    "unknown_mode": {"source__mode": "webcam"},
    "extra_top_level_field": {"admin": True},
    "extra_source_field": {"source__path": "C:/Windows/system32"},
    "replay_without_id": {"source__mode": "replay"},
    "replay_id_traversal": {"source__mode": "replay", "source__replay_id": "../../etc/passwd"},
    "replay_id_windows_path": {"source__mode": "replay", "source__replay_id": "C:\\clips\\a.mp4"},
    "replay_id_too_long": {"source__mode": "replay", "source__replay_id": "a" * 129},
    "camera_index_negative": {"source__camera_index": -1},
    "camera_index_too_big": {"source__camera_index": 99},
    "fps_zero": {"source__fps": 0},
    "fps_too_high": {"source__fps": 1000},
    "width_too_small": {"source__width": 10},
    "height_too_big": {"source__height": 5000},
    "exam_id_traversal": {"exam_id": "../exam"},
    "exam_id_empty": {"exam_id": ""},
    "student_label_too_long": {"student_label": "x" * 65},
    "consent_missing": {"consent": _DROP},
    "consent_naive_datetime": {"consent__accepted_at": "2026-10-08T09:00:00"},
    "consent_empty_text_version": {"consent__text_version": ""},
}

# Pydantic (source of truth) runs in lax mode: JSON strings are coerced to int/bool although the
# generated JSON Schema says integer/boolean. TS clients send typed values; recorded, not a blocker.
LAX_COERCED = {
    "fps_as_string": {"source__fps": "30"},
    "consent_accepted_string": {"consent__accepted": "yes"},
    "retain_media_string": {"retain_media": "false"},
}


@pytest.mark.xfail(strict=False, reason="QA-OBS-005 (A01, informational): Pydantic lax mode accepts strings for int/bool fields that the JSON Schema rejects")
@pytest.mark.parametrize("case", list(LAX_COERCED))
def test_create_rejects_lax_typed_values(backend, api, case):
    body = _body(**LAX_COERCED[case])
    contract.api_error(backend.http.post("/sessions", json=body), 422, "INVALID_ARGUMENT")


@pytest.mark.parametrize("case", list(INVALID_CREATE))
def test_create_rejects_contract_violations(backend, api, case):
    r = backend.http.post("/sessions", json=_body(**INVALID_CREATE[case]))
    contract.api_error(r, 422, "INVALID_ARGUMENT")
    assert backend.http.get("/health").json()["active_session_id"] is None, "a rejected create must not leave a session"


def test_create_rejects_consent_not_accepted(backend, api):
    r = backend.http.post("/sessions", json=_body(consent__accepted=False))
    contract.api_error(r, {400, 422, 500}, "INVALID_ARGUMENT")  # status: see test_error_status_matches_contract
    assert backend.http.get("/health").json()["active_session_id"] is None


def test_create_rejects_unknown_exam(backend, api):
    r = backend.http.post("/sessions", json=_body(exam_id="exam-that-does-not-exist"))
    contract.api_error(r, {404, 422, 500}, {"INVALID_ARGUMENT", "NOT_FOUND"})


# CONTRACTS.md §1 Errors: ErrorCode → HTTP status
CONTRACT_STATUS = {"INVALID_ARGUMENT": 422, "SESSION_MISMATCH": 409, "NOT_FOUND": 404}


def _status_case(backend, api, case):
    if case == "consent_not_accepted":
        return backend.http.post("/sessions", json=_body(consent__accepted=False))
    if case == "unknown_exam":
        return backend.http.post("/sessions", json=_body(exam_id="exam-that-does-not-exist"))
    sid = api.create()["session_id"]
    return backend.http.post(f"/sessions/{sid}/environment/events", json=_batch("another-session", env_event("shortcut_ctrl_c", 1)))


@pytest.mark.parametrize("case", ["consent_not_accepted", "unknown_exam", "session_mismatch"])
def test_error_status_matches_contract(backend, api, case):
    r = _status_case(backend, api, case)
    code = r.json()["error"]["code"]
    assert r.status_code == CONTRACT_STATUS[code], f"{code} returned HTTP {r.status_code}"


def test_create_accepts_cyrillic_and_kazakh_label(backend, api):
    r = backend.http.post("/sessions", json=_body(student_label="Студент Әлия Құрманова"))
    info = contract.ok(r, "SessionInfo", 201)
    assert info.student_label == "Студент Әлия Құрманова"


def test_create_with_wrong_content_type_rejected(backend, api):
    import json

    r = backend.http.post("/sessions", content=json.dumps(session_create_body()).encode(), headers={"Content-Type": "text/plain"})
    # FastAPI parses JSON regardless of content type for non-form bodies; either outcome is a contract response
    assert r.status_code in (201, 415, 422), r.status_code
    if r.status_code != 201:
        contract.api_error(r, r.status_code)


# ------------------------------------------------------------------ path identifiers


@pytest.mark.parametrize("sid", ["nope", "..", "%2e%2e", "a b", "s'--", "<script>", "s%00x", "Ω"])
def test_unknown_or_illegal_session_ids_are_404_or_422(backend, sid):
    for method, suffix in [("GET", ""), ("POST", "/preflight"), ("POST", "/start"), ("GET", "/incidents"), ("GET", "/summary"), ("GET", "/metrics"), ("GET", "/exam")]:
        r = backend.http.request(method, f"/sessions/{sid}{suffix}")
        contract.api_error(r, {404, 422}, {"SESSION_NOT_FOUND", "NOT_FOUND", "INVALID_ARGUMENT"})


def test_overlong_session_id_is_rejected_cleanly(backend):
    with httpx.Client(base_url=backend.base, headers=backend.auth, timeout=10) as c:
        r = c.get("/sessions/" + "A" * 1000)
        contract.api_error(r, {404, 422})
        assert c.get("/health").status_code == 200, "connection must stay usable after a 4xx"


def test_overlong_question_id_is_rejected_cleanly(backend, api):
    sid = api.running_session()
    with httpx.Client(base_url=backend.base, headers=backend.auth, timeout=10) as c:
        r = c.put(f"/sessions/{sid}/answers/" + "Q" * 200, json={"value": "x", "client_seq": 1})
        contract.api_error(r, {404, 422})


def test_overlong_incident_id_is_rejected_cleanly(backend, api):
    sid = api.running_session()
    with httpx.Client(base_url=backend.base, headers=backend.auth, timeout=10) as c:
        r = c.post(f"/sessions/{sid}/incidents/" + "I" * 1000 + "/reviews", json={"decision": "dismissed", "operator": "qa"})
        contract.api_error(r, {404, 422})


def test_backend_still_serves_after_bad_requests(backend):
    for _ in range(3):
        with httpx.Client(base_url=backend.base, headers=backend.auth, timeout=10) as c:
            c.get("/sessions/" + "A" * 1000)
    assert backend.http.get("/health").status_code == 200
    assert backend.proc.poll() is None


# ------------------------------------------------------------------ lifecycle bodies


@pytest.mark.parametrize(
    "path,body",
    [
        ("/pause", {}),
        ("/pause", {"reason": ""}),
        ("/pause", {"reason": "x" * 201}),
        ("/abort", {}),
        ("/abort", {"reason": None}),
        ("/calibration/skip", {}),
        ("/calibration/skip", {"reason": ""}),
        ("/calibration/target", {"target": "behind"}),
        ("/calibration/target", {"target": "center", "extra": 1}),
    ],
)
def test_lifecycle_body_validation(backend, api, path, body):
    sid = api.create()["session_id"]
    contract.api_error(backend.http.post(f"/sessions/{sid}{path}", json=body), 422, "INVALID_ARGUMENT")


# ------------------------------------------------------------------ environment events


def _batch(sid, *events):
    return {"session_id": sid, "events": list(events)}


INVALID_EVENTS = {
    "unknown_action": {"action": "shortcut_ctrl_alt_del"},
    "unknown_enforcement": {"enforcement": "maybe"},
    "unknown_scope": {"scope": "kernel"},
    "mechanism_uppercase": {"mechanism": "Electron.Hook"},
    "mechanism_space": {"mechanism": "a b"},
    "mechanism_too_long": {"mechanism": "a" * 65},
    "negative_seq": {"client_seq": -1},
    "seq_float": {"client_seq": 1.5},
    "naive_wall_time": {"client_wall_time": "2026-10-08T09:00:05"},
    "process_name_full_path": {"detail": {"shortcut": None, "process_name": "C:\\Windows\\notepad.exe", "duration_ms": None}},
    "process_name_unix_path": {"detail": {"shortcut": None, "process_name": "/usr/bin/firefox", "duration_ms": None}},
    "window_title_field": {"detail": {"shortcut": None, "process_name": None, "duration_ms": None, "window_title": "Gmail - secret"}},
    "typed_text_field": {"detail": {"shortcut": None, "process_name": None, "duration_ms": None, "typed_text": "answer"}},
    "clipboard_field": {"clipboard": "copied answer"},
    "observation_id_injection": {"observation_id": "forged"},
}


@pytest.mark.parametrize("case", list(INVALID_EVENTS))
def test_environment_event_validation_and_privacy_allowlist(backend, api, case):
    sid = api.create()["session_id"]
    ev = {**env_event("shortcut_ctrl_c", 1), **INVALID_EVENTS[case]}
    contract.api_error(backend.http.post(f"/sessions/{sid}/environment/events", json=_batch(sid, ev)), 422, "INVALID_ARGUMENT")


def test_environment_batch_limits(backend, api):
    sid = api.create()["session_id"]
    contract.api_error(backend.http.post(f"/sessions/{sid}/environment/events", json=_batch(sid)), 422)
    too_many = [env_event("shortcut_ctrl_v", i) for i in range(101)]
    contract.api_error(backend.http.post(f"/sessions/{sid}/environment/events", json=_batch(sid, *too_many)), 422)
    exactly = [env_event("shortcut_ctrl_v", i) for i in range(100)]
    ack = contract.ok(backend.http.post(f"/sessions/{sid}/environment/events", json=_batch(sid, *exactly)), "EnvironmentEventAck")
    assert ack.accepted == 100 and len(ack.observation_ids) == 100


def test_environment_duplicates_inside_one_batch(backend, api):
    sid = api.create()["session_id"]
    ev = env_event("shortcut_alt_tab", 7)
    ack = contract.ok(backend.http.post(f"/sessions/{sid}/environment/events", json=_batch(sid, ev, ev, ev)), "EnvironmentEventAck")
    assert (ack.accepted, ack.duplicates) == (1, 2)


def test_environment_events_session_checks(backend, api):
    sid = api.create()["session_id"]
    ev = env_event("shortcut_ctrl_c", 1)
    contract.api_error(backend.http.post(f"/sessions/{sid}/environment/events", json=_batch("another-session", ev)), {409, 500}, "SESSION_MISMATCH")  # 500: QA-BUG-003
    contract.api_error(backend.http.post("/sessions/unknown/environment/events", json=_batch("unknown", ev)), 404, "SESSION_NOT_FOUND")
    backend.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})
    contract.api_error(backend.http.post(f"/sessions/{sid}/environment/events", json=_batch(sid, ev)), 409, "INVALID_STATE")


def test_environment_detail_allowlist_accepts_basename(backend, api):
    sid = api.create()["session_id"]
    ev = env_event("foreign_window_foreground", 1, process_name="chrome.exe", duration_ms=1200)
    ack = contract.ok(backend.http.post(f"/sessions/{sid}/environment/events", json=_batch(sid, ev)), "EnvironmentEventAck")
    assert ack.accepted == 1


# ------------------------------------------------------------------ capabilities


@pytest.mark.parametrize(
    "patch",
    [
        {"reported_at": "2026-10-08T09:00:00"},
        {"items": [{"action": "shortcut_win", "status": "probably", "mechanism": "x", "verified_on": None, "note_ru": None}]},
        {"items": [{"action": "shortcut_win", "status": "blocked", "mechanism": "x" * 65, "verified_on": None, "note_ru": None}]},
        {"items": [{"action": "shortcut_win", "status": "blocked", "mechanism": "x", "verified_on": None, "note_ru": None}] * 65},
        {"platform": "p" * 129},
        {"extra": 1},
    ],
    ids=["naive_time", "bad_status", "long_mechanism", "too_many_items", "long_platform", "extra_field"],
)
def test_capabilities_validation(backend, patch):
    from qorgau_qa.backend import FAKE_SHELL_CAPABILITIES

    before = backend.http.get("/environment/capabilities").json()
    body = {**copy.deepcopy(FAKE_SHELL_CAPABILITIES), **patch}
    contract.api_error(backend.http.put("/environment/capabilities", json=body), 422, "INVALID_ARGUMENT")
    assert backend.http.get("/environment/capabilities").json() == before, "a rejected PUT must not change stored capabilities"


# ------------------------------------------------------------------ answers and reviews


@pytest.mark.parametrize(
    "body",
    [{}, {"value": "x"}, {"value": "x", "client_seq": -1}, {"value": "x" * 4001, "client_seq": 1}, {"value": ["../a"], "client_seq": 1}, {"value": 5, "client_seq": 1}, {"value": "x", "client_seq": 1, "extra": 1}],
    ids=["empty", "no_seq", "negative_seq", "too_long", "traversal_option", "number", "extra"],
)
def test_answer_validation(backend, api, body):
    sid = api.running_session()
    contract.api_error(backend.http.put(f"/sessions/{sid}/answers/q1", json=body), 422, "INVALID_ARGUMENT")


def test_answers_rejected_outside_running(backend, api):
    sid = api.create()["session_id"]
    contract.api_error(backend.http.put(f"/sessions/{sid}/answers/q1", json={"value": ["a"], "client_seq": 1}), 409, "INVALID_STATE")
    contract.api_error(backend.http.put("/sessions/nope/answers/q1", json={"value": ["a"], "client_seq": 1}), 404, "SESSION_NOT_FOUND")


def test_answers_are_checked_against_exam_definition(backend, api):
    sid = api.running_session()
    r = backend.http.put(f"/sessions/{sid}/answers/not-a-question", json={"value": "x", "client_seq": 1})
    contract.api_error(r, {404, 422})


@pytest.mark.parametrize(
    "body",
    [{}, {"decision": "guilty", "operator": "t"}, {"decision": "dismissed"}, {"decision": "dismissed", "operator": ""}, {"decision": "dismissed", "operator": "t" * 65}, {"decision": "dismissed", "operator": "t", "comment": "c" * 2001}, {"decision": "dismissed", "operator": "t", "auto_sanction": True}],
    ids=["empty", "unknown_decision", "no_operator", "empty_operator", "long_operator", "long_comment", "extra_field"],
)
def test_review_validation(backend, api, body):
    sid = api.create()["session_id"]
    r = backend.http.post(f"/sessions/{sid}/incidents/inc-x/reviews", json=body)
    contract.api_error(r, {404, 422})
    if r.status_code == 404:  # incident unknown → validation may legitimately come second
        assert r.json()["error"]["code"] in ("NOT_FOUND", "SESSION_NOT_FOUND")


def test_review_of_unknown_incident_is_404(backend, api):
    sid = api.create()["session_id"]
    contract.api_error(backend.http.post(f"/sessions/{sid}/incidents/inc-unknown/reviews", json={"decision": "dismissed", "operator": "qa"}), 404, "NOT_FOUND")
    contract.api_error(backend.http.get(f"/sessions/{sid}/incidents/inc-unknown"), 404, "NOT_FOUND")


def test_delete_active_session_refused_and_history_kept(backend, api):
    sid = api.create()["session_id"]
    contract.api_error(backend.http.delete(f"/sessions/{sid}"), 409, "SESSION_ACTIVE")
    backend.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})
    r = backend.http.delete(f"/sessions/{sid}")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert sid not in [s["session_id"] for s in backend.http.get("/sessions").json()], "deleted session still in history"
    contract.api_error(backend.http.delete("/sessions/never-existed"), 404, "SESSION_NOT_FOUND")


def test_deleted_session_is_not_readable(backend, api):
    sid = api.create()["session_id"]
    backend.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})
    assert backend.http.delete(f"/sessions/{sid}").status_code == 200
    contract.api_error(backend.http.get(f"/sessions/{sid}"), 404, "SESSION_NOT_FOUND")


def test_consent_record_is_required_fields(backend, api):
    consent = dict(CONSENT)
    consent.pop("text_version")
    r = backend.http.post("/sessions", json={"source": {"mode": "synthetic"}, "exam_id": EXAM_ID, "consent": consent})
    contract.api_error(r, 422, "INVALID_ARGUMENT")
