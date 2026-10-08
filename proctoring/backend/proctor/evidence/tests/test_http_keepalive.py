"""QA-BUG-002: use a real Windows TCP connection, not just an ASGI TestClient."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading


def test_bad_a08_path_ids_preserve_http_connection(tmp_path):
    token = secrets.token_hex(32)
    root = Path(__file__).resolve().parents[4]
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "QORGAU_DATA_DIR": str(tmp_path / "data"),
        "QORGAU_EXAM_PATH": str(root / "contracts/fixtures/v1/ExamDefinition.demo_min.json"),
        "QORGAU_LOG_LEVEL": "WARNING",
    }
    connection = None
    with (tmp_path / "backend.stderr.txt").open("w", encoding="utf-8") as errors:
        process = subprocess.Popen(
            [sys.executable, "-m", "proctor", "serve", "--token-stdin", "--port", "0"],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
            text=True, encoding="utf-8",
        )
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
        try:
            process.stdin.write(token + "\n")
            process.stdin.flush()
            line = ready.get(timeout=25)
            assert line.startswith("QORGAU_READY "), line
            port = json.loads(line.split(" ", 1)[1])["port"]
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

            def request(method, path, body=None):
                connection.request(method, path, json.dumps(body) if body is not None else None, headers)
                response = connection.getresponse()
                payload = json.loads(response.read())
                return response.status, payload

            status, _ = request("GET", "/v1/health")
            assert status == 200
            original_socket = connection.sock
            assert original_socket is not None
            status, session = request("POST", "/v1/sessions", {
                "source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "retain_media": False,
                "consent": {"accepted": True, "text_version": "consent-ru-1",
                            "accepted_at": "2026-10-08T09:00:00Z"},
            })
            assert status == 201, session
            sid = session["session_id"]
            for bad in ("x" * 129, "x" * 1500, "bad%20id"):
                cases = [
                    ("GET", f"/v1/sessions/{bad}/summary", None),
                    ("GET", f"/v1/sessions/{sid}/incidents/{bad}", None),
                    ("GET", f"/v1/sessions/{sid}/evidence/{bad}", None),
                    ("PUT", f"/v1/sessions/{sid}/answers/{bad}", {"value": ["a"], "client_seq": 1}),
                    ("POST", f"/v1/sessions/{sid}/incidents/{bad}/reviews",
                     {"decision": "dismissed", "operator": "qa"}),
                ]
                for method, path, body in cases:
                    status, payload = request(method, path, body)
                    assert status == 422, (method, path, status, payload)
                    assert payload["error"]["code"] == "INVALID_ARGUMENT"
                    # HTTPConnection may reconnect automatically: socket identity catches that too.
                    assert connection.sock is original_socket
                    assert request("GET", "/v1/health")[0] == 200
                    assert connection.sock is original_socket
        finally:
            if connection is not None:
                connection.close()
            process.stdin.close()  # public parent/child shutdown protocol
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()  # this test's own child only
                process.wait(timeout=5)
            process.stdout.close()
