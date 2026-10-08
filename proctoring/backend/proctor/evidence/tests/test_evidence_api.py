"""A08 routes mounted by A01's real create_app (auth middleware, error mapping, synthetic pipeline)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.evidence import EvidenceConfig, create_evidence_store
from proctor.settings import Settings
from proctor_contracts.v1 import ApiError, ExportManifest, HumanReview, Incident, IncidentDetail, SessionInfo
from proctor.evidence.review_zones import SessionSummary

TOKEN = "e" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}
EVIL_LABEL = "<script>alert('label')</script>"


def _settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", exam_path=tmp_path / "missing_exam.json", synthetic_fps=30.0, fusion_tick_ms=50.0)


@pytest.fixture()
def api(tmp_path):
    holder: dict = {}

    def factory(settings: Settings):
        holder["store"] = create_evidence_store(settings, EvidenceConfig(min_free_disk_bytes=0))
        return holder["store"]

    app = create_app(_settings(tmp_path), TOKEN, module_overrides={"evidence": factory})
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        client.store = holder["store"]
        yield client


def _wait(predicate, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    return None


def _running_session(client, retain_media: bool = True) -> str:
    r = client.post(
        "/v1/sessions",
        json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "student_label": EVIL_LABEL, "consent": CONSENT,
              "retain_media": retain_media},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    report = client.post(f"/v1/sessions/{sid}/preflight").json()
    assert report["ready"] is True
    storage = next(c for c in report["checks"] if c["check_id"] == "storage")
    assert storage["status"] == "pass" and storage["details"]["impl"] == "module"
    client.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "test"})
    assert client.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
    return sid


def test_module_is_discovered_by_composition_root(tmp_path):
    """Without overrides A01's registry imports proctor.evidence and reports it healthy."""
    app = create_app(_settings(tmp_path), TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        comps = {x["component"]: x for x in c.get("/v1/health").json()["components"]}
        assert comps["evidence"]["status"] == "ok" and comps["evidence"]["code"] == "ok"
        assert c.get("/v1/sessions").json() == []
    assert (tmp_path / "data" / "qorgau-evidence.sqlite3").is_file()


def test_full_synthetic_flow(api):
    sid = _running_session(api)
    r = api.put(f"/v1/sessions/{sid}/answers/q2", json={"value": "<img src=x onerror=alert(1)>", "client_seq": 1})
    assert r.status_code == 200 and r.json()["value"].startswith("<img")
    ev = {"action": "shortcut_alt_tab", "enforcement": "detected_only", "mechanism": "test.shell", "scope": "window",
          "client_seq": 1, "client_wall_time": "2026-10-08T09:00:05Z", "detail": {"shortcut": "<b>Alt+Tab</b>"}}
    assert api.post(f"/v1/sessions/{sid}/environment/events", json={"session_id": sid, "events": [ev]}).status_code == 200

    def phone_incident():
        incidents = [Incident.model_validate(i) for i in api.get(f"/v1/sessions/{sid}/incidents").json()]
        return next((i for i in incidents if i.rule_id.value == "phone_visible" and i.evidence_ids), None)

    phone = _wait(phone_incident)
    assert phone is not None, "synthetic phone incident with a snapshot did not arrive"
    rules = {i["rule_id"] for i in api.get(f"/v1/sessions/{sid}/incidents").json()}
    assert {"phone_visible", "environment_blocked_action"} <= rules

    review = api.post(
        f"/v1/sessions/{sid}/incidents/{phone.incident_id}/reviews",
        json={"decision": "dismissed", "comment": "</td><svg onload=alert(1)>", "operator": "teacher-1"},
    )
    assert review.status_code == 200
    HumanReview.model_validate(review.json())
    detail = IncidentDetail.model_validate(api.get(f"/v1/sessions/{sid}/incidents/{phone.incident_id}").json())
    assert detail.incident.review_status.value == "dismissed" and detail.evidence
    media = api.get(f"/v1/sessions/{sid}/evidence/{detail.evidence[0].evidence_id}")
    assert media.status_code == 200 and media.headers["content-type"] == "image/jpeg"
    assert media.content[:3] == b"\xff\xd8\xff" and media.headers["x-content-type-options"] == "nosniff"

    assert api.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
    assert api.put(f"/v1/sessions/{sid}/answers/q1", json={"value": "late", "client_seq": 2}).status_code == 409

    summary = SessionSummary.model_validate(api.get(f"/v1/sessions/{sid}/summary").json())
    assert summary.incidents_total >= 2 and summary.reviews_by_decision["dismissed"] == 1
    assert summary.observed_ms > 0 and any("СИНТЕТИЧЕСКИЕ" in x for x in summary.limitations_ru)

    html_resp = api.get(f"/v1/sessions/{sid}/report.html")
    assert html_resp.status_code == 200 and html_resp.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in html_resp.headers["content-security-policy"]
    page = html_resp.text
    assert EVIL_LABEL not in page and "&lt;script&gt;" in page and "<svg" not in page
    assert "СИНТЕТИЧЕСКИЕ ДАННЫЕ" in page and "data:image/jpeg;base64," in page

    export = api.get(f"/v1/sessions/{sid}/report.json")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]
    payload = json.loads(export.content)
    manifest = ExportManifest.model_validate(payload["manifest"])
    assert manifest.session_id == sid and manifest.source_mode.value == "synthetic"
    names = {f.name for f in manifest.files}
    assert "report.html" in names and f"{detail.evidence[0].evidence_id}.jpg" in names
    assert payload["session"]["student_label"] == EVIL_LABEL  # JSON keeps raw text, HTML escapes it
    assert manifest.config_versions["fusion.rule_version"] == phone.rule_version

    assert api.delete(f"/v1/sessions/{sid}").json() == {"deleted": True}
    r = api.get(f"/v1/sessions/{sid}/incidents")
    assert r.status_code == 404 and r.json()["error"]["code"] == "SESSION_NOT_FOUND"
    assert all(s["session_id"] != sid for s in api.get("/v1/sessions").json())


