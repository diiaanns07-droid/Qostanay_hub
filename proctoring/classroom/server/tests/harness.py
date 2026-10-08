"""Test harness for the class server (owner: T01): a REAL `python -m classroom.server` process + WS clients.

Timings are shortened through QORGAU_CLASS_* env vars so heartbeat/expiry tests take seconds, not minutes.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

PROCTORING = Path(__file__).resolve().parents[3]
FAST_ENV = {
    "QORGAU_CLASS_PING_INTERVAL_S": "0.3",
    "QORGAU_CLASS_PONG_TIMEOUT_S": "1.2",
    "QORGAU_CLASS_STATUS_STALE_S": "1.0",
    "QORGAU_CLASS_ACK_WINDOW_S": "0.8",
    "QORGAU_CLASS_JOIN_BLOCK_S": "1.5",
    "QORGAU_CLASS_PIN_BLOCK_S": "1.5",
    "QORGAU_CLASS_TICK_S": "0.1",
    "QORGAU_CLASS_PREVIEW_MIN_INTERVAL_S": "0",
    "QORGAU_CLASS_LOG_LEVEL": "INFO",
    "QORGAU_CLASS_UI": "none",  # UI-serving tests opt in explicitly (test_panel_c2.py)
}
# smallest valid JPEG-like payload (SOI ... EOI); the server only checks markers and size
TINY_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


def now_iso(offset_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat()


def env_for(data_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("QORGAU_CLASS_")}
    env["PYTHONPATH"] = os.pathsep.join([str(PROCTORING), str(PROCTORING / "backend"), str(PROCTORING / "contracts" / "python")])
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(FAST_ENV)
    env["QORGAU_CLASS_DATA_DIR"] = str(data_dir)
    env.update(extra or {})
    return env


class ServerProcess:
    def __init__(self, data_dir: Path, extra_env: dict[str, str] | None = None):
        self.data_dir = data_dir
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "classroom.server", "--port", "0", "--host", "127.0.0.1", "--exit-on-stdin-eof"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            cwd=str(PROCTORING), env=env_for(data_dir, extra_env),
        )
        self.stdout: list[str] = []
        self.stderr: list[str] = []
        ready = threading.Event()
        threading.Thread(target=self._pump, args=(self.proc.stdout, self.stdout, ready), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, self.stderr, None), daemon=True).start()
        if not ready.wait(30):
            self.proc.kill()
            raise RuntimeError("server did not print READY+PIN:\n" + "".join(self.stderr[-30:]))
        line = next(l for l in self.stdout if l.startswith("QORGAU_CLASS_READY "))
        self.ready = json.loads(line.split(" ", 1)[1])
        self.pin = next(l for l in self.stdout if l.startswith("QORGAU_CLASS_PIN ")).split()[1]
        self.port = int(self.ready["port"])
        self.base = f"http://127.0.0.1:{self.port}"
        self.exit_code: int | None = None

    @staticmethod
    def _pump(stream: Any, sink: list[str], ready: threading.Event | None) -> None:
        for line in stream:
            sink.append(line)
            if ready is not None and line.startswith("QORGAU_CLASS_PIN "):
                ready.set()

    def teacher(self, login: bool = True) -> httpx.Client:
        c = httpx.Client(base_url=self.base, timeout=10)
        if login:
            r = c.post("/api/teacher/login", json={"pin": self.pin})
            assert r.status_code == 200, r.text
        return c

    def student(self, **kw: Any) -> "StudentClient":
        return StudentClient(f"ws://127.0.0.1:{self.port}/ws/student", **kw)

    def stop(self, timeout: float = 15) -> int:
        if self.exit_code is not None:
            return self.exit_code
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        try:
            self.exit_code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.exit_code = self.proc.wait(5)
        return self.exit_code

    def kill(self) -> None:
        self.proc.kill()
        self.exit_code = self.proc.wait(5)


class StudentClient:
    """qorgau.class.v1 student. A reader thread stores every server message and auto-answers ping with pong
    unless `silent` is set (simulates a frozen/vanished client)."""

    def __init__(self, url: str, *, auto_pong: bool = True):
        self._cm = connect(url, open_timeout=10, max_size=2**20)
        self.ws = self._cm.__enter__()
        self.messages: list[dict[str, Any]] = []
        self.auto_pong = auto_pong
        self.silent = False
        self.closed: ConnectionClosed | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.ws.recv(timeout=0.2)
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                self.closed = exc
                return
            except Exception:
                return
            msg = json.loads(raw)
            with self._lock:
                self.messages.append(msg)
            if msg.get("type") == "ping" and self.auto_pong and not self.silent:
                self.send("pong")

    def send(self, type_: str, **fields: Any) -> None:
        if self.silent:
            return
        self.ws.send(json.dumps({"type": type_, "v": 1, "msg_id": secrets.token_hex(8), "sent_at": now_iso(), **fields}))

    def send_raw(self, text: str) -> None:
        self.ws.send(text)

    def hello(self, *, join_code: str | None = None, resume_token: str | None = None, timeout: float = 5, **fields: Any) -> dict[str, Any]:
        body = {"protocol": "qorgau.class.v1", "computer_name": fields.pop("computer_name", "PC-01"), "student_label": fields.pop("student_label", "Студент 1"), "app_version": fields.pop("app_version", "test-client/1")}
        if join_code is not None:
            body["join_code"] = join_code
        if resume_token is not None:
            body["resume_token"] = resume_token
        body.update(fields)
        self.send("hello", **body)
        return self.wait_for(lambda m: m["type"] in ("welcome", "error"), timeout)

    def wait_for(self, pred: Callable[[dict[str, Any]], bool], timeout: float = 5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < deadline:
            with self._lock:
                batch = self.messages[seen:]
                seen = len(self.messages)
            for msg in batch:
                if pred(msg):
                    return msg
            time.sleep(0.02)
        raise AssertionError(f"no matching message within {timeout}s; got types {[m['type'] for m in self.snapshot()][-15:]}")

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.snapshot() if m.get("type") == kind]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.messages)

    def wait_closed(self, timeout: float = 5) -> ConnectionClosed:
        self._thread.join(timeout)
        assert self.closed is not None, "socket still open"
        return self.closed

    def status(self, **over: Any) -> None:
        body = {"exam_state": "running", "camera": "ok", "monitoring": "ok", "zone": "green", "zone_reasons_ru": [], "incidents_total": 0,
                "incidents_by_priority": {"low": 0, "medium": 0, "high": 0}, "locked": False, "mic_active": False}
        body.update(over)
        self.send("status", **body)

    def incident(self, seq: int, incident_id: str, state: str = "open", **over: Any) -> None:
        body = {"seq": seq, "incident_id": incident_id, "rule_id": "phone_visible", "category": "phone", "priority": "medium", "state": state,
                "t_start_wall": "2026-10-08T09:00:00+00:00", "duration_ms": 0 if state == "open" else 4200.0,
                "explanation_ru": "Телефон виден 4,2 с", "clip_available": False}
        body.update(over)
        self.send("incident", **body)

    def preview(self, data: bytes = TINY_JPEG) -> None:
        self.send("preview", jpeg_b64=base64.b64encode(data).decode(), frame_wall=now_iso())

    def close(self) -> None:
        self._stop.set()
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass
        self._thread.join(2)


class TeacherStream:
    def __init__(self, server: ServerProcess, cookie: str | None, query: str = ""):
        headers = {"Cookie": f"qorgau_teacher={cookie}"} if cookie else {}
        self._cm = connect(f"ws://127.0.0.1:{server.port}/ws/teacher{('?' + query) if query else ''}", additional_headers=headers, open_timeout=10, max_size=2**22)
        self.ws = self._cm.__enter__()
        self.messages: list[dict[str, Any]] = []
        self.closed: ConnectionClosed | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.ws.recv(timeout=0.2)
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                self.closed = exc
                return
            except Exception:
                return
            with self._lock:
                self.messages.append(json.loads(raw))

    def send(self, payload: dict[str, Any]) -> None:
        self.ws.send(json.dumps(payload))

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.messages)

    def wait_for(self, pred: Callable[[dict[str, Any]], bool], timeout: float = 5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self.snapshot():
                if pred(msg):
                    return msg
            time.sleep(0.02)
        raise AssertionError(f"teacher stream: nothing matched within {timeout}s; types {[m['type'] for m in self.snapshot()][-15:]}")

    def close(self) -> None:
        self._stop.set()
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass
        self._thread.join(2)


def wait_until(pred: Callable[[], Any], timeout: float = 5, step: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(step)
