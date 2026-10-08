"""Start and drive a REAL backend process exactly like Electron main (A06) will (owner: A09).

    with BackendProcess.start(tmp_path) as be:
        r = be.http.get("/health")

* spawn `<python> -m proctor serve --token-stdin --port 0` with cwd = proctoring/ (CONTRACTS.md §4)
* token = 32 random bytes hex, written to stdin line 1, never put on the command line or in env
* wait for the single `QORGAU_READY {json}` line on stdout; everything else is captured
* stop = close stdin (parent died) and require a clean exit; stderr/stdout are kept for token scans
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

QA_ROOT = Path(__file__).resolve().parents[1]  # proctoring/qa
PROCTORING_ROOT = QA_ROOT.parent  # proctoring/
READY_PREFIX = "QORGAU_READY "
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}
# Deterministic exam for API tests: the frozen contract fixture (QORGAU_EXAM_PATH). The real A10 exam
# (demo/exams/demo_exam.json) is checked separately on the integration candidate.
QA_EXAM_PATH = PROCTORING_ROOT / "contracts" / "fixtures" / "v1" / "ExamDefinition.demo_min.json"
EXAM_ID = json.loads(QA_EXAM_PATH.read_text(encoding="utf-8"))["exam_id"]


def backend_env(extra: dict[str, str] | None = None, *, data_dir: Path | None = None) -> dict[str, str]:
    """Environment for a backend child: import paths + isolated QORGAU_* settings, no inherited QORGAU_*."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("QORGAU_")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PROCTORING_ROOT / "backend"), str(PROCTORING_ROOT / "contracts" / "python"), str(QA_ROOT)]
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["QORGAU_LOG_LEVEL"] = "INFO"
    env["QORGAU_EXAM_PATH"] = str(QA_EXAM_PATH)
    if data_dir is not None:
        env["QORGAU_DATA_DIR"] = str(data_dir)
    env.update(extra or {})
    return env


