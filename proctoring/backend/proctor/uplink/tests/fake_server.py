"""Minimal qorgau.class.v1 server for C2 tests (NOT the real class server; T01 owns that).

FastAPI + uvicorn on 127.0.0.1:<free port>, same port for WS /ws/student and POST /api/student/clips/{id}
(as in the protocol). Records everything, drops duplicates by (student_id, seq), can send commands,
drop connections and be stopped/restarted on the same port while keeping its state (resume tokens).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

EXAM = {"exam_id": "demo-exam-1", "title": "Демо-экзамен", "mode": "url", "allowed_urls": ["https://exam.local/*"],
        "allowed_apps": [], "instructions_ru": "Тестовый сервер C2"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(type_: str, **fields: Any) -> dict[str, Any]:
    return {"type": type_, "v": 1, "msg_id": str(uuid.uuid4()), "sent_at": datetime.now(timezone.utc).isoformat(), **fields}


class FakeClassServer:
    def __init__(self, join_code: str = "123456", port: int | None = None):
        self.join_code = join_code
        self.port = port or free_port()
        self.lock = threading.Lock()
        self.tokens: dict[str, str] = {}  # resume_token -> student_id
        self.hellos: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []  # every message after hello (incl. duplicates)
        self.accepted: list[dict[str, Any]] = []  # queued types (incident/ack) after (student_id, seq) dedup
        self.duplicates = 0
        self.clips: dict[str, dict[str, Any]] = {}
        self._seen: set[tuple[str, int]] = set()
        self._sockets: list[tuple[WebSocket, str]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.app = self._make_app()

    # ------------------------------------------------------------------ app
    def _make_app(self) -> FastAPI:
        app = FastAPI()

        @app.websocket("/ws/student")
        async def ws_student(ws: WebSocket) -> None:
            await ws.accept()
            self._loop = asyncio.get_running_loop()
            try:
                hello = json.loads(await ws.receive_text())
            except WebSocketDisconnect:
                return
            with self.lock:
                self.hellos.append(hello)
            sid = None
            if hello.get("resume_token") in self.tokens:
                sid = self.tokens[hello["resume_token"]]
            elif hello.get("join_code") == self.join_code:
                sid = f"st-{len(self.tokens) + 1}"
            if hello.get("type") != "hello" or sid is None:
                await ws.send_text(json.dumps(_env("error", code="join_rejected", message_ru="Неверный код")))
                await ws.close()
                return
            token = secrets.token_hex(32)
            with self.lock:
                self.tokens[token] = sid
            await ws.send_text(json.dumps(_env("welcome", student_id=sid, resume_token=token, server_time=datetime.now(timezone.utc).isoformat(), exam=EXAM)))
            entry = (ws, sid)
            self._sockets.append(entry)
            try:
                while True:
                    msg = json.loads(await ws.receive_text())
                    with self.lock:
                        self.messages.append(msg)
                        if msg.get("type") in ("incident", "ack") and "seq" in msg:
                            key = (sid, int(msg["seq"]))
                            if key in self._seen:
                                self.duplicates += 1
                            else:
                                self._seen.add(key)
                                self.accepted.append(msg)
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                if entry in self._sockets:
                    self._sockets.remove(entry)

        @app.post("/api/student/clips/{incident_id}")
        async def clip(incident_id: str, request: Request) -> JSONResponse:
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if token not in self.tokens:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            body = await request.body()
            if len(body) > 8 * 1024 * 1024:
                return JSONResponse({"error": "too_large"}, status_code=413)
            with self.lock:
                self.clips[incident_id] = {"bytes": len(body), "content_type": request.headers.get("content-type"), "student_id": self.tokens[token]}
            return JSONResponse({"ok": True})

        return app

    # ------------------------------------------------------------------ control
    def start(self) -> "FakeClassServer":
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning", ws="websockets-sansio", lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("fake server did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._server.force_exit = True
        if self._thread is not None:
            self._thread.join(10)
        self._sockets.clear()

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def connected(self) -> int:
        return len(self._sockets)

    def _run(self, coro: Any) -> Any:
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(5)

    def send_command(self, kind: str, payload: dict[str, Any] | None = None, command_id: str | None = None) -> str:
        cid = command_id or str(uuid.uuid4())
        msg = json.dumps(_env("command", command_id=cid, kind=kind, payload=payload or {}))
        ws = self._sockets[-1][0]
        self._run(ws.send_text(msg))
        return cid

    def send_ping(self) -> None:
        ws = self._sockets[-1][0]
        self._run(ws.send_text(json.dumps(_env("ping"))))

    def drop_connections(self) -> None:
        for ws, _ in list(self._sockets):
            try:
                self._run(ws.close(code=1012))
            except Exception:
                pass

    def of_type(self, type_: str) -> list[dict[str, Any]]:
        with self.lock:
            return [m for m in self.messages if m.get("type") == type_]

    def all_acks(self, command_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [m for m in self.messages if m.get("type") == "ack" and m.get("command_id") == command_id]

    def acks(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {m["command_id"]: m for m in self.accepted if m.get("type") == "ack"}
