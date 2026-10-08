"""Process-level failures of the backend, exercised exactly as Electron main would see them.

port busy · bad/short token · no token flag · non-loopback bind · corrupt exam file · crash (kill)
mid-exam and restart · stdin EOF / "shutdown" during a running exam · paths with spaces + Cyrillic.
All run on Linux here; Windows-specific behaviour (SO_EXCLUSIVEADDRUSE, Job objects, AV) is NOT
covered — see qa/scenarios/WINDOWS.md.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from qorgau_qa import contract
from qorgau_qa.backend import PROCTORING_ROOT, Api, BackendProcess, BackendStartError, backend_env


def _run_serve(args: list[str], stdin: str, env: dict[str, str] | None = None, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "proctor", "serve", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROCTORING_ROOT),
        env=backend_env(env),
        timeout=timeout,
    )


def test_port_already_in_use_fails_fast_without_ready(tmp_path):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        t0 = time.monotonic()
        token = "a" * 64
        proc = _run_serve(["--token-stdin", "--port", str(port)], token + "\n", env={"QORGAU_DATA_DIR": str(tmp_path)})
        elapsed = time.monotonic() - t0
    assert proc.returncode != 0
    assert "QORGAU_READY" not in proc.stdout
    assert elapsed < 20, f"took {elapsed:.1f}s to fail"
    assert token not in proc.stdout + proc.stderr


def test_ephemeral_port_avoids_busy_port(fresh_backend):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        be = fresh_backend()
        assert be.port != blocker.getsockname()[1]
        assert be.http.get("/health").status_code == 200
        assert be.stop() == 0


@pytest.mark.parametrize("token", ["", "short", "x" * 31])
def test_short_or_empty_token_refused(tmp_path, token):
    proc = _run_serve(["--token-stdin", "--port", "0"], token + "\n", env={"QORGAU_DATA_DIR": str(tmp_path)})
    assert proc.returncode == 2 and "QORGAU_READY" not in proc.stdout
    if token:
        assert token not in proc.stderr


def test_no_token_mode_refused(tmp_path):
    proc = _run_serve(["--port", "0"], "", env={"QORGAU_DATA_DIR": str(tmp_path)})
    assert proc.returncode == 2 and "QORGAU_READY" not in proc.stdout


def test_token_env_var_mode_works_and_value_not_logged(fresh_backend, tmp_path):
    token = "e" * 48
    env = backend_env({"QORGAU_DATA_DIR": str(tmp_path), "QA_TOKEN_VAR": token})
    proc = subprocess.Popen([sys.executable, "-m", "proctor", "serve", "--token-env", "QA_TOKEN_VAR", "--port", "0", "--exit-on-stdin-eof"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROCTORING_ROOT), env=env)
    try:
        line = proc.stdout.readline()
        assert line.startswith("QORGAU_READY ")
        import json

        port = json.loads(line.split(" ", 1)[1])["port"]
        assert httpx.get(f"http://127.0.0.1:{port}/v1/health", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    finally:
        out, err = proc.communicate(timeout=20)  # closes stdin → --exit-on-stdin-eof
    assert proc.returncode == 0
    assert token not in line + out + err


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_non_loopback_bind_refused(tmp_path, host):
    proc = _run_serve(["--token-stdin", "--port", "0"], "a" * 64 + "\n", env={"QORGAU_DATA_DIR": str(tmp_path), "QORGAU_HOST": host})
    assert proc.returncode != 0 and "QORGAU_READY" not in proc.stdout


def test_corrupt_exam_file_fails_visibly(tmp_path):
    bad = tmp_path / "exam.json"
    bad.write_text("{ this is not an exam", encoding="utf-8")
    with pytest.raises(BackendStartError) as exc:
        BackendProcess.start(tmp_path / "data", env={"QORGAU_EXAM_PATH": str(bad)}, ready_timeout=30)
    assert exc.value.returncode not in (None, 0), "backend must exit non-zero, not hang without READY"


def test_missing_exam_file_falls_back_to_labelled_demo_exam(fresh_backend, tmp_path):
    be = fresh_backend(env={"QORGAU_EXAM_PATH": str(tmp_path / "missing.json")})
    api = Api(be.http)
    sid = api.create()["session_id"]
    exam = contract.ok(be.http.get(f"/sessions/{sid}/exam"), "ExamDefinition")
    assert exam.is_demo is True
    assert any("not found" in l for l in be.stderr_lines), "fallback to the fixture exam must be logged"
    be.http.post(f"/sessions/{sid}/abort", json={"reason": "qa"})
    assert be.stop() == 0


def test_stdin_eof_during_running_exam_aborts_and_exits(fresh_backend):
    be = fresh_backend()
    api = Api(be.http)
    api.running_session()
    t0 = time.monotonic()
    rc = be.stop(timeout=20)
    elapsed = time.monotonic() - t0
    assert rc == 0, f"exit code {rc}"
    assert elapsed < 10, f"graceful shutdown took {elapsed:.1f}s (contract: ≤ ~5 s)"
    assert any("stdin closed" in l or "shutdown" in l for l in be.stderr_lines)
    assert not be.token_leaks()


def test_shutdown_line_stops_backend(fresh_backend):
    be = fresh_backend()
    Api(be.http).running_session()
    be.proc.stdin.write("shutdown\n")
    be.proc.stdin.flush()
    rc = be.proc.wait(20)
    be.exit_code = rc
    assert rc == 0


def test_crash_mid_exam_then_restart_on_same_data_dir(tmp_path):
    data = tmp_path / "data"
    be = BackendProcess.start(data)
    sid = Api(be.http).running_session()
    port = be.port
    be.kill()  # TerminateProcess/SIGKILL: no cleanup code runs
    assert be.exit_code != 0
    be2 = BackendProcess.start(data)
    try:
        api = Api(be2.http)
        health = contract.ok(be2.http.get("/health"), "HealthReport")
        assert health.active_session_id is None, "a crashed session must not block a new one"
        sid2 = api.running_session()
        assert sid2 != sid
        be2.http.post(f"/sessions/{sid2}/finish")
        # the old port is free again (no orphan listener left by the killed process)
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
    finally:
        assert be2.stop() == 0


def test_backend_ignores_duplicate_token_lines_and_extra_stdin(fresh_backend):
    """Garbage on stdin after the token must not stop the backend (only EOF or 'shutdown')."""
    be = fresh_backend()
    be.proc.stdin.write("not-a-command\n\n")
    be.proc.stdin.flush()
    time.sleep(0.5)
    assert be.proc.poll() is None and be.http.get("/health").status_code == 200
    assert be.stop() == 0


def _copy_tree(dst_root: Path) -> Path:
    """Copy backend + contracts (no venv, no data) to a new root; returns the copied proctoring/."""
    dst = dst_root / "proctoring"
    for part in ("backend", "contracts"):
        shutil.copytree(PROCTORING_ROOT / part, dst / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    return dst


def test_runs_from_path_with_spaces_and_cyrillic(tmp_path):
    """Linux-level check of path handling (PROCTORING_ROOT, fallback exam, data dir). Windows NOT covered."""
    root = tmp_path / "Мои документы" / "Qorgau Exam тест"
    copied = _copy_tree(root)
    data = root / "данные пользователя"
    env = {
        "PYTHONPATH": os.pathsep.join([str(copied / "backend"), str(copied / "contracts" / "python"), str(PROCTORING_ROOT / "qa")]),
        "QORGAU_EXAM_PATH": str(copied / "contracts" / "fixtures" / "v1" / "ExamDefinition.demo_min.json"),
    }
    be = BackendProcess.start(data, env=env, cwd=copied)
    try:
        api = Api(be.http)
        sid = api.running_session()
        exam = contract.ok(be.http.get(f"/sessions/{sid}/exam"), "ExamDefinition")
        assert exam.exam_id
        be.http.post(f"/sessions/{sid}/finish")
        mod = be.http.get("/health").json()
        assert mod["overall"] in ("ok", "degraded")
    finally:
        assert be.stop() == 0
    assert not be.token_leaks()


def test_backend_does_not_write_into_source_tree(tmp_path):
    """Evidence/data must land under QORGAU_DATA_DIR, never inside proctoring/ (privacy, Git)."""
    copied = _copy_tree(tmp_path / "src")
    before = {p for p in copied.rglob("*") if "__pycache__" not in p.parts}
    env = {"PYTHONPATH": os.pathsep.join([str(copied / "backend"), str(copied / "contracts" / "python"), str(PROCTORING_ROOT / "qa")]), "PYTHONDONTWRITEBYTECODE": "1"}
    be = BackendProcess.start(tmp_path / "data", env=env, cwd=copied)
    try:
        api = Api(be.http)
        sid = api.running_session()
        be.http.post(f"/sessions/{sid}/finish")
    finally:
        be.stop()
    after = {p for p in copied.rglob("*") if "__pycache__" not in p.parts}
    assert after == before, f"backend created files in the source tree: {sorted(str(p) for p in after - before)[:5]}"