@dataclass
class BackendProcess:
    proc: subprocess.Popen
    token: str = field(repr=False)  # never in tracebacks / logs
    ready: dict[str, Any] = field(default_factory=dict)
    ready_line: str = ""
    stdout_lines: list[str] = field(default_factory=list, repr=False)
    stderr_lines: list[str] = field(default_factory=list, repr=False)
    _http: httpx.Client | None = field(default=None, repr=False)
    exit_code: int | None = None

    # ------------------------------------------------------------------ start
    @classmethod
    def start(
        cls,
        data_dir: Path,
        *,
        env: dict[str, str] | None = None,
        argv_prefix: Sequence[str] | None = None,
        serve_args: Sequence[str] = ("--port", "0"),
        cwd: Path | None = None,
        token: str | None = None,
        ready_timeout: float = 60.0,
    ) -> "BackendProcess":
        """argv_prefix replaces `[python]` (e.g. a network sandbox wrapper); `-m proctor serve` is appended."""
        token = token or secrets.token_hex(32)
        cmd = [*(argv_prefix or [sys.executable]), "-m", "proctor", "serve", "--token-stdin", *serve_args]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd or PROCTORING_ROOT),
            env=backend_env(env, data_dir=data_dir),
        )
        out: list[str] = []
        err: list[str] = []
        ready_evt = threading.Event()
        threading.Thread(target=_pump, args=(proc.stdout, out, ready_evt), daemon=True).start()
        threading.Thread(target=_pump, args=(proc.stderr, err, None), daemon=True).start()
        assert proc.stdin is not None
        proc.stdin.write(token + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline and not ready_evt.is_set() and proc.poll() is None:
            ready_evt.wait(0.1)
        line = next((l for l in out if l.startswith(READY_PREFIX)), None)
        if line is None:
            rc = proc.poll()
            if rc is None:
                proc.kill()
                proc.wait(10)
            raise BackendStartError(proc.returncode, out, err)
        ready = json.loads(line[len(READY_PREFIX) :])
        return cls(proc=proc, token=token, ready=ready, ready_line=line, stdout_lines=out, stderr_lines=err)

    # ---------------------------------------------------------------- clients
    @property
    def port(self) -> int:
        return int(self.ready["port"])

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(base_url=self.base, headers=self.auth, timeout=20.0)
        return self._http

    def raw(self) -> httpx.Client:
        """Client WITHOUT credentials (negative tests). Caller closes it."""
        return httpx.Client(base_url=self.base, timeout=10.0)

    def ws_url(self, path: str = "/stream", query: str = "") -> str:
        return f"ws://127.0.0.1:{self.port}/v1{path}{('?' + query) if query else ''}"

    # ------------------------------------------------------------------- stop
    def stop(self, timeout: float = 20.0) -> int:
        """Close stdin like a dying parent; kill only if the backend does not exit in time."""
        if self._http is not None:
            self._http.close()
            self._http = None
        if self.exit_code is not None:
            return self.exit_code
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.exit_code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(10)
            self.exit_code = -999  # sentinel: had to kill
        time.sleep(0.05)  # let pump threads drain
        return self.exit_code

    def kill(self) -> None:
        """Hard crash (SIGKILL / TerminateProcess): no cleanup code runs in the backend."""
        if self._http is not None:
            self._http.close()
            self._http = None
        self.proc.kill()
        self.exit_code = self.proc.wait(10)

    def logs_contain(self, needle: str) -> bool:
        return any(needle in l for l in self.stdout_lines + self.stderr_lines)

    def token_leaks(self) -> list[str]:
        """Log lines that contain the token, with the token redacted (safe to print/commit)."""
        return [l.replace(self.token, "<TOKEN>").rstrip()[:300] for l in self.stdout_lines + self.stderr_lines if self.token in l]

    def __enter__(self) -> "BackendProcess":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


class BackendStartError(RuntimeError):
    def __init__(self, returncode: int | None, out: list[str], err: list[str]):
        self.returncode = returncode
        self.stdout_lines = out
        self.stderr_lines = err
        tail = "".join(err[-15:])
        super().__init__(f"backend did not print READY (exit={returncode}); stderr tail:\n{tail}")


def _pump(stream: Any, sink: list[str], ready_evt: threading.Event | None) -> None:
    for line in stream:
        sink.append(line)
        if ready_evt is not None and line.startswith(READY_PREFIX):
            ready_evt.set()


# --------------------------------------------------------------------------- flow helpers


def wait_for(predicate: Callable[[], Any], timeout: float, step: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(step)


def session_create_body(mode: str = "synthetic", **source: Any) -> dict[str, Any]:
    return {"source": {"mode": mode, **source}, "exam_id": EXAM_ID, "student_label": "qa-a09", "consent": dict(CONSENT)}


def env_event(action: str, seq: int, enforcement: str = "blocked", **detail: Any) -> dict[str, Any]:
    """EnvironmentEventIn as a fake shell would send it (mechanism labelled qa.fake_shell)."""
    return {
        "action": action,
        "enforcement": enforcement,
        "mechanism": "qa.fake_shell",
        "scope": "window",
        "client_seq": seq,
        "client_wall_time": "2026-10-08T09:00:05Z",
        "detail": {"shortcut": detail.get("shortcut"), "process_name": detail.get("process_name"), "duration_ms": detail.get("duration_ms")},
    }


FAKE_SHELL_CAPABILITIES = {
    "reported_at": "2026-10-08T09:00:00Z",
    "platform": "qa-fake-shell (NOT Windows, NOT a capability measurement)",
    "shell_version": "0.0.0-qa",
    "exam_mode_supported": True,
    "items": [  # treat as read-only; copy before modifying
        {"action": "shortcut_ctrl_c", "status": "blocked", "mechanism": "qa.fake", "verified_on": None, "note_ru": None},
        {"action": "shortcut_alt_tab", "status": "unverified", "mechanism": "none", "verified_on": None, "note_ru": None},
    ],
}


class Api:
    """Thin helper over one authenticated client: lifecycle steps with status assertions."""

    def __init__(self, http: httpx.Client):
        self.http = http

    def abort_active(self) -> None:
        sid = self.http.get("/health").json().get("active_session_id")
        if sid:
            self.http.post(f"/sessions/{sid}/abort", json={"reason": "qa cleanup"})

    def create(self, mode: str = "synthetic", **source: Any) -> dict[str, Any]:
        r = self.http.post("/sessions", json=session_create_body(mode, **source))
        assert r.status_code == 201, r.text
        return r.json()

    def ready(self, sid: str, calibrate: bool = False) -> dict[str, Any]:
        pf = self.http.post(f"/sessions/{sid}/preflight")
        assert pf.status_code == 200 and pf.json()["ready"] is True, pf.text
        if calibrate:
            self.calibrate(sid)
        else:
            r = self.http.post(f"/sessions/{sid}/calibration/skip", json={"reason": "qa: calibration not under test"})
            assert r.status_code == 200 and r.json()["phase"] == "skipped", r.text
        return pf.json()

    def calibrate(self, sid: str, timeout_per_target: float = 15.0) -> dict[str, Any]:
        r = self.http.post(f"/sessions/{sid}/calibration/start")
        assert r.status_code == 200, r.text
        for target in ("center", "left", "right", "up", "down"):
            r = self.http.post(f"/sessions/{sid}/calibration/target", json={"target": target})
            assert r.status_code == 200, r.text

            def done() -> bool:
                cal = self.http.get(f"/sessions/{sid}/calibration").json()
                return any(t["target"] == target and t["state"] == "ok" for t in cal["targets"])

            assert wait_for(done, timeout_per_target), f"calibration target {target} not collected"
        r = self.http.post(f"/sessions/{sid}/calibration/finish")
        assert r.status_code == 200 and r.json()["phase"] == "completed", r.text
        return r.json()

    def start(self, sid: str) -> dict[str, Any]:
        r = self.http.post(f"/sessions/{sid}/start")
        assert r.status_code == 200 and r.json()["state"] == "running", r.text
        return r.json()

    def running_session(self, calibrate: bool = False) -> str:
        sid = self.create()["session_id"]
        self.ready(sid, calibrate=calibrate)
        self.start(sid)
        return sid
