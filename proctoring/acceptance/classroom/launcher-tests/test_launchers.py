"""Windows launcher integration checks. No camera, Electron window, microphone or hooks.

Run with the project's Python 3.12; no pytest dependency. Child output stays in memory.
Only sanitized diagnostics are emitted; PINs and session credentials are not saved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

LAUNCHERS = Path(__file__).resolve().parents[1]
PYTHON = str(Path(sys.executable).resolve())
POWERSHELL = os.environ.get("QORGAU_TEST_POWERSHELL", "powershell.exe")


def environment():
    return {k: v for k, v in os.environ.items() if not k.startswith("QORGAU_")}


def command(script, *args):
    return [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(LAUNCHERS / script), "-Python", PYTHON, *map(str, args)]


def sanitized(text):
    text = re.sub(r"(?im)^.*(?:PIN|QORGAU_CLASS_PIN).*$", "[PIN omitted]", text)
    return re.sub(r"(?<!\d)\d{6}(?!\d)", "[code omitted]", text)


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class RunningTeacher:
    def __init__(self, data, *extra):
        self.process = subprocess.Popen(command("Start-Teacher.ps1", "-Port", "0", "-DataDir", data, *extra),
                                        cwd=tempfile.gettempdir(), env=environment(), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace")
        self.lines = []
        self.queue = queue.Queue()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.port = None

    def read(self):
        for line in self.process.stdout:
            self.lines.append(line)
            self.queue.put(line)
        self.queue.put(None)

    def ready(self):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                line = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                raise AssertionError(sanitized("".join(self.lines)))
            match = re.search(r"QORGAU_LAUNCH_READY teacher port=(\d+) pid=(\d+)", line)
            if match:
                self.port = int(match[1])
                self.pid = int(match[2])
                return self.port
        raise AssertionError("No readiness: " + sanitized("".join(self.lines)))

    def close(self):
        if self.process.poll() is None:
            self.process.kill()  # exercise abrupt parent loss; job must close all descendants
        self.process.wait(timeout=15)
        self.reader.join(timeout=10)
        self.process.stdin.close()
        self.process.stdout.close()
        if self.port:
            deadline = time.monotonic() + 10
            while listening(self.port) and time.monotonic() < deadline:
                time.sleep(0.1)
            if listening(self.port):
                raise AssertionError("Orphan class server still listening")


@unittest.skipUnless(sys.platform == "win32", "Windows launcher tests")
class Launchers(unittest.TestCase):
    def run_script(self, name, *args, env=None, success=True):
        result = subprocess.run(command(name, *args), cwd=tempfile.gettempdir(), env=env or environment(),
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
        if success:
            self.assertEqual(result.returncode, 0, sanitized(result.stdout + result.stderr))
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_01_parse_both_scripts(self):
        for script in ("Start-Teacher.ps1", "Start-Student.ps1"):
            path = str(LAUNCHERS / script).replace("'", "''")
            code = f"$t=$null;$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e);if($e.Count){{exit 1}}"
            result = subprocess.run([POWERSHELL, "-NoProfile", "-Command", code], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, script)

    def test_02_check_only_does_not_create_data_or_listen(self):
        with tempfile.TemporaryDirectory(prefix="qorgau-launch-check-") as directory:
            data = Path(directory) / "never-created"
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                port = reserved.getsockname()[1]
                # An occupied port must not interfere: check-only never binds it.
                self.run_script("Start-Teacher.ps1", "-Port", port, "-DataDir", data, "-CheckOnly")
            self.assertFalse(data.exists())
            self.assertFalse(listening(port))
            env = environment()
            env["ELECTRON_RUN_AS_NODE"] = "1"
            env["QORGAU_SHELL_NATIVE_ENFORCE"] = "1"
            self.run_script("Start-Student.ps1", "-Server", f"127.0.0.1:{port}", "-JoinCode", "000000",
                            "-Label", "LAUNCHER CHECK", "-BackendOnly", "-CheckOnly", "-DataDir", data, env=env)
            self.assertFalse(data.exists())

    def test_03_bad_server_code_and_python_fail_locally(self):
        for args in (("-Server", "http://127.0.0.1:8765", "-JoinCode", "000000"),
                     ("-Server", "127.0.0.1:70000", "-JoinCode", "000000"),
                     ("-Server", "127.0.0.1:8765", "-JoinCode", "wrong")):
            self.run_script("Start-Student.ps1", *args, "-CheckOnly", "-Label", "CHECK", success=False)
        # Invoke separately to avoid duplicate -Python arguments.
        result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(LAUNCHERS / "Start-Teacher.ps1"), "-Python", "Z:\\missing\\python.exe", "-CheckOnly"],
                                capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)

    def test_04_real_c1_ephemeral_ready_http_and_graceful_eof(self):
        with tempfile.TemporaryDirectory(prefix="qorgau-launch-c1-") as directory:
            server = RunningTeacher(Path(directory) / "data with spaces", "-StopAfterSeconds", "3")
            try:
                port = server.ready()
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/config.json", timeout=3) as response:
                    self.assertEqual(json.load(response), {"adapter": "real", "features": ["history", "exams", "audio"]})
                self.assertEqual(server.process.wait(timeout=20), 0, sanitized("".join(server.lines)))
                self.assertFalse(listening(port))
            finally:
                server.close()

    def test_05_killing_launcher_leaves_no_server(self):
        with tempfile.TemporaryDirectory(prefix="qorgau-launch-kill-") as directory:
            server = RunningTeacher(Path(directory) / "data")
            try:
                server.ready()
            finally:
                server.close()

    def test_06_busy_port_fails_without_leaving_process(self):
        with tempfile.TemporaryDirectory(prefix="qorgau-launch-busy-") as directory, socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            reserved.listen()
            self.run_script("Start-Teacher.ps1", "-Port", reserved.getsockname()[1], "-DataDir", directory,
                            "-ReadyTimeoutSeconds", "5", success=False)

    def test_07_missing_electron_is_reported_without_old_checkout_fallback(self):
        electron = LAUNCHERS.parents[1] / "desktop/node_modules/electron/dist/electron.exe"
        if electron.exists():
            self.skipTest("Current checkout has Electron; missing-dependency case not applicable")
        result = self.run_script("Start-Student.ps1", "-Server", "127.0.0.1:8765", "-JoinCode", "000000",
                                 "-Label", "CHECK", "-CheckOnly", success=False)
        self.assertIn("npm ci", result.stdout)

    def test_08_real_backend_ready_then_eof_without_exam_or_camera(self):
        with tempfile.TemporaryDirectory(prefix="qorgau-launch-backend-") as directory, socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            result = self.run_script("Start-Student.ps1", "-Server", f"127.0.0.1:{reserved.getsockname()[1]}",
                                     "-JoinCode", "000000", "-Label", "LAUNCHER BACKEND CHECK", "-BackendOnly",
                                     "-DataDir", directory, "-StopAfterSeconds", "1")
            match = re.search(r"QORGAU_LAUNCH_READY backend port=(\d+)", result.stdout)
            self.assertIsNotNone(match, sanitized(result.stdout))
            self.assertFalse(listening(int(match[1])))
            self.assertNotIn("000000", result.stdout + result.stderr)

    def test_09_whitespace_label_is_rejected(self):
        self.run_script("Start-Student.ps1", "-Server", "127.0.0.1:8765", "-JoinCode", "000000",
                        "-Label", "   ", "-CheckOnly", success=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
