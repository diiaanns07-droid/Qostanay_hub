"""Explicit C1 adapter for the negotiated T05 audio extension; no media on the server."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

# The existing T05 package is deliberately kept in its owned source directory.
ROOT = Path(__file__).resolve().parents[2] / "class-audio"
if "qorgau_class_audio" not in sys.modules:
    spec = importlib.util.spec_from_file_location("qorgau_class_audio", ROOT / "server/qorgau_class_audio/__init__.py",
                                               submodule_search_locations=[str(ROOT / "server/qorgau_class_audio")])
    assert spec and spec.loader
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)

from qorgau_class_audio.hub import AudioHub
from qorgau_class_audio.protocol import AUDIO_PROTOCOL, BadMessage, clean_signal, req_id


def validate_server_audio(message: dict[str, Any]) -> bool:
    """Only this negotiated extension can bypass the legacy audio schema."""
    if message.get("audio_protocol") != AUDIO_PROTOCOL:
        return False
    if len(json.dumps(message)) > 80_000:
        raise ValueError("audio message too large")
    kind = message.get("type")
    if kind == "audio_signal":
        req_id(message, "audio_session_id")
        req_id(message, "command_id")
        clean_signal(message, {"offer", "ice"})
    elif kind == "command" and message.get("kind") in ("audio_start", "audio_update", "audio_stop"):
        req_id(message, "command_id")
        req_id(message.get("payload", {}), "audio_session_id")
    elif kind != "audio_error":
        raise ValueError("invalid audio extension message")
    return True


class StudentLink:
    def __init__(self, ctx: Any, student_id: str):
        self.ctx, self.student_id = ctx, student_id

    async def send(self, message: dict[str, Any]) -> None:
        if not self.ctx.send_raw_to_student(self.student_id, {**message, "audio_protocol": AUDIO_PROTOCOL}):
            raise ConnectionError("student offline")


class TeacherLink:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.lock = asyncio.Lock()

    async def send(self, message: dict[str, Any]) -> None:
        async with self.lock:
            await self.ws.send_json(message)


class ClassroomAudioFeature:
    name, owner = "audio", "T05"

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.hub = AudioHub()
        self.router = APIRouter()
        self.tasks: set[asyncio.Task] = set()
        self.student_sessions: dict[str, str] = {}
        self.router.add_api_websocket_route("/api/teacher/audio/ws", self.teacher_socket)
        self.router.add_api_route("/api/teacher/audio/assets/{folder}/{name}", self.asset, methods=["GET"])

    async def asset(self, folder: str, name: str):
        allowed = {"teacher": {"teacher-audio.js", "teacher-signaling.js", "audio-panel.js", "audio.css"},
                   "shared": {"media-errors.js"}}
        if name not in allowed.get(folder, set()):
            from fastapi import HTTPException
            raise HTTPException(404)
        return FileResponse(ROOT / "web" / folder / name)

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def teacher_socket(self, ws: WebSocket):
        # C1 Gate verifies loopback/Host/Origin before this feature; cookie is rechecked even when idle.
        try:
            principal = self.ctx.teacher(ws)
        except Exception:
            await ws.close(4401)
            return
        await ws.accept()
        link = TeacherLink(ws)
        self.hub.teacher_online(principal.teacher_id, link)
        receive = asyncio.create_task(ws.receive_text())
        try:
            while True:
                self.ctx.teacher(ws)
                done, _ = await asyncio.wait({receive}, timeout=0.5)
                if not done:
                    continue
                text = receive.result()
                receive = asyncio.create_task(ws.receive_text())
                try:
                    if len(text.encode()) > 80_000:
                        raise ValueError("too large")
                    msg = json.loads(text)
                    if not isinstance(msg, dict) or msg.get("type") not in {"audio_request", "audio_update", "audio_stop", "audio_signal", "audio_media"}:
                        raise ValueError("unknown message")
                    if msg.get("type") == "audio_request":
                        student = self.ctx.student(msg.get("student_id"))
                        if student is None or student.session_id != self.ctx.current_session_id():
                            raise ValueError("inactive classroom session")
                    await self.hub.on_teacher_message(link, msg)
                except (ValueError, TypeError):
                    await link.send({"type": "audio_error", "code": "bad_request", "message_ru": "Некорректный запрос аудиосвязи"})
        except WebSocketDisconnect:
            pass
        except Exception:
            await self.hub.teacher_auth_lost(principal.teacher_id)
            try:
                await ws.close(4401)
            except Exception:
                pass
        finally:
            receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)
            await self.hub.teacher_offline(link)

    def on_student_connected(self, student, hello):
        if hello.get("audio_protocol") == AUDIO_PROTOCOL:
            # A replacement connection never inherits a live audio session.
            if student.student_id in self.hub.students:
                self.spawn(self.hub.student_offline(student.student_id, self.hub.students[student.student_id]))
            self.student_sessions[student.student_id] = student.session_id
            self.hub.student_online(student.student_id, StudentLink(self.ctx, student.student_id))

    async def on_student_extension(self, student_id: str, msg: dict[str, Any]) -> bool:
        if msg.get("audio_protocol") != AUDIO_PROTOCOL:
            return False
        if student_id not in self.hub.students or self.student_sessions.get(student_id) != self.ctx.current_session_id():
            return True
        kind = msg.get("type")
        if kind not in ("ack", "audio_signal", "audio_media"):
            return True
        if len(json.dumps(msg)) > 80_000:
            return True
        if kind == "ack":
            if self.hub.owns_command(msg.get("command_id")) and isinstance(msg.get("ok"), bool):
                await self.hub.on_student_ack(student_id, msg)
        else:
            if kind == "audio_media" and any(not isinstance(msg.get(k), bool) for k in ("mic_live", "indicator_shown", "teacher_audio_playing")):
                return True
            await self.hub.on_student_message(student_id, msg)
        return True

    def on_student_disconnected(self, student_id):
        link = self.hub.students.get(student_id)
        self.spawn(self.hub.student_offline(student_id, link))

    def tick(self):
        for sid, session in list(self.student_sessions.items()):
            if session != self.ctx.current_session_id():
                self.spawn(self.hub.student_offline(sid, self.hub.students.get(sid)))
                self.student_sessions.pop(sid, None)

    async def async_close(self):
        await self.hub.shutdown()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


def create_classroom_feature(ctx):
    return ClassroomAudioFeature(ctx)
