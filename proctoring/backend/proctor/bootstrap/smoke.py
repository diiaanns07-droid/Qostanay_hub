"""Synthetic end-to-end smoke test through a REAL backend process (owner: A01).

Starts `python -m proctor serve --token-stdin` exactly like Electron main does, performs the
READY handshake, drives one SYNTHETIC session through the public API + WebSocket stream and
checks that closing stdin stops the backend. It proves wiring only — not CV quality.
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
from typing import Any, Callable

PROCTORING_ROOT = Path(__file__).resolve().parents[3]


class _Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        return bool(ok)


def _wait(predicate: Callable[[], Any], timeout: float, step: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def run_smoke(as_json: bool = False) -> int:
    import httpx
    from websockets.sync.client import connect

    from proctor_contracts.v1 import StreamEnvelope

    res = _Result()
    token = secrets.token_hex(32)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PROCTORING_ROOT / "backend"), str(PROCTORING_ROOT / "contracts" / "python"), env.get("PYTHONPATH", "")]
    )
    env.setdefault("QORGAU_LOG_LEVEL", "WARNING")
    proc = subprocess.Popen(
        [sys.executable, "-m", "proctor", "serve", "--token-stdin", "--port", "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    stderr_tail: list[str] = []
    threading.Thread(target=lambda: [stderr_tail.append(l) for l in proc.stderr], daemon=True).start()  # type: ignore[union-attr]
    messages: list[dict] = []
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write(token + "\n")
        proc.stdin.flush()
        ready_line: list[str] = []
        threading.Thread(target=lambda: ready_line.append(proc.stdout.readline()), daemon=True).start()  # type: ignore[union-attr]
        _wait(lambda: ready_line, 60)
        line = ready_line[0] if ready_line else ""
        if not res.check("ready_handshake", line.startswith("QORGAU_READY "), line.strip()[:160]):
            raise RuntimeError("backend did not become ready")
        ready = json.loads(line[len("QORGAU_READY ") :])
        res.check("token_not_in_ready_line", token not in line, "token absent from stdout")
        base = f"http://127.0.0.1:{ready['port']}/v1"
        auth = {"Authorization": f"Bearer {token}"}
        http = httpx.Client(base_url=base, headers=auth, timeout=15.0)

        r = httpx.get(f"{base}/health", timeout=5)
        res.check("rejects_missing_token", r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED", str(r.status_code))
        r = httpx.get(f"{base}/health", headers={**auth, "Origin": "http://evil.example"}, timeout=5)
        res.check("rejects_foreign_origin", r.status_code == 403, str(r.status_code))
        r = http.get("/health")
        comps = {c["component"]: c["code"] for c in r.json()["components"]} if r.status_code == 200 else {}
        res.check("health", r.status_code == 200, json.dumps(comps, ensure_ascii=False))

        ws = connect(f"ws://127.0.0.1:{ready['port']}/v1/stream", additional_headers=auth, open_timeout=10)
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    messages.append(json.loads(ws.recv(timeout=0.5)))
                except TimeoutError:
                    continue
                except Exception:
                    return

        threading.Thread(target=reader, daemon=True).start()

        consent = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}
        exam_id = "demo-exam-1"
        exam_probe = None

        # LIVE must never fall back to synthetic parts
        r = http.post("/sessions", json={"source": {"mode": "live"}, "exam_id": exam_id, "consent": consent})
        if r.status_code == 422:
            exam_probe = r.json()
        live = r.json() if r.status_code == 201 else None
        if live:
            pf = http.post(f"/sessions/{live['session_id']}/preflight").json()
            cam = next(c for c in pf["checks"] if c["check_id"] == "camera")
            res.check(
                "live_without_capture_module_is_not_ready",
                pf["ready"] is False and cam["status"] == "fail",
                f"camera={cam['status']}:{cam['message_code']} (expected until A02 integrates)",
            )
            http.post(f"/sessions/{live['session_id']}/abort", json={"reason": "smoke: live not available"})
        else:
            res.check("live_without_capture_module_is_not_ready", False, f"create failed: {r.status_code} {exam_probe}")

        r = http.post(
            "/sessions",
            json={"source": {"mode": "synthetic"}, "exam_id": exam_id, "student_label": "smoke", "consent": consent},
        )
        res.check("create_synthetic_session", r.status_code == 201, str(r.status_code))
        sid = r.json()["session_id"]
        r = http.post("/sessions", json={"source": {"mode": "synthetic"}, "exam_id": exam_id, "consent": consent})
        res.check("second_active_session_rejected", r.status_code == 409 and r.json()["error"]["code"] == "SESSION_ACTIVE", str(r.status_code))

        pf = http.post(f"/sessions/{sid}/preflight").json()
        res.check("preflight_ready_synthetic", pf["ready"] is True, ", ".join(f"{c['check_id']}={c['status']}" for c in pf["checks"]))

        cal = http.post(f"/sessions/{sid}/calibration/start").json()
        res.check("calibration_started", cal["phase"] == "collecting", cal["phase"])
        for target in ("center", "left", "right", "up", "down"):
            http.post(f"/sessions/{sid}/calibration/target", json={"target": target})
            ok = _wait(
                lambda: next(
                    t for t in http.get(f"/sessions/{sid}/calibration").json()["targets"] if t["target"] == target
                )["state"]
                == "ok",
                10,
            )
            res.check(f"calibration_target_{target}", bool(ok), "samples collected from frames, not a timer")
        cal = http.post(f"/sessions/{sid}/calibration/finish").json()
        res.check("calibration_completed", cal["phase"] == "completed", cal["phase"])

        info = http.post(f"/sessions/{sid}/start").json()
        res.check("exam_running", info.get("state") == "running", str(info.get("state")))
        r = http.put(f"/sessions/{sid}/answers/q1", json={"value": ["b"], "client_seq": 1})
        res.check("answer_autosave", r.status_code == 200, str(r.status_code))

        ev = {
            "action": "shortcut_alt_tab",
            "enforcement": "detected_only",
            "mechanism": "smoke.fake_shell",
            "scope": "window",
            "client_seq": 1,
            "client_wall_time": "2026-10-08T09:00:05Z",
            "detail": {"shortcut": "Alt+Tab", "process_name": None, "duration_ms": None},
        }
        ack1 = http.post(f"/sessions/{sid}/environment/events", json={"session_id": sid, "events": [ev]}).json()
        ack2 = http.post(f"/sessions/{sid}/environment/events", json={"session_id": sid, "events": [ev]}).json()
        res.check("environment_event_dedup", ack1.get("accepted") == 1 and ack2.get("duplicates") == 1, f"{ack1} / {ack2}")

        def phone_incident() -> dict | None:
            for m in messages:
                msg = m.get("message", {})
                if msg.get("type") == "incident" and msg["change"]["incident"]["rule_id"] == "phone_visible":
                    return msg["change"]["incident"]
            return None

        inc = _wait(phone_incident, 30)
        res.check(
            "synthetic_phone_incident_on_stream",
            inc is not None and inc["source_mode"] == "synthetic",
            (inc or {}).get("explanation", {}).get("summary_ru", "no incident within 30 s"),
        )
        incidents = http.get(f"/sessions/{sid}/incidents").json()
        res.check("incidents_api", isinstance(incidents, list) and len(incidents) >= 1, f"{len(incidents)} incident(s)")
        if incidents:
            iid = incidents[0]["incident_id"]
            rv = http.post(
                f"/sessions/{sid}/incidents/{iid}/reviews",
                json={"decision": "dismissed", "comment": "smoke <b>test</b>", "operator": "smoke"},
            )
            detail = http.get(f"/sessions/{sid}/incidents/{iid}").json()
            res.check("human_review", rv.status_code == 200 and detail["incident"]["review_status"] == "dismissed", str(rv.status_code))
        r = http.get(f"/sessions/{sid}/preview.jpg")
        res.check(
            "preview_jpeg",
            r.status_code == 200 and r.content[:2] == b"\xff\xd8",
            f"{r.status_code}, {len(r.content)} bytes, meta={'X-Qorgau-Preview-Meta' in r.headers}",
        )
        m = http.get(f"/sessions/{sid}/metrics").json()
        res.check("metrics", m.get("frames_captured", 0) > 0, f"capture_fps={m.get('capture_fps')} frames={m.get('frames_captured')}")

        info = http.post(f"/sessions/{sid}/finish").json()
        res.check("finish", info.get("state") == "finished", str(info.get("state")))
        again = http.post(f"/sessions/{sid}/finish")
        res.check("finish_idempotent", again.status_code == 200 and again.json()["state"] == "finished", str(again.status_code))
        r = http.put(f"/sessions/{sid}/answers/q1", json={"value": ["a"], "client_seq": 2})
        res.check("no_writes_after_finish", r.status_code == 409, str(r.status_code))
        closed = _wait(
            lambda: any(
                x.get("message", {}).get("type") == "session_state" and x["message"]["session"]["state"] == "finished"
                for x in messages
            ),
            5,
        )
        res.check("finished_state_on_stream", bool(closed))

        r = http.post("/sessions", json={"source": {"mode": "synthetic"}, "exam_id": exam_id, "consent": consent})
        res.check("restart_new_session_after_finish", r.status_code == 201, str(r.status_code))
        if r.status_code == 201:
            sid2 = r.json()["session_id"]
            pf2 = http.post(f"/sessions/{sid2}/preflight").json()
            res.check("capture_reopens_for_new_session", pf2["ready"] is True)
            http.post(f"/sessions/{sid2}/abort", json={"reason": "smoke cleanup"})

        stop.set()
        ws.close()
        bad = 0
        for raw in messages:
            try:
                StreamEnvelope.model_validate(raw)
            except Exception:
                bad += 1
        res.check("stream_envelopes_match_contract", len(messages) > 0 and bad == 0, f"{len(messages)} messages, {bad} invalid")
        foreign = [x for x in messages if x.get("session_id") not in (None, sid, live and live["session_id"], locals().get("sid2"))]
        res.check("no_foreign_session_messages", not foreign, f"{len(foreign)} foreign")
        http.close()
    except Exception as exc:  # report, do not hide
        res.check("smoke_completed_without_exception", False, f"{type(exc).__name__}: {exc}")
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()  # parent "dies" -> backend must exit
        try:
            rc = proc.wait(timeout=15)
            res.check("exits_on_stdin_eof", rc == 0, f"exit code {rc}")
        except subprocess.TimeoutExpired:
            proc.kill()
            res.check("exits_on_stdin_eof", False, "killed after 15 s")
    leaked = [l for l in stderr_tail if token in l]
    res.check("token_not_in_logs", not leaked, f"{len(stderr_tail)} stderr lines scanned")

    passed = all(ok for _, ok, _ in res.rows)
    if as_json:
        print(json.dumps({"passed": passed, "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in res.rows]}, ensure_ascii=False, indent=2))
    else:
        for name, ok, detail in res.rows:
            print(f"{'PASS' if ok else 'FAIL'}  {name:45s} {detail}")
        print(f"\nSMOKE {'PASSED' if passed else 'FAILED'}: {sum(o for _, o, _ in res.rows)}/{len(res.rows)} checks (SYNTHETIC data, not CV accuracy)")
        if not passed and stderr_tail:
            print("--- backend stderr (tail) ---", *stderr_tail[-30:], sep="\n", file=sys.stderr)
    return 0 if passed else 1
