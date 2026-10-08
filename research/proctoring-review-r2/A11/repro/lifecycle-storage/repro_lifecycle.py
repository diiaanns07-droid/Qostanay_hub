"""A11 repro: finish / abort / shutdown / restart / delete through A01r2 (29cadde) + A08 (5509950).

ISOLATED REPRO (combo tree), synthetic bootstrap capture/analyzers/engine, REAL A08 SQLite store.
Prints facts; does not assert, so every scenario is reported.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from repro_common import client, evidence_store, make_app, record_stream, running_synthetic, wait

base = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
data = base / "data"
shutil.rmtree(data, ignore_errors=True)


def open_incident(c, sid):
    inc = c.get(f"/v1/sessions/{sid}/incidents").json()
    return [i for i in inc if i["state"] == "open"]


def report(c, sid):
    r = c.get(f"/v1/sessions/{sid}/report.json")
    return r.status_code, (r.json() if r.status_code == 200 else r.text[:200])


out: dict = {}

# ---------------- 1) finish while a phone incident is OPEN ----------------
app = make_app(data)
seen = record_stream(app)
with client(app) as c:
    sid = running_synthetic(c)
    ok = wait(lambda: open_incident(c, sid), 15)
    c.put(f"/v1/sessions/{sid}/answers/q1", json={"value": ["b"], "client_seq": 1})
    fin = c.post(f"/v1/sessions/{sid}/finish").json()
    st, rep = report(c, sid)
    out["finish"] = {
        "had_open_incident_before_finish": bool(ok),
        "state": fin["state"],
        "incidents_after": [(i["state"], i.get("end_reason")) for i in c.get(f"/v1/sessions/{sid}/incidents").json()],
        "report_status": st,
        "missing_observation_refs": rep.get("missing_observation_refs") if st == 200 else None,
        "observations_total": rep.get("observations_total") if st == 200 else None,
        "counters": rep.get("summary", {}) and rep.get("counters") if st == 200 else None,
        "gaps": [(g["component"], g["reason"], round(g["t_start_ms"]), g["t_end_ms"] and round(g["t_end_ms"])) for g in rep["coverage"]["gaps"]] if st == 200 else None,
        "answer_after_finish": c.put(f"/v1/sessions/{sid}/answers/q1", json={"value": ["a"], "client_seq": 2}).status_code,
    }
    sid_finish = sid

    # ---------------- 2) abort while a phone incident is OPEN ----------------
    sid = running_synthetic(c)
    ok = wait(lambda: open_incident(c, sid), 15)
    ab = c.post(f"/v1/sessions/{sid}/abort", json={"reason": "repro"}).json()
    st, rep = report(c, sid)
    out["abort"] = {
        "had_open_incident_before_abort": bool(ok),
        "state": ab["state"],
        "incidents_after": [(i["state"], i.get("end_reason")) for i in c.get(f"/v1/sessions/{sid}/incidents").json()],
        "missing_observation_refs": rep.get("missing_observation_refs") if st == 200 else None,
    }
    sid_abort = sid

    # ---------------- 3) backend shutdown while a phone incident is OPEN ----------------
    sid = running_synthetic(c)
    ok = wait(lambda: open_incident(c, sid), 15)
    out["shutdown_pre"] = {"had_open_incident_before_shutdown": bool(ok)}
    sid_shutdown = sid
# lifespan exit -> manager.shutdown() (abort) -> registry.close() (A08 close)

# ---------------- 4) restart: new process state, same data_dir ----------------
app2 = make_app(data)
with client(app2) as c:
    listed = {s["session_id"]: s["state"] for s in c.get("/v1/sessions").json()}
    out["restart"] = {
        "listed_by_A08_GET_/sessions": {k: listed.get(k) for k in (sid_finish, sid_abort, sid_shutdown)},
        "shutdown_session_incidents": [(i["state"], i.get("end_reason")) for i in c.get(f"/v1/sessions/{sid_shutdown}/incidents").json()],
        "A01_GET_/sessions/{sid}": {
            k: (lambda r: (r.status_code, r.json().get("error", {}).get("code") if r.status_code != 200 else r.json()["state"]))(
                c.get(f"/v1/sessions/{k}")
            )
            for k in (sid_finish, sid_abort, sid_shutdown)
        },
        "A08_GET_summary_status": c.get(f"/v1/sessions/{sid_finish}/summary").status_code,
        "A08_GET_report_json_status": c.get(f"/v1/sessions/{sid_finish}/report.json").status_code,
        "A01_GET_exam_status": c.get(f"/v1/sessions/{sid_finish}/exam").status_code,
    }
    # ---------------- 5) delete in the same process that ran the session ----------------
    sid = running_synthetic(c)
    c.post(f"/v1/sessions/{sid}/finish")
    d = c.delete(f"/v1/sessions/{sid}")
    after = c.get(f"/v1/sessions/{sid}")
    out["delete_same_process"] = {
        "delete": (d.status_code, d.json()),
        "A01_GET_after_delete": (after.status_code, after.json().get("state") if after.status_code == 200 else after.json()["error"]["code"]),
        "A08_summary_after_delete": c.get(f"/v1/sessions/{sid}/summary").status_code,
        "A01_runtime_still_held": sid in app2.state.proctor["manager"]._sessions,
    }

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
