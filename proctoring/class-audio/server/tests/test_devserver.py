"""Dev signaling server over a REAL uvicorn socket: auth boundaries and fail-closed behaviour."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
import websockets

from qorgau_class_audio.devserver import create_app

PIN = "424242"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _lan_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip


@pytest.fixture()
def server():
    app = create_app(PIN)
    port = _free_port()
    cfg = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield app, port
    srv.should_exit = True
    th.join(timeout=5)


def login(port: int) -> httpx.Client:
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Origin": f"http://127.0.0.1:{port}"})
    r = c.post("/api/teacher/login", json={"pin": PIN})
    assert r.status_code == 200
    return c


async def recv_type(ws, type_, timeout=3.0):
    end = time.monotonic() + timeout
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.01, end - time.monotonic())))
        if msg["type"] == type_:
            return msg


def test_teacher_endpoints_are_loopback_only(server):
    _, port = server
    ip = _lan_ip()
    if ip is None:
        pytest.skip("no non-loopback interface in this environment")
    r = httpx.get(f"http://{ip}:{port}/")
    assert r.status_code == 403
    r = httpx.post(f"http://{ip}:{port}/api/teacher/login", json={"pin": PIN}, headers={"Origin": f"http://{ip}:{port}"})
    assert r.status_code == 403
    # the test peer is reachable from the LAN side
    assert httpx.get(f"http://{ip}:{port}/test-peer/").status_code == 200


def test_pin_wrong_and_rate_limited(server):
    _, port = server
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Origin": f"http://127.0.0.1:{port}"})
    codes = [c.post("/api/teacher/login", json={"pin": "000000"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert c.post("/api/teacher/login", json={"pin": PIN}).status_code == 429  # even the right PIN waits


def test_cross_origin_login_and_ws_rejected(server):
    _, port = server
    r = httpx.post(f"http://127.0.0.1:{port}/api/teacher/login", json={"pin": PIN}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    c = login(port)
    cookie = c.cookies.get("qorgau_teacher")

    async def go():
        with pytest.raises(websockets.exceptions.InvalidStatus):
            async with websockets.connect(
                f"ws://127.0.0.1:{port}/ws/teacher", origin="http://evil.example", additional_headers={"Cookie": f"qorgau_teacher={cookie}"}
            ) as ws:
                await ws.recv()

    try:
        asyncio.run(go())
    except AssertionError:
        raise
    except Exception:
        pass  # closed with 4403 after accept-less handshake is also a rejection


def test_static_headers_csp_and_permissions(server):
    _, port = server
    r = httpx.get(f"http://127.0.0.1:{port}/test-peer/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["permissions-policy"] == "microphone=(self), camera=()"
    assert httpx.get(f"http://127.0.0.1:{port}/test-peer/../devserver.py").status_code == 404


def test_full_signaling_and_logout_closes_audio(server):
    app, port = server
    c = login(port)
    cookie = c.cookies.get("qorgau_teacher")
    join = c.get("/api/teacher/session").json()["join_code"]

    async def go():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/student") as st:
            await st.send(json.dumps({"type": "hello", "v": 1, "protocol": "qorgau.class.v1", "join_code": join, "computer_name": "PC-1", "student_label": "A"}))
            welcome = await recv_type(st, "welcome")
            sid = welcome["student_id"]
            async with websockets.connect(
                f"ws://127.0.0.1:{port}/ws/teacher", origin=f"http://127.0.0.1:{port}", additional_headers={"Cookie": f"qorgau_teacher={cookie}"}
            ) as te:
                hello = await recv_type(te, "teacher_hello")
                assert any(s["student_id"] == sid for s in hello["students"])
                await te.send(json.dumps({"type": "audio_request", "student_id": sid, "listen": True, "talk": False}))
                assert (await recv_type(te, "audio_state"))["state"] == "requested"
                cmd = await recv_type(st, "command")
                assert cmd["kind"] == "audio_start"
                await st.send(json.dumps({"type": "ack", "command_id": cmd["command_id"], "ok": True}))
                assert (await recv_type(te, "audio_state"))["state"] == "accepted"
                # logout from another request → audio ends, student told, teacher socket closed 4401
                c.post("/api/teacher/logout")
                stop = await recv_type(st, "command")
                assert stop["kind"] == "audio_stop" and stop["payload"]["reason"] == "teacher_auth_lost"
                with pytest.raises(websockets.exceptions.ConnectionClosed) as ei:
                    while True:
                        await te.recv()
                assert ei.value.rcvd is not None and ei.value.rcvd.code == 4401
            # resume with the token → same student id
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/student") as st2:
            await st2.send(json.dumps({"type": "hello", "v": 1, "protocol": "qorgau.class.v1", "resume_token": welcome["resume_token"]}))
            assert (await recv_type(st2, "welcome"))["student_id"] == sid

    asyncio.run(go())


def test_wrong_join_code_rejected(server):
    _, port = server

    async def go():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/student") as st:
            await st.send(json.dumps({"type": "hello", "v": 1, "protocol": "qorgau.class.v1", "join_code": "000000"}))
            err = await recv_type(st, "error")
            assert err["code"] == "join_rejected"

    asyncio.run(go())


def test_teacher_ws_without_cookie_rejected(server):
    _, port = server

    async def go():
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws/teacher", origin=f"http://127.0.0.1:{port}") as te:
                await te.recv()
        except websockets.exceptions.InvalidStatus as e:
            return e.response.status_code
        except websockets.exceptions.ConnectionClosed as e:
            return e.rcvd.code if e.rcvd else None

    assert asyncio.run(go()) in (403, 4401)
