"""A11 ISOLATED REPRO (combo tree, not an integration branch): real backend process
A01r2 @29cadde (app/session) + A05 @8f763a1 (proctor.fusion) + A08 @5509950 (proctor.evidence),
SYNTHETIC source (bootstrap scripted phone 3-7 s / gaze-down 10-14 s per 20 s cycle; no CV, no camera).

Flow: record /v1/stream -> create synthetic session (retain_media) -> preflight -> calibration -> start ->
wait for phone_visible OPENED on the stream -> POST a teacher review while it is open -> wait until the stream
delivers its CLOSED change -> wait for a gaze incident -> finish -> GET incidents / detail / summary.
Writes everything the renderer would receive to out_backend/.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

HERE = Path(__file__).parent
COMBO = HERE / "combo"
OUT = HERE / "out_backend"
OUT.mkdir(exist_ok=True)
DATA = HERE / "tmp" / f"data-{int(time.time())}"
DATA.mkdir(parents=True, exist_ok=True)


def wait(pred, timeout, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def main() -> int:
    token = secrets.token_hex(32)
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{COMBO / 'backend'}{os.pathsep}{COMBO / 'contracts' / 'python'}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QORGAU_DATA_DIR"] = str(DATA)
    env["QORGAU_MODELS_DIR"] = str(HERE / "tmp" / "models-empty")
    env["QORGAU_LOG_LEVEL"] = "WARNING"
    proc = subprocess.Popen(
        [sys.executable, "-m", "proctor", "serve", "--token-stdin", "--port", "0"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(HERE / "tmp"),
    )
    errs: list[str] = []
    threading.Thread(target=lambda: [errs.append(l) for l in proc.stderr], daemon=True).start()
    msgs: list[dict] = []
    try:
        proc.stdin.write(token + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line.startswith("QORGAU_READY "), line + "".join(errs[-20:])
        port = json.loads(line[len("QORGAU_READY "):])["port"]
        base = f"http://127.0.0.1:{port}/v1"
        auth = {"Authorization": f"Bearer {token}"}
        http = httpx.Client(base_url=base, headers=auth, timeout=20.0)
        health = http.get("/health").json()
        comps = {c["component"]: (c["status"], c["code"]) for c in health["components"]}
        print("health:", comps)

        ws = connect(f"ws://127.0.0.1:{port}/v1/stream", additional_headers=auth, open_timeout=10)
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    msgs.append(json.loads(ws.recv(timeout=0.5)))
                except TimeoutError:
                    continue
                except Exception:
                    return

        threading.Thread(target=reader, daemon=True).start()
        consent = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}
        r = http.post("/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "student_label": "a11",
                                         "retain_media": True, "consent": consent})
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        pf = http.post(f"/sessions/{sid}/preflight").json()
        assert pf["ready"], pf
        http.post(f"/sessions/{sid}/calibration/start")
        for target in ("center", "left", "right", "up", "down"):
            http.post(f"/sessions/{sid}/calibration/target", json={"target": target})
            wait(lambda: next(t for t in http.get(f"/sessions/{sid}/calibration").json()["targets"] if t["target"] == target)["state"] == "ok", 10)
        http.post(f"/sessions/{sid}/calibration/finish")
        info = http.post(f"/sessions/{sid}/start").json()
        assert info["state"] == "running", info

        def incident_changes(rule=None, change=None):
            out = []
            for m in list(msgs):
                msg = m.get("message", {})
                if msg.get("type") != "incident":
                    continue
                c = msg["change"]
                if rule and c["incident"]["rule_id"] != rule:
                    continue
                if change and c["change"] != change:
                    continue
                out.append(c)
            return out

        opened = wait(lambda: incident_changes("phone_visible", "opened"), 40)
        assert opened, "no phone_visible incident"
        iid = opened[0]["incident"]["incident_id"]
        # teacher reviews the episode while it is still open (live operator console)
        rv = http.post(f"/sessions/{sid}/incidents/{iid}/reviews", json={"decision": "confirmed", "comment": "a11", "operator": "T1"})
        print("review:", rv.status_code, rv.json().get("decision"))
        rest_after_review = http.get(f"/sessions/{sid}/incidents").json()
        closed = wait(lambda: [c for c in incident_changes() if c["incident"]["incident_id"] == iid and c["change"] == "closed"], 30)
        assert closed, "phone incident did not close"
        wait(lambda: incident_changes("gaze_prolonged_down", "opened"), 30)
        time.sleep(1.0)
        fin = http.post(f"/sessions/{sid}/finish").json()
        print("finish:", fin["state"])
        time.sleep(1.0)
        rest_final = http.get(f"/sessions/{sid}/incidents").json()
        detail = http.get(f"/sessions/{sid}/incidents/{iid}").json()
        summary = http.get(f"/sessions/{sid}/summary").json()
        session = http.get(f"/sessions/{sid}").json()
        stop.set()
        ws.close()
        (OUT / "envelopes.json").write_text(json.dumps(msgs, ensure_ascii=False))
        (OUT / "rest_incidents_after_review.json").write_text(json.dumps(rest_after_review, ensure_ascii=False))
        (OUT / "rest_incidents_final.json").write_text(json.dumps(rest_final, ensure_ascii=False))
        (OUT / "detail.json").write_text(json.dumps(detail, ensure_ascii=False))
        (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False))
        (OUT / "session.json").write_text(json.dumps(session, ensure_ascii=False))
        (OUT / "review.json").write_text(json.dumps(rv.json(), ensure_ascii=False))
        # --- report what each source says about the reviewed incident
        ws_changes = [c for c in incident_changes() if c["incident"]["incident_id"] == iid]
        print("WS changes for", iid, [(c["change"], c["incident"]["update_seq"], c["incident"]["review_status"], len(c["incident"]["evidence_ids"])) for c in ws_changes])
        r_after = next(i for i in rest_after_review if i["incident_id"] == iid)
        r_fin = next(i for i in rest_final if i["incident_id"] == iid)
        print("REST right after review:", r_after["update_seq"], r_after["review_status"], r_after["evidence_ids"])
        print("REST final:", r_fin["update_seq"], r_fin["review_status"], r_fin["evidence_ids"])
        print("detail.reviews:", [x["decision"] for x in detail["reviews"]], "evidence:", len(detail["evidence"]))
        print("summary incidents_total:", summary["incidents_total"], "reviews_by_decision:", summary["reviews_by_decision"])
        print("all incidents final:", [(i["rule_id"], i["update_seq"], i["state"], i["review_status"]) for i in rest_final])
        return 0
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        tail = [l for l in errs if "Traceback" in l or "ERROR" in l]
        if tail:
            print("backend stderr (errors):", "".join(tail[-10:]))


if __name__ == "__main__":
    sys.exit(main())
