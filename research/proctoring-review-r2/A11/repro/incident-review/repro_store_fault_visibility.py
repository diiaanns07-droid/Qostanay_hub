"""A11 ISOLATED REPRO (in-process, combo tree; not an integration run):
A01r2 @29cadde app + A05 @8f763a1 engine + A08 @5509950 SqliteEvidenceStore, synthetic source.

One transient SQLite failure is injected into A08 exactly while the phone_visible CLOSED change is written
(A08's own fault hook `_fault_point('incident_change:before_commit')`, i.e. the transaction rolls back like a
real 'database or disk is full'). Question: does A01r2's QA-BUG-005 machinery (store_errors counter, PipelineFaults,
SessionInfo.last_error, monitoring_degraded HealthObservation) notice, and what do stream vs REST/report say?
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

import proctor.evidence.store as st
from proctor.app import create_app
from proctor.settings import Settings

TOKEN = "q" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}

published: list = []  # every IncidentChange A01 hands to the store == every IncidentMsg A01 publishes (session.py _emit)
inject = {"armed": False, "fired": 0}
_orig_record = st.SqliteEvidenceStore.record_incident_change
_orig_fault = st.SqliteEvidenceStore._fault_point


def record(self, change):
    published.append(change)
    inc = change.incident
    inject["armed"] = inc.rule_id.value == "phone_visible" and change.change.value == "closed"
    try:
        return _orig_record(self, change)
    finally:
        inject["armed"] = False


def fault(self, name):
    if inject["armed"] and name == "incident_change:before_commit":
        inject["fired"] += 1
        raise sqlite3.OperationalError("database or disk is full (a11 injected, transient)")
    return _orig_fault(self, name)


st.SqliteEvidenceStore.record_incident_change = record
st.SqliteEvidenceStore._fault_point = fault


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="a11-fault-", dir=str(Path(__file__).parent / "tmp")))
    settings = Settings(data_dir=tmp / "data", models_dir=tmp / "models", replay_dir=tmp / "replay",
                        exam_path=tmp / "missing.json", synthetic_fps=30.0, fusion_tick_ms=50.0)
    app = create_app(settings, TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        comps = {h["component"]: h["code"] for h in c.get("/v1/health").json()["components"]}
        print("modules:", {k: comps[k] for k in ("fusion", "evidence")})
        sid = c.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT}).json()["session_id"]
        assert c.post(f"/v1/sessions/{sid}/preflight").json()["ready"] is True
        c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "a11"})
        assert c.post(f"/v1/sessions/{sid}/start").json()["state"] == "running"
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not inject["fired"]:
            time.sleep(0.2)
        time.sleep(1.0)
        mid_health = {h["component"]: (h["status"], h["code"]) for h in c.get("/v1/health").json()["components"]}
        mid_info = c.get(f"/v1/sessions/{sid}").json()
        info = c.post(f"/v1/sessions/{sid}/finish").json()
        rest = c.get(f"/v1/sessions/{sid}/incidents").json()
        summary = c.get(f"/v1/sessions/{sid}/summary").json()
        report = c.get(f"/v1/sessions/{sid}/report.json").json()
        rt = app.state.__dict__.get("_state", {}) if hasattr(app, "state") else {}
    phone_ws = [(ch.change.value, ch.incident.update_seq, ch.incident.state.value) for ch in published if ch.incident.rule_id.value == "phone_visible"]
    phone_rest = [(i["incident_id"], i["update_seq"], i["state"], i["t_end_ms"]) for i in rest if i["rule_id"] == "phone_visible"]
    print("injected failures:", inject["fired"])
    print("stream (IncidentMsg) phone_visible:", phone_ws)
    print("REST after finish phone_visible:", phone_rest)
    print("health while running (evidence):", mid_health.get("evidence"))
    print("SessionInfo.last_error while running:", mid_info.get("last_error"))
    print("SessionInfo.last_error after finish:", info.get("last_error"))
    print("monitoring_degraded incidents:", [i["incident_id"] for i in rest if i["rule_id"] == "monitoring_degraded"])
    print("summary limitations:", summary["limitations_ru"])
    counters = report.get("counters") or report.get("session", {}).get("counters")
    print("report counters:", counters if counters is not None else sorted(report.keys()))


if __name__ == "__main__":
    main()
