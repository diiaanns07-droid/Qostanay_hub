"""Ephemeral C2 ⇄ trusted Electron audio relay. Never stores SDP or opens a microphone."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel

AUDIO_PROTOCOL = "qorgau.class.audio.v1"


class ClassAudioMessage(BaseModel):
    type: Literal["class_audio"] = "class_audio"
    bridge_scope: str
    message: dict[str, Any]


class AudioBridge:
    def __init__(self, uplink):
        self.uplink = uplink
        self.scope = str(uuid.uuid4())
        self.ready_at = 0.0
        self.ws = None
        self.session_id: str | None = None
        self.source_session_id: str | None = None
        self.pending: set[str] = set()
        self.deadline = 0.0

    def publish(self, message):
        if self.uplink._publish:
            self.uplink._publish(ClassAudioMessage(bridge_scope=self.scope, message=message))

    async def send(self, message):
        if self.ws is None or self.uplink.connection != "connected":
            raise ValueError("classroom connection unavailable")
        from .client import envelope
        fields = {k: v for k, v in message.items() if k not in ("v", "msg_id", "sent_at", "audio_protocol", "type")}
        await self.uplink._send(self.ws, envelope(message["type"], audio_protocol=AUDIO_PROTOCOL, **fields))

    async def receive(self, ws, msg: dict[str, Any]) -> bool:
        if msg.get("audio_protocol") != AUDIO_PROTOCOL:
            return False  # legacy commands retain the explicit unsupported response
        kind = msg.get("type")
        if kind not in ("audio_signal", "audio_error", "command"):
            return False
        self.ws = ws
        if kind == "command":
            cmd = msg.get("kind")
            if cmd not in ("audio_start", "audio_update", "audio_stop"):
                return False
            payload = msg.get("payload") or {}
            sid, cid = payload.get("audio_session_id"), msg.get("command_id")
            if not isinstance(cid, str) or not isinstance(sid, str) or not sid.startswith("as-"):
                return True
            if cmd != "audio_stop" and (time.monotonic() - self.ready_at > 6 or self.uplink._snap.exam_state == "finished"):
                await self.send({"type": "ack", "command_id": cid, "ok": False, "error_code": "not_supported",
                                 "error_ru": "Приложение студента не готово к аудиосвязи"})
                return True
            if cmd == "audio_start":
                if self.session_id not in (None, sid):
                    await self.send({"type": "ack", "command_id": cid, "ok": False, "error_code": "busy"})
                    return True
                self.session_id = sid
                self.source_session_id = self.uplink._snap.session_id
            elif sid != self.session_id:
                return True
            self.pending.add(cid)
            self.deadline = time.monotonic() + 15
        elif kind == "audio_signal" and msg.get("audio_session_id") != self.session_id:
            return True
        self.publish(msg)
        if kind == "command" and msg.get("kind") == "audio_stop":
            self.session_id = None
            self.uplink.mic_active = False
            self.uplink.audio_direction = None
            self.uplink._publish_state()
        return True

    async def renderer(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict) or len(json.dumps(body)) > 80_000:
            raise ValueError("invalid audio body")
        msg = body.get("message")
        if not isinstance(msg, dict):
            raise ValueError("missing audio message")
        kind = msg.get("type")
        if kind == "ready":
            self.ready_at = time.monotonic()
            return {"bridge_scope": self.scope, "protocol": AUDIO_PROTOCOL}
        if body.get("bridge_scope") != self.scope:
            raise ValueError("stale audio bridge")
        if kind == "dispose":
            self.ready_at = 0
            await self.end("renderer_closed")
            return {"accepted": True}
        if kind == "ack":
            cid = msg.get("command_id")
            if cid not in self.pending or not isinstance(msg.get("ok"), bool):
                raise ValueError("unknown audio command")
            self.pending.remove(cid)
            clean = {"type": "ack", "command_id": cid, "ok": msg["ok"]}
            if not msg["ok"]:
                clean.update(error_code=str(msg.get("error_code", "failed"))[:64], error_ru=str(msg.get("error_ru", ""))[:200])
                self.session_id = None
                self.uplink.mic_active = False
            await self.send(clean)
            return {"accepted": True}
        if not self.session_id or msg.get("audio_session_id") != self.session_id:
            raise ValueError("audio session mismatch")
        if kind == "audio_signal":
            sig_kind = msg.get("kind")
            clean = {"type": kind, "audio_session_id": self.session_id, "kind": sig_kind}
            if sig_kind == "answer":
                sdp = msg.get("sdp")
                if not isinstance(sdp, str) or not sdp.startswith("v=0") or len(sdp) > 65536:
                    raise ValueError("invalid SDP")
                clean["sdp"] = sdp
            elif sig_kind == "ice":
                ice = msg.get("ice")
                if ice is not None and (not isinstance(ice, dict) or len(json.dumps(ice)) > 4096):
                    raise ValueError("invalid ICE")
                clean["ice"] = ice
            else:
                raise ValueError("invalid audio signal kind")
        elif kind == "audio_media":
            for key in ("mic_live", "indicator_shown", "teacher_audio_playing"):
                if not isinstance(msg.get(key), bool):
                    raise ValueError("invalid media status")
            if msg["mic_live"] and not msg["indicator_shown"]:
                await self.end("indicator_missing")
                raise ValueError("microphone requires visible indicator")
            connection = msg.get("connection")
            if connection not in ("new", "connecting", "connected", "disconnected", "failed", "closed"):
                raise ValueError("invalid connection state")
            clean = {"type": kind, "audio_session_id": self.session_id, "connection": connection,
                     **{key: msg[key] for key in ("mic_live", "indicator_shown", "teacher_audio_playing")}}
            clean["stopped"] = msg.get("stopped") is True
            self.uplink.mic_active = msg["mic_live"]
            self.uplink._publish_state()
        else:
            raise ValueError("unsupported audio message")
        await self.send(clean)
        if kind == "audio_media" and clean.get("stopped"):
            self.session_id = None
            self.pending.clear()
        return {"accepted": True}

    async def end(self, reason):
        sid = self.session_id
        self.session_id = None
        self.pending.clear()
        self.uplink.mic_active = False
        self.uplink.audio_direction = None
        self.publish({"type": "transport_lost", "reason": reason})
        self.uplink._publish_state()
        if sid and self.ws is not None and self.uplink.connection == "connected":
            try:
                await self.send({"type": "audio_media", "audio_session_id": sid, "mic_live": False,
                                 "indicator_shown": False, "teacher_audio_playing": False, "connection": "closed", "stopped": True})
            except Exception:
                pass

    def observe(self, snapshot):
        if self.session_id and (snapshot.exam_state == "finished" or snapshot.session_id != self.source_session_id
                                or time.monotonic() - self.ready_at > 6):
            asyncio.create_task(self.end("session_or_renderer_ended"))

    def disconnected(self):
        self.ws = None
        self.session_id = None
        self.pending.clear()
        self.uplink.mic_active = False
        self.uplink.audio_direction = None
        self.publish({"type": "transport_lost", "reason": "classroom_disconnected"})
        self.scope = str(uuid.uuid4())
        self.ready_at = 0


def install_audio_routes(router, get_uplink):
    """Mount on the backend's existing authenticated /v1 router; token stays in Electron main."""
    from fastapi import HTTPException

    @router.post("/class/audio")
    async def audio_message(body: dict[str, Any]):
        uplink = get_uplink()
        if uplink is None or uplink._loop is None:
            raise HTTPException(409, "classroom uplink unavailable")
        try:
            future = asyncio.run_coroutine_threadsafe(uplink.audio.renderer(body), uplink._loop)
            return await asyncio.wait_for(asyncio.wrap_future(future), 5)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (TimeoutError, RuntimeError) as exc:
            raise HTTPException(409, "audio bridge unavailable") from exc
