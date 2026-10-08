"""End to end through REAL processes: DEV harness (uvicorn) + fake student (WebSocket + HTTP upload) +
Chromium (Playwright) driving the teacher UI, then a server restart on the same data dir.
Skipped when Node/Playwright/Chromium or OpenCV are missing. All data are SYNTHETIC."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROCTORING = HERE.parents[1]
PIN = "246810"


def _node_env() -> dict | None:
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        return None
    root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
    env = {**os.environ, "NODE_PATH": os.pathsep.join(p for p in (root, os.environ.get("NODE_PATH", "")) if p)}
    return env if subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True).returncode == 0 else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int, data: Path, log: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "classreview.devserver", "--port", str(port), "--pin", PIN, "--data-dir", str(data)],
        cwd=PROCTORING, stdout=open(log, "a"), stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError(log.read_text()[-2000:])


def _node(phase: str, base: str, out: Path, env: dict) -> dict:
    p = subprocess.run(["node", str(HERE / "e2e_review.cjs"), phase, base, PIN, str(out)], capture_output=True, text=True, env=env, timeout=240)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_teacher_flow_in_browser_with_restart(tmp_path):
    env = _node_env()
    if env is None:
        pytest.skip("Node + Playwright + Chromium not available")
    pytest.importorskip("cv2")
    httpx = pytest.importorskip("httpx")
    port, data, log = _free_port(), tmp_path / "data", tmp_path / "server.log"
    base = f"http://127.0.0.1:{port}"
    server = _start_server(port, data, log)
    student = None
    try:
        c = httpx.Client(base_url=base)
        assert c.post("/api/teacher/login", json={"pin": PIN}).status_code == 200
        code = c.post("/api/teacher/session", json={"title": "e2e"}).json()["join_code"]
        student = subprocess.Popen(
            [sys.executable, "-m", "classreview.examples.fake_student", "--server", base, "--code", code, "--refuse", "2", "--run-seconds", "200"],
            cwd=PROCTORING, stdout=open(tmp_path / "student.log", "w"), stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            st = c.get("/api/teacher/students").json()
            if st and st[0]["incidents_total"] == 3:
                break
            time.sleep(0.3)
        assert st and st[0]["incidents_total"] == 3 and "_token" not in st[0]
        main = _node("main", base, tmp_path, env)
        failed = [s for s in main["steps"] if not s["ok"]]
        assert not failed, json.dumps(main, ensure_ascii=False, indent=1)
        assert any(r["status"] == 206 for r in main["clipResponses"]), main["clipResponses"]  # seeking used byte ranges
        assert main["console"] == [], main["console"]
    finally:
        if student is not None:
            student.terminate()
        server.terminate()
        server.wait(10)
    server = _start_server(port, data, log)  # restart on the same data dir
    try:
        after = _node("after-restart", base, tmp_path, env)
        failed = [s for s in after["steps"] if not s["ok"]]
        assert not failed, json.dumps(after, ensure_ascii=False, indent=1)
    finally:
        server.terminate()
        server.wait(10)
