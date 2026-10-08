"""Integrated candidate flow on the REAL modules that are installed (owner: A01).

* test_integrated_synthetic_flow — real capture (A02), fusion (A05) and evidence (A08) when present, scripted
  synthetic analyzers (by design for synthetic mode): preflight → calibration → exam → incidents → review →
  HTML/JSON report → finish → restart. Skips a stage only when its module is genuinely absent.
* test_integrated_replay_real_analyzers — REPLAY through the real phone (A03) and attention (A04) analyzers.
  Needs QORGAU_IT_REPLAY_DIR with a replay manifest `faces_01.json` (media outside Git) and verified weights;
  otherwise SKIPPED (never a fake PASS). It proves wiring, not CV quality.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.settings import PROCTORING_ROOT, Settings

TOKEN = "i" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}
WS_HEADERS = {"Host": "127.0.0.1", **AUTH}


def _client(tmp_path: Path, replay_dir: Path | None = None) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        models_dir=PROCTORING_ROOT / "models",  # real weights if prepared; absent weights => honest UNAVAILABLE
        replay_dir=replay_dir or tmp_path / "replay",
        synthetic_fps=15.0,
    )
    return TestClient(create_app(settings, TOKEN), base_url="http://127.0.0.1", headers=AUTH)


def _impls(report: dict) -> dict:
    return {c["check_id"]: (c["status"], c["details"].get("impl")) for c in report["checks"]}


def _collect(ws, until, timeout: float) -> list[dict]:
    seen, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = ws.receive_json()["message"]
        seen.append(msg)
        if until(seen):
            break
    return seen


def test_integrated_synthetic_flow(tmp_path):
    with _client(tmp_path) as c:
        health = {h["component"]: h for h in c.get("/v1/health").json()["components"]}
        exam = c.get  # noqa: F841 - readability
        r = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "student_label": "it", "consent": CONSENT})
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        report = c.post(f"/v1/sessions/{sid}/preflight").json()
        assert report["ready"] is True, _impls(report)
        impls = _impls(report)
        if health["capture"]["code"] != "module_not_integrated":
            assert impls["camera"][1] == "module", impls  # A02 serves synthetic frames
        if health["evidence"]["code"] != "module_not_integrated":
            assert impls["storage"] == ("pass", "module"), impls
        if health["fusion"]["code"] != "module_not_integrated":
            assert impls["fusion"] == ("pass", "module"), impls

        assert c.post(f"/v1/sessions/{sid}/calibration/start").json()["phase"] == "collecting"
        for target in ("center", "left", "right", "up", "down"):
            c.post(f"/v1/sessions/{sid}/calibration/target", json={"target": target})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                states = {t["target"]: t["state"] for t in c.get(f"/v1/sessions/{sid}/calibration").json()["targets"]}
                if states[target] == "ok":
                    break
                time.sleep(0.1)
        assert c.post(f"/v1/sessions/{sid}/calibration/finish").json()["phase"] == "completed"

        exam_def = c.get(f"/v1/sessions/{sid}/exam").json()
        assert c.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
        q = exam_def["questions"][0]
        value = [q["options"][0]["option_id"]] if q["options"] else "ответ"
        assert c.put(f"/v1/sessions/{sid}/answers/{q['question_id']}", json={"value": value, "client_seq": 1}).status_code == 200
        ev = {"action": "shortcut_ctrl_v", "enforcement": "blocked", "mechanism": "it.fake_shell", "scope": "window",
              "client_seq": 1, "client_wall_time": "2026-10-08T09:00:05Z"}
        assert c.post(f"/v1/sessions/{sid}/environment/events", json={"session_id": sid, "events": [ev]}).status_code == 200

        with c.websocket_connect(f"/v1/stream?session_id={sid}", headers=WS_HEADERS) as ws:
            seen = _collect(
                ws,
                lambda ms: any(m["type"] == "incident" and m["change"]["incident"]["rule_id"] == "phone_visible" for m in ms),
                40,
            )
        rules = {m["change"]["incident"]["rule_id"] for m in seen if m["type"] == "incident"}
        assert "phone_visible" in rules, rules
        incidents = c.get(f"/v1/sessions/{sid}/incidents").json()
        phone = next(i for i in incidents if i["rule_id"] == "phone_visible")
        assert phone["source_mode"] == "synthetic"
        rv = c.post(f"/v1/sessions/{sid}/incidents/{phone['incident_id']}/reviews",
                    json={"decision": "inconclusive", "comment": "<b>it</b>", "operator": "teacher-it"})
        assert rv.status_code == 200
        assert c.get(f"/v1/sessions/{sid}/incidents/{phone['incident_id']}").json()["incident"]["review_status"] == "inconclusive"

        assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
        summary = c.get(f"/v1/sessions/{sid}/summary").json()
        assert summary["incidents_total"] >= 1 and summary["session"]["state"] == "finished"
        html = c.get(f"/v1/sessions/{sid}/report.html")
        js = c.get(f"/v1/sessions/{sid}/report.json")
        if health["evidence"]["code"] != "module_not_integrated":
            assert html.status_code == 200 and "<b>it</b>" not in html.text and "&lt;b&gt;it&lt;/b&gt;" in html.text
            assert "http://" not in html.text and "https://" not in html.text.replace("https://example", "")
            assert js.status_code == 200
        else:
            assert html.status_code == 501
        # no spurious capture-close gap at finish (A02 R7)
        assert not [i for i in c.get(f"/v1/sessions/{sid}/incidents").json()
                    if i["rule_id"] == "monitoring_degraded" and i["end_reason"] == "session_finished" and i["duration_ms"] < 50]

        sid2 = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT}).json()["session_id"]
        assert c.post(f"/v1/sessions/{sid2}/preflight").json()["ready"] is True  # restart reopens capture
        assert c.post(f"/v1/sessions/{sid2}/abort", json={"reason": "it"}).json()["state"] == "aborted"


REPLAY_DIR = os.environ.get("QORGAU_IT_REPLAY_DIR")


@pytest.mark.skipif(not REPLAY_DIR, reason="QORGAU_IT_REPLAY_DIR not set (replay media is kept outside Git)")
def test_integrated_replay_real_analyzers(tmp_path):
    with _client(tmp_path, Path(REPLAY_DIR)) as c:
        health = {h["component"]: h for h in c.get("/v1/health").json()["components"]}
        for comp in ("capture", "phone", "attention", "fusion", "evidence"):
            if health[comp]["status"] not in ("ok", "stopped") and health[comp]["code"] not in ("idle", "model_loaded"):
                pytest.skip(f"{comp} not ready here: {health[comp]['code']}")
        r = c.post("/v1/sessions", json={"source": {"mode": "replay", "replay_id": "faces_01"}, "exam_id": "demo-exam-1", "consent": CONSENT})
        sid = r.json()["session_id"]
        report = c.post(f"/v1/sessions/{sid}/preflight").json()
        impls = _impls(report)
        assert report["ready"] is True, impls
        assert all(impl in ("module", None) for _, impl in impls.values()), impls  # never bootstrap in replay
        assert c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "static replay images"}).json()["phase"] == "skipped"
        assert c.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
        with c.websocket_connect(f"/v1/stream?session_id={sid}", headers=WS_HEADERS) as ws:
            seen = _collect(ws, lambda ms: False, 13)
        obs = [m["observation"] for m in seen if m["type"] == "observation"]
        att = [o for o in obs if o["kind"] == "attention"]
        phone = [o for o in obs if o["kind"] == "phone"]
        assert att and all(o["producer"]["module"] == "attention" and o["source_mode"] == "replay" for o in att)
        assert phone and all(o["producer"]["module"] == "phone" and o["source_mode"] == "replay" for o in phone)
        counts = {o["face_count"] for o in att}
        assert 1 in counts and 2 in counts, counts  # one face, then two faces in the public sample images
        assert c.post(f"/v1/sessions/{sid}/finish").json()["state"] == "finished"
        incidents = c.get(f"/v1/sessions/{sid}/incidents").json()
        print("replay incidents:", sorted({(i["rule_id"], i["priority"]) for i in incidents}))
        print("face_count values:", sorted(x for x in counts if x is not None), "phone obs:", len(phone), "attention obs:", len(att))
        assert c.get(f"/v1/sessions/{sid}/report.html").status_code == 200
