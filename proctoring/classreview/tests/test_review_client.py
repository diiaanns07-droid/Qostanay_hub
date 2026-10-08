"""The reference student uploader (examples/upload_clip.py) against real HTTP servers."""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import incident

from classreview.examples.upload_clip import UploadFailed, upload_clip


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(store):
    uvicorn = pytest.importorskip("uvicorn")
    from fastapi import FastAPI

    from classreview import create_review_router

    app = FastAPI()
    app.include_router(create_review_router(store, teacher_guard=lambda r: "t", student_resolver={"tok-a": "stu-a"}.get))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", store
    server.should_exit = True
    t.join(5)


def test_upload_is_idempotent_over_real_http(live_server, clip):
    base, store = live_server
    store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    store.note_clip_requested("stu-a", "inc-1", "cmd-1")
    sleeps: list[float] = []
    first = upload_clip(base, "tok-a", "inc-1", clip, source="test", sleep=sleeps.append)
    again = upload_clip(base, "tok-a", "inc-1", clip, source="test", sleep=sleeps.append)  # "lost response" retry
    assert first["status"] == "stored" and again["status"] == "duplicate" and first["sha256"] == again["sha256"]
    assert sleeps == []
    for token, iid, code in (("bad", "inc-1", 401), ("tok-a", "inc-404", 404)):
        with pytest.raises(UploadFailed) as exc:
            upload_clip(base, token, iid, clip, sleep=sleeps.append)
        assert exc.value.status == code
    assert sleeps == []  # 4xx are final: no retries
    store.ingest_incident("stu-a", incident(2, incident_id="inc-2"), class_session_id="cls-1")
    store.note_clip_requested("stu-a", "inc-2", "cmd-2")
    with pytest.raises(UploadFailed) as exc:
        upload_clip(base, "tok-a", "inc-2", b"not a video", sleep=sleeps.append)
    assert exc.value.status == 422 and exc.value.code == "not_mp4"


def test_retries_5xx_and_network_errors_with_backoff():
    calls: list[bytes] = []

    class Flaky(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            calls.append(self.rfile.read(int(self.headers["Content-Length"])))
            if len(calls) < 3:
                self.send_response(503)
                self.end_headers()
                return
            body = json.dumps({"status": "stored", "sha256": "x"}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Flaky)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sleeps: list[float] = []
    try:
        r = upload_clip(f"http://127.0.0.1:{srv.server_port}", "tok", "inc-1", b"same-bytes", sleep=sleeps.append)
    finally:
        srv.shutdown()
    assert r["status"] == "stored" and sleeps == [1.0, 2.0] and calls == [b"same-bytes"] * 3
    sleeps.clear()
    with pytest.raises(UploadFailed) as exc:
        upload_clip(f"http://127.0.0.1:{_free_port()}", "tok", "inc-1", b"x", attempts=3, timeout_s=1, sleep=sleeps.append)
    assert exc.value.code == "network" and sleeps == [1.0, 2.0]


def test_client_side_limits():
    with pytest.raises(ValueError):
        upload_clip("http://127.0.0.1:1", "t", "inc-1", b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        upload_clip("http://127.0.0.1:1", "t", "inc-1", b"x", media_type="video/webm")
