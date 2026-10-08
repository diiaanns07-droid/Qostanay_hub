"""WS /v1/stream and /v1/preview against the contract (real process, synthetic session)."""

from __future__ import annotations

import json
import time

from websockets.sync.client import connect

from qorgau_qa import contract
from qorgau_qa.backend import wait_for
from qorgau_qa.stream import StreamRecorder, parse_preview_frame


def test_stream_session_filter_isolates_sessions(backend, api):
    with StreamRecorder(backend.ws_url("/stream", "session_id=some-other-session"), headers=backend.auth) as other, StreamRecorder(backend.ws_url("/stream"), headers=backend.auth) as all_:
        sid = api.running_session()
        assert all_.wait(lambda ms: any(m["session_id"] == sid and m["message"]["type"] == "observation" for m in ms), 10)
        backend.http.post(f"/sessions/{sid}/finish")
        time.sleep(0.3)
        leaked = [m for m in other.snapshot() if m["session_id"] == sid]
    assert not leaked, f"{len(leaked)} messages of {sid} delivered to a client filtered on another session"
    assert other.snapshot()[0]["message"]["type"] == "hello"


def test_stream_filter_on_own_session_receives_lifecycle(backend, api):
    sid = api.create()["session_id"]
    with StreamRecorder(backend.ws_url("/stream", f"session_id={sid}"), headers=backend.auth) as rec:
        api.ready(sid)
        api.start(sid)
        backend.http.post(f"/sessions/{sid}/finish")
        got = rec.wait(lambda ms: [m for m in ms if m["message"]["type"] == "session_state" and m["message"]["session"]["state"] == "finished"], 5)
        msgs = rec.snapshot()
    assert got
    for raw in msgs:
        contract.validate("StreamEnvelope", raw)
        assert raw["session_id"] in (None, sid)


def test_stream_observations_carry_provenance(backend, api):
    with StreamRecorder(backend.ws_url("/stream"), headers=backend.auth) as rec:
        sid = api.running_session()
        rec.wait(lambda ms: len([m for m in ms if m["message"]["type"] == "observation"]) >= 40, 15)
        backend.http.post(f"/sessions/{sid}/finish")
        obs = [m["message"]["observation"] for m in rec.snapshot() if m["message"]["type"] == "observation"]
    assert len(obs) >= 40
    ids = [o["observation_id"] for o in obs]
    assert len(ids) == len(set(ids)), "observation_id must be unique per session"
    frame_obs = [o for o in obs if o["kind"] in ("phone", "attention")]
    for o in frame_obs:
        assert o["session_id"] == sid and o["frame_id"] is not None and o["source_mode"] == "synthetic"
        assert o["producer"]["module"] and o["producer"]["version"]
        assert o["wall_time"].endswith("Z") or "+" in o["wall_time"], "wall_time must be timezone-aware"
    for kind in ("phone", "attention"):
        ts = [o["t_session_ms"] for o in frame_obs if o["kind"] == kind]
        assert ts == sorted(ts), f"{kind} observations out of session-time order"
        fids = [o["frame_id"] for o in frame_obs if o["kind"] == kind]
        assert fids == sorted(fids) and len(set(fids)) == len(fids), f"{kind}: frame ids not strictly increasing"


def test_stream_survives_many_clients_connecting_and_dropping(backend, api):
    sid = api.running_session()
    for _ in range(20):
        with connect(backend.ws_url("/stream"), additional_headers=backend.auth, open_timeout=5) as ws:
            assert json.loads(ws.recv(timeout=5))["message"]["type"] == "hello"
    assert backend.http.get("/health").status_code == 200
    backend.http.post(f"/sessions/{sid}/finish")


def test_slow_stream_client_does_not_block_api(backend, api):
    """A client that never reads must not stall REST or other clients (bounded per-client queue)."""
    slow = connect(backend.ws_url("/stream"), additional_headers=backend.auth, open_timeout=5).__enter__()
    try:
        sid = api.running_session()
        t0 = time.monotonic()
        for _ in range(20):
            assert backend.http.get("/health").status_code == 200
        assert time.monotonic() - t0 < 5.0
        with StreamRecorder(backend.ws_url("/stream"), headers=backend.auth) as fast:
            assert fast.wait(lambda ms: any(m["message"]["type"] == "observation" for m in ms), 5)
        backend.http.post(f"/sessions/{sid}/finish")
    finally:
        slow.close()


def test_preview_ws_binary_framing(backend, api):
    sid = api.running_session()
    frames = []
    with connect(backend.ws_url("/preview", f"session_id={sid}"), additional_headers=backend.auth, open_timeout=5) as ws:
        deadline = time.monotonic() + 5
        while len(frames) < 5 and time.monotonic() < deadline:
            frames.append(ws.recv(timeout=5))
    backend.http.post(f"/sessions/{sid}/finish")
    assert len(frames) >= 5
    last = -1
    for data in frames:
        assert isinstance(data, bytes), "preview frames must be binary"
        meta, jpeg = parse_preview_frame(data)
        m = contract.validate("PreviewFrameMeta", meta)
        assert m.session_id == sid and m.mirrored is False
        assert m.frame_id > last, "preview frame ids must increase (no duplicates)"
        last = m.frame_id
        assert jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9", "payload is not a complete JPEG"
        assert len(jpeg) == m.byte_length, "byte_length must equal the JPEG payload size"
        assert 0 < m.width <= 1920 and 0 < m.height <= 1080


def test_preview_ws_for_other_session_sends_nothing(backend, api):
    sid = api.running_session()
    with connect(backend.ws_url("/preview", "session_id=not-this-one"), additional_headers=backend.auth, open_timeout=5) as ws:
        try:
            data = ws.recv(timeout=1.5)
        except TimeoutError:
            data = None
    backend.http.post(f"/sessions/{sid}/finish")
    assert data is None, "preview of another session leaked"


def test_preview_rest_meta_matches_image(backend, api):
    sid = api.running_session()
    assert wait_for(lambda: backend.http.get(f"/sessions/{sid}/preview.jpg").status_code == 200, 5)
    r = backend.http.get(f"/sessions/{sid}/preview.jpg")
    meta = contract.validate("PreviewFrameMeta", json.loads(r.headers["X-Qorgau-Preview-Meta"]))
    assert r.headers["content-type"] == "image/jpeg"
    assert meta.session_id == sid and meta.byte_length == len(r.content)
    try:
        import cv2
        import numpy as np
    except Exception:
        return
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and (img.shape[1], img.shape[0]) == (meta.width, meta.height)
    backend.http.post(f"/sessions/{sid}/finish")
