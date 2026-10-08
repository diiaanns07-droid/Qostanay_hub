"""A11 repro: backend killed mid-exam and restarted (what A06's BackendSupervisor does after a crash).

ISOLATED REPRO, REAL `python -m proctor serve --token-stdin --port 0` processes started from the combo tree
(A01r2 29cadde backend + A08 5509950 evidence). Other modules absent -> synthetic bootstrap parts (labelled).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

W = Path(__file__).resolve().parent
COMBO = W / "combo"
PY = "/home/user/Qostanay_hub/proctoring/.venv/bin/python"
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}


def launch(data: Path, token: str):
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{COMBO / 'backend'}:{COMBO / 'contracts' / 'python'}",
        PYTHONDONTWRITEBYTECODE="1",
        QORGAU_DATA_DIR=str(data),
        QORGAU_LOG_LEVEL="WARNING",
        QORGAU_MODELS_DIR=str(data / "models"),
    )
    p = subprocess.Popen([PY, "-m", "proctor", "serve", "--token-stdin", "--port", "0"], cwd=str(COMBO),
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(data.parent / f"serve-{time.time_ns()}.log", "w"),
                         text=True, env=env)
    p.stdin.write(token + "\n")
    p.stdin.flush()
    line = p.stdout.readline()
    assert line.startswith("QORGAU_READY "), line
    port = json.loads(line[len("QORGAU_READY "):])["port"]
    return p, httpx.Client(base_url=f"http://127.0.0.1:{port}/v1", headers={"Authorization": f"Bearer {token}"}, timeout=20)


def sc(r):
    try:
        body = r.json()
    except Exception:
        body = r.text[:80]
    if isinstance(body, dict) and "error" in body:
        return r.status_code, body["error"]["code"]
    if isinstance(body, dict) and "state" in body:
        return r.status_code, body["state"]
    return r.status_code, None


base = Path(sys.argv[1])
shutil.rmtree(base, ignore_errors=True)
data = base / "data"
data.mkdir(parents=True)
token = secrets.token_hex(32)

p1, h = launch(data, token)
sid = h.post("/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT}).json()["session_id"]
assert h.post(f"/sessions/{sid}/preflight").json()["ready"]
h.post(f"/sessions/{sid}/calibration/skip", json={"reason": "repro"})
assert h.post(f"/sessions/{sid}/start").json()["state"] == "running"
h.put(f"/sessions/{sid}/answers/q1", json={"value": ["b"], "client_seq": 1})
deadline = time.monotonic() + 15
while time.monotonic() < deadline and not h.get(f"/sessions/{sid}/incidents").json():
    time.sleep(0.2)
before = {"incidents_before_kill": [(i["rule_id"], i["state"]) for i in h.get(f"/sessions/{sid}/incidents").json()]}
p1.send_signal(signal.SIGKILL)
p1.wait()

p2, h2 = launch(data, token)
listed = next((s for s in h2.get("/sessions").json() if s["session_id"] == sid), None)
out = {
    **before,
    "after_restart": {
        "A08 GET /sessions (list) state": listed and listed["state"],
        "A08 list last_error.details": listed and (listed.get("last_error") or {}).get("details"),
        "A01 GET /sessions/{sid}": sc(h2.get(f"/sessions/{sid}")),
        "A01 GET /sessions/{sid}/exam": sc(h2.get(f"/sessions/{sid}/exam")),
        "A01 POST /sessions/{sid}/finish": sc(h2.post(f"/sessions/{sid}/finish")),
        "A01 POST /sessions/{sid}/abort": sc(h2.post(f"/sessions/{sid}/abort", json={"reason": "x"})),
        "A08 PUT /answers/q1": sc(h2.put(f"/sessions/{sid}/answers/q1", json={"value": ["c"], "client_seq": 2})),
        "A08 GET /summary": h2.get(f"/sessions/{sid}/summary").status_code,
        "A08 GET /incidents": [(i["rule_id"], i["state"]) for i in h2.get(f"/sessions/{sid}/incidents").json()],
        "A01 GET /health active_session_id": h2.get("/health").json()["active_session_id"],
    },
}
p2.stdin.close()
p2.wait(timeout=20)
print(json.dumps(out, ensure_ascii=False, indent=1))
