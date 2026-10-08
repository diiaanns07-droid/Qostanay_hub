"""A11 repro: evidence-store write failure during a running session, A01r2 (29cadde) + A08 (5509950).

ISOLATED REPRO (combo tree), synthetic bootstrap capture/analyzers/engine, REAL A08 SQLite store.
Fault: every SQLite write transaction of A08 fails ("disk I/O error") for ~8 s, reads keep working
(simulates a locked/failing disk). Then the fault is removed and the session is finished normally.

Question: does A01r2's v1.0.1 "pipeline faults are visible" path (CONTRACTS.md §2) fire for the real store?
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from proctor_contracts.v1 import HealthMsg, IncidentMsg, ObservationMsg, SessionStateMsg

from repro_common import client, evidence_store, make_app, record_stream, running_synthetic, wait


class FailingWrites:
    """Proxy for A08's sqlite3.Connection: BEGIN IMMEDIATE (every A08 write) raises, reads pass through."""

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, *a, **k):
        if sql.strip().upper().startswith("BEGIN"):
            raise sqlite3.OperationalError("disk I/O error")
        return self._c.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._c, name)


def run(base: Path, control: bool) -> dict:
    data = base / ("data-control" if control else "data")
    shutil.rmtree(data, ignore_errors=True)
    app = make_app(data)
    seen = record_stream(app)
    with client(app) as c:
        sid = running_synthetic(c)
        rt = app.state.proctor["manager"].runtime(sid)
        store = evidence_store(app)
        real = store._conn
        t0 = time.monotonic()
        if control:
            # CONTROL: same fault, but surfaced as an exception (what A01r2's own tests use as a store double)
            orig = store.record_observation

            def raising(obs):
                raise OSError("disk full (control double)")

            store.record_observation = raising
            store.record_incident_change = raising
        else:
            store._conn = FailingWrites(real)
        ev = {"action": "shortcut_alt_tab", "enforcement": "detected_only", "mechanism": "repro", "scope": "window",
              "client_seq": 1, "client_wall_time": "2026-10-08T09:00:05Z",
              "detail": {"shortcut": "Alt+Tab", "process_name": None, "duration_ms": None}}
        c.post(f"/v1/sessions/{sid}/environment/events", json={"session_id": sid, "events": [ev]})
        # phone window of the synthetic script is 3.0-7.0 s session time -> OPENED ~4 s, CLOSED ~7.5 s
        wait(lambda: any(isinstance(m, IncidentMsg) and m.change.incident.rule_id.value == "phone_visible"
                         and m.change.change.value == "closed" for m in seen), 15)
        time.sleep(0.5)
        during = {
            "fault_window_s": round(time.monotonic() - t0, 1),
            "A01_counters": dict(rt.counters),
            "A01_faults_health": [h.model_dump(mode="json") for h in rt.faults.health()],
            "GET_session_last_error": c.get(f"/v1/sessions/{sid}").json()["last_error"],
            "GET_health_evidence": next(h for h in c.get("/v1/health").json()["components"] if h["component"] == "evidence"),
            "stream_HealthMsg_count": sum(isinstance(m, HealthMsg) for m in seen),
            "stream_HealthObservation_evidence": sum(
                isinstance(m, ObservationMsg) and m.observation.kind == "health"
                and m.observation.health.component.value == "evidence" for m in seen),
            "stream_incident_ids": sorted({m.change.incident.incident_id for m in seen if isinstance(m, IncidentMsg)}),
            "REST_incident_ids": sorted(i["incident_id"] for i in c.get(f"/v1/sessions/{sid}/incidents").json()),
            "A08_write_errors": int(store._stats["write_errors"]),
        }
        # recover the disk, finish normally
        if control:
            store.record_observation = orig
            del store.record_incident_change
        else:
            store._conn = real
        fin = c.post(f"/v1/sessions/{sid}/finish").json()
        rep = c.get(f"/v1/sessions/{sid}/report.json").json()
        after = {
            "finish_state": fin["state"],
            "finish_last_error": fin["last_error"],
            "report_incidents": [i["incident"]["incident_id"] for i in rep["incidents"]],
            "report_gaps": [(g["component"], g["reason"]) for g in rep["coverage"]["gaps"]],
            "report_limitations_storage": [l for l in rep["summary"]["limitations_ru"] if "хранилищ" in l.lower()],
        }
    return {"control_raising_store" if control else "real_A08_store": {"during_fault": during, "after_recovery": after}}


base = Path(sys.argv[1])
out = {}
out.update(run(base, control=False))
out.update(run(base, control=True))
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