def test_errors_use_api_error(api, fx):
    sid = _running_session(api, retain_media=False)

    def code(r, status):
        assert r.status_code == status, (r.status_code, r.text)
        return ApiError.model_validate(r.json()).error.code.value

    assert code(api.delete(f"/v1/sessions/{sid}"), 409) == "SESSION_ACTIVE"
    assert code(api.get("/v1/sessions/nope/incidents"), 404) == "SESSION_NOT_FOUND"
    assert code(api.get(f"/v1/sessions/{sid}/incidents/nope"), 404) == "NOT_FOUND"
    assert code(api.get(f"/v1/sessions/{sid}/evidence/nope"), 404) == "NOT_FOUND"
    assert code(api.get(f"/v1/sessions/{sid}/incidents/{'x' * 129}"), 422) == "INVALID_ARGUMENT"
    assert code(api.get(f"/v1/sessions/{sid}/evidence/bad%20id"), 422) == "INVALID_ARGUMENT"
    for path in (f"/v1/sessions/{sid}/evidence/..%2F..%2Fetc%2Fpasswd", f"/v1/sessions/{sid}/evidence/../../x"):
        assert api.get(path).status_code in (404, 422)
    review = {"decision": "confirmed", "comment": "x" * 2001, "operator": "t"}
    api.store.record_incident_change(fx.incident_change(sid))
    assert code(api.post(f"/v1/sessions/{sid}/incidents/inc-1/reviews", json=review), 422) == "INVALID_ARGUMENT"
    assert code(api.post(f"/v1/sessions/{sid}/incidents/inc-1/reviews", json={**review, "comment": "", "extra": 1}), 422) == "INVALID_ARGUMENT"
    assert code(api.put(f"/v1/sessions/{sid}/answers/q1", json={"value": "x" * 4001, "client_seq": 1}), 422) == "INVALID_ARGUMENT"
    big = json.dumps({"value": "x" * 1_100_000, "client_seq": 1})
    assert code(api.put(f"/v1/sessions/{sid}/answers/q1", content=big, headers={"Content-Type": "application/json"}), 413) == "PAYLOAD_TOO_LARGE"
    api.post(f"/v1/sessions/{sid}/finish")
    assert code(api.put(f"/v1/sessions/{sid}/answers/q1", json={"value": "x", "client_seq": 1}), 409) == "INVALID_STATE"
    pages = api.store._conn.execute("PRAGMA page_count").fetchone()[0]
    api.store._conn.execute(f"PRAGMA max_page_count = {pages}")
    big_review = {"decision": "confirmed", "operator": "t"}
    statuses = {api.post(f"/v1/sessions/{sid}/incidents/inc-1/reviews", json={**big_review, "comment": f"{i}" * 2000}).status_code for i in range(10)}
    assert 503 in statuses
    api.store._conn.execute("PRAGMA max_page_count = 1073741823")


def test_routes_require_token(api):
    for method, path in (("get", "/v1/sessions"), ("get", "/v1/sessions/x/report.html"), ("delete", "/v1/sessions/x")):
        r = getattr(api, method)(path, headers={"Authorization": "Bearer " + "z" * 48})
        assert r.status_code == 401
    r = api.get("/v1/sessions", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_sessions_history_newest_first(api):
    first = _running_session(api, retain_media=False)
    api.post(f"/v1/sessions/{first}/finish")
    time.sleep(0.01)
    second = _running_session(api, retain_media=False)
    listed = [SessionInfo.model_validate(s) for s in api.get("/v1/sessions").json()]
    assert [s.session_id for s in listed][:2] == [second, first]
    assert listed[0].state.value == "running" and listed[1].state.value == "finished"
    api.post(f"/v1/sessions/{second}/abort", json={"reason": "t"})
