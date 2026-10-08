"""Timed backend rehearsal, using real HTTP/process IO; no camera or keyboard hooks.

Synthetic mode checks orchestration only. Replay needs A02/A03/A04/A05/A08 integrated
by A01 and a consented local recording. It never falls back to synthetic.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "contracts" / "python"))
from proctor_contracts.v1 import ExamDefinition, Incident, SessionInfo, SessionSummary


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", check=True)
    return result.stdout.strip()


def run(args: argparse.Namespace) -> dict:
    sha = git("rev-parse", "HEAD")
    if args.expected_sha and sha != args.expected_sha:
        raise ValueError("HEAD does not match --expected-sha; use A01's exact candidate")
    if args.mode == "replay" and (not args.replay_id or not args.replay_dir):
        raise ValueError("replay requires --replay-id and --replay-dir")
    exam_path = ROOT / "demo" / "exams" / "demo_exam.json"
    exam = ExamDefinition.model_validate_json(exam_path.read_text(encoding="utf-8"))
    started = time.monotonic()
    rows: list[dict] = []
    report = {
        "tested_sha": sha, "contract": "qorgau.v1", "source_mode": args.mode,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "exam_sha256": hashlib.sha256(exam_path.read_bytes()).hexdigest(),
        "rehearsal_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "product_tree_dirty": bool(git("status", "--porcelain", "--", ".", ":!demo", ":!docs/pitch", ":!handoffs/A10")),
        "scope": "Backend API rehearsal. No Electron UI, real camera or OS protection is exercised.",
        "checks": rows, "product_release_verified": False,
    }

    def record(name: str, status: str, detail: str = "") -> None:
        rows.append({"check": name, "status": status,
                     "elapsed_s": round(time.monotonic() - started, 2), "detail": detail[:400]})

    # A temporary session contains only explicitly synthetic data or the local replay.
    # No images, tokens, names or raw backend logs are included in the report.
    with tempfile.TemporaryDirectory(prefix="qorgau-a10-") as data_dir:
        env = {k: v for k, v in os.environ.items() if not k.startswith("QORGAU_")}
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   PYTHONPATH=os.pathsep.join([str(ROOT / "backend"), str(ROOT / "contracts" / "python")]),
                   QORGAU_DATA_DIR=data_dir, QORGAU_EXAM_PATH=str(exam_path))
        if args.replay_dir:
            env["QORGAU_REPLAY_DIR"] = str(Path(args.replay_dir).resolve())
        if args.models_dir:
            env["QORGAU_MODELS_DIR"] = str(Path(args.models_dir).resolve())
        token = secrets.token_hex(32)
        proc = subprocess.Popen([sys.executable, "-m", "proctor", "serve", "--token-stdin", "--port", "0"],
                                cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        ready_queue: queue.Queue[str] = queue.Queue()
        token_leaked = threading.Event()

        def pump(stream, readiness: bool) -> None:
            for line in stream:
                if token in line:
                    token_leaked.set()
                if readiness and line.startswith("QORGAU_READY "):
                    ready_queue.put(line)
        pumps = [threading.Thread(target=pump, args=(proc.stdout, True), daemon=True),
                 threading.Thread(target=pump, args=(proc.stderr, False), daemon=True)]
        for thread in pumps:
            thread.start()
        sid = None
        request = None
        try:
            proc.stdin.write(token + "\n")
            proc.stdin.flush()
            ready = json.loads(ready_queue.get(timeout=60).split(" ", 1)[1])
            if ready.get("contract") != "qorgau.v1":
                raise ValueError("Unexpected backend contract")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def request(method: str, route: str, payload=None, expected=(200,)):
                data = None if payload is None else json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(f"http://127.0.0.1:{ready['port']}/v1{route}", data=data,
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method=method)
                try:
                    response = opener.open(req, timeout=15)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    raw = response.read()
                    if response.status not in expected:
                        raise ValueError(f"{method} {route}: HTTP {response.status}")
                    body = json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw
                    return response.status, body

            record("backend_ready", "PASS")
            source = {"mode": args.mode}
            if args.mode == "replay":
                source["replay_id"] = args.replay_id
            _, info = request("POST", "/sessions", {"exam_id": exam.exam_id, "source": source,
                "student_label": "A10 demo", "retain_media": False, "consent": {
                    "accepted": True, "text_version": "a10-demo-1",
                    "accepted_at": dt.datetime.now(dt.timezone.utc).isoformat()}}, (201,))
            session = SessionInfo.model_validate(info)
            sid = session.session_id
            if session.source_mode.value != args.mode:
                raise ValueError("Source mode changed unexpectedly")
            record("session_mode", "PASS", args.mode)
            _, served = request("GET", f"/sessions/{sid}/exam")
            if ExamDefinition.model_validate(served) != exam:
                raise ValueError("Backend is not serving the A10 exam")
            record("demo_exam_served", "PASS", f"{len(exam.questions)} questions")
            _, preflight = request("POST", f"/sessions/{sid}/preflight")
            if not preflight["ready"]:
                failed = [c["check_id"] for c in preflight["checks"] if c["required"] and c["status"] != "pass"]
                raise ValueError("Preflight failed: " + ", ".join(failed))
            record("preflight", "PASS", "Inspect source mode; synthetic readiness does not prove CV readiness")
            # A replay cannot follow interactive screen targets. This omission is visible.
            request("POST", f"/sessions/{sid}/calibration/skip", {"reason": f"A10 {args.mode} API rehearsal; calibration not tested"})
            record("calibration", "NOT_RUN", "Explicitly skipped for automated rehearsal; do it during LIVE rehearsal")
            request("POST", f"/sessions/{sid}/start")
            request("PUT", f"/sessions/{sid}/answers/q1", {"value": ["b"], "client_seq": 1})
            record("start_and_save_answer", "PASS")
            deadline = time.monotonic() + args.wait_seconds
            incident = None
            while time.monotonic() < deadline:
                _, items = request("GET", f"/sessions/{sid}/incidents")
                incident = next((Incident.model_validate(x) for x in items if x["rule_id"] == "phone_visible"), None)
                if incident is not None:
                    break
                time.sleep(0.25)
            if incident is None:
                raise ValueError("No phone_visible episode within rehearsal timeout")
            if incident.source_mode.value != args.mode or not incident.explanation.summary_ru:
                raise ValueError("Episode is missing its mode or explanation")
            record("phone_episode", "PASS", "Synthetic signal" if args.mode == "synthetic" else "Detected from replay; not an accuracy metric")
            request("POST", f"/sessions/{sid}/finish")
            record("finish", "PASS")
            request("POST", f"/sessions/{sid}/incidents/{incident.incident_id}/reviews", {
                "decision": "inconclusive", "comment": "Учебный показ; требуется проверка преподавателем.", "operator": "demo-teacher"})
            record("human_review", "PASS")
            _, summary = request("GET", f"/sessions/{sid}/summary")
            SessionSummary.model_validate(summary)
            record("summary", "PASS")
            for suffix in ("html", "json"):
                status, _ = request("GET", f"/sessions/{sid}/report.{suffix}", expected=(200, 501))
                record(f"report_{suffix}", "PASS" if status == 200 else "NOT_RUN",
                       "Export endpoint responded" if status == 200 else "A08 not integrated; HTTP 501")
            status, _ = request("GET", f"/sessions/{sid}/preview.jpg", expected=(204,))
            record("capture_stopped", "PASS", str(status))
        except Exception as exc:
            record("rehearsal", "FAIL", str(exc).replace(token, "<redacted>"))
        finally:
            if sid and request:
                try:
                    request("POST", f"/sessions/{sid}/abort", {"reason": "A10 cleanup"}, (200, 409))
                except Exception:
                    pass
            try:
                proc.stdin.close()
                rc = proc.wait(20)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait(10)
                rc = -999
            for thread in pumps:
                thread.join(2)
            record("backend_shutdown", "PASS" if rc == 0 else "FAIL", f"exit={rc}")
            record("token_not_logged", "FAIL" if token_leaked.is_set() else "PASS")
    record("live_camera_and_gaze", "NOT_RUN", "Requires voluntary LIVE rehearsal on A01 candidate")
    record("windows_shortcuts_and_emergency_exit", "NOT_RUN", "Requires A06 shell and controlled manual test")
    report["duration_s"] = round(time.monotonic() - started, 2)
    report["automated_rehearsal_status"] = "FAIL" if any(r["status"] == "FAIL" for r in rows) else "PASS"
    report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("synthetic", "replay"), default="synthetic")
    parser.add_argument("--expected-sha")
    parser.add_argument("--replay-id")
    parser.add_argument("--replay-dir")
    parser.add_argument("--models-dir")
    parser.add_argument("--wait-seconds", type=float, default=35)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.wait_seconds <= 0 or args.wait_seconds > 180:
        parser.error("--wait-seconds must be in (0, 180]")
    report = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["automated_rehearsal_status"], "duration_s": report["duration_s"],
                      "mode": args.mode, "product_release_verified": False, "report": str(args.out)}, ensure_ascii=False))
    return 1 if report["automated_rehearsal_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
