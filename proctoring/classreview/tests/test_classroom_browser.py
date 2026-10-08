"""Production C1 panel playback and decisions, using a browser and a synthetic WS student.

Requires Node/Playwright (+ NODE_PATH if not local) and Chromium, or ADAL_REVIEW_CHROME.
No fake media device flags, camera access, microphone access or native lockdown.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from classroom.server.config import ServerConfig
from test_classroom_feature import assert_mounted, await_incident, history, join, running, upload


def browser_env():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for real C1 browser test")
    env = dict(os.environ)
    check = subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True, timeout=15)
    if check.returncode:
        pytest.skip("Playwright unavailable; set NODE_PATH to its node_modules")
    return node, env


def browser_phase(node, env, phase, server, sid, out):
    result = subprocess.run([node, str(Path(__file__).with_name("c1_browser.cjs")), phase,
                             server.base, server.pin, sid, str(out)], env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=100)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_real_panel_requests_plays_seeks_and_restores_clip(tmp_path, clip):
    node, env = browser_env()
    data = tmp_path / "production-browser"
    config = {"QORGAU_CLASS_FEATURES": ServerConfig().features, "QORGAU_CLASS_UI": "class-panel"}
    iid = "test-browser-clip"
    with running(data, **config) as server, closing(server.teacher()) as teacher:
        assert_mounted(teacher)
        student, welcome, _ = join(server, teacher)
        sid = welcome["student_id"]
        try:
            student.incident(1, iid, source_mode="synthetic", source_session_id="test-browser-source",
                             clip_available=True, explanation_ru="TEST: synthetic browser fixture")
            await_incident(teacher, sid, iid)

            def respond_to_clip_request():
                command = student.wait_for(lambda m: m["type"] == "command" and m["kind"] == "request_clip", timeout=40)
                response = upload(server, welcome["resume_token"], iid, clip, source="test")
                assert response.status_code == 201, response.text
                student.send("ack", command_id=command["command_id"], ok=True)

            with ThreadPoolExecutor(max_workers=1) as worker:
                uploaded = worker.submit(respond_to_clip_request)
                report = browser_phase(node, env, "initial", server, sid, tmp_path)
                uploaded.result(timeout=5)
            assert report["frames"][2] > report["frames"][0]
            before = history(teacher, sid)[iid]
            assert before["decision"] == "dismissed" and len(before["decision_history"]) == 2
            assert_mounted(teacher)
        finally:
            student.silent = True
            student.close()
    with running(data, **config) as server, closing(server.teacher()) as teacher:
        assert_mounted(teacher)
        report = browser_phase(node, env, "restart", server, sid, tmp_path)
        assert report["frames"][2] > report["frames"][0]
        after = history(teacher, sid)[iid]
        assert after["decision_history"] == before["decision_history"]
        assert after["event_provenance"] == before["event_provenance"]
