"""A11 check: A01r2 path-id dependency on A08's router, DELETE of active/paused session, record_session_config absence.
ISOLATED REPRO (combo tree A01r2 29cadde + A08 5509950)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from repro_common import client, evidence_store, make_app, running_synthetic

base = Path(sys.argv[1])
data = base / "data"
shutil.rmtree(data, ignore_errors=True)
app = make_app(data)
out = {}
with client(app) as c:
    sid = running_synthetic(c)
    long = "A" * 1000
    for name, (method, path, body) in {
        "GET incidents long sid": ("GET", f"/v1/sessions/{long}/incidents", None),
        "GET incident long iid": ("GET", f"/v1/sessions/{sid}/incidents/{long}", None),
        "PUT answer 200-char qid": ("PUT", f"/v1/sessions/{sid}/answers/{'q' * 200}", {"value": ["a"], "client_seq": 1}),
        "POST review long iid": ("POST", f"/v1/sessions/{sid}/incidents/{long}/reviews", {"decision": "dismissed", "comment": "", "operator": "t"}),
        "DELETE long sid": ("DELETE", f"/v1/sessions/{long}", None),
    }.items():
        r = c.request(method, path, json=body)
        out[name] = (r.status_code, r.json()["error"]["code"], c.get("/v1/health").status_code)
    out["DELETE running"] = (lambda r: (r.status_code, r.json()["error"]["code"]))(c.delete(f"/v1/sessions/{sid}"))
    c.post(f"/v1/sessions/{sid}/pause", json={"reason": "x"})
    out["DELETE paused"] = (lambda r: (r.status_code, r.json()["error"]["code"]))(c.delete(f"/v1/sessions/{sid}"))
    out["PUT answer while paused"] = (lambda r: (r.status_code, r.json()["error"]["code"]))(
        c.put(f"/v1/sessions/{sid}/answers/q1", json={"value": ["a"], "client_seq": 1}))
    out["store_has_record_session_config"] = hasattr(evidence_store(app), "record_session_config")
    c.post(f"/v1/sessions/{sid}/finish")
    out["DELETE finished"] = (lambda r: (r.status_code, r.json()))(c.delete(f"/v1/sessions/{sid}"))
    out["DELETE again"] = (lambda r: (r.status_code, r.json()))(c.delete(f"/v1/sessions/{sid}"))
print(json.dumps(out, ensure_ascii=False, indent=1))
