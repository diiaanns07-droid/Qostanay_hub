"""A11 repro: abort reason is dropped by A01; A08's report labels every abort "прервана оператором".

ISOLATED REPRO (combo tree A01r2 29cadde + A08 5509950), synthetic bootstrap pipeline.
Two aborts that are NOT operator decisions:
  1) the exact body A06 @62a7fb1 main.ts:182-184 sends after a student emergency exit;
  2) backend shutdown (A01 SessionManager.shutdown -> AbortRequest(reason="backend_shutdown")), i.e. app quit.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from repro_common import client, make_app, running_synthetic

base = Path(sys.argv[1])
data = base / "data"
shutil.rmtree(data, ignore_errors=True)
out = {}
app = make_app(data)
with client(app) as c:
    sid1 = running_synthetic(c)
    r = c.post(f"/v1/sessions/{sid1}/abort", json={"reason": "emergency_exit: student_emergency_exit"})
    out["emergency_abort_response_state"] = r.json()["state"]
    sid2 = running_synthetic(c)
# app quit -> lifespan shutdown -> abort(backend_shutdown)
app2 = make_app(data)
with client(app2) as c:
    for name, sid in (("student_emergency_exit", sid1), ("app_quit_backend_shutdown", sid2)):
        html = c.get(f"/v1/sessions/{sid}/report.html").text
        js = c.get(f"/v1/sessions/{sid}/report.json").text
        m = re.search(r"Состояние</[^>]+>\s*<[^>]+>([^<]+)<", html)
        out[name] = {
            "report_html_state_label": m.group(1) if m else None,
            "html_contains_прервана_оператором": "прервана оператором" in html,
            "reason_text_anywhere_in_report_json": ("emergency_exit" in js) or ("backend_shutdown" in js),
            "reason_text_anywhere_in_report_html": ("emergency_exit" in html) or ("backend_shutdown" in html),
        }
print(json.dumps(out, ensure_ascii=False, indent=1))
