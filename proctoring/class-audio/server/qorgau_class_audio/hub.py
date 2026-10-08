"""AudioHub — authorized signaling for teacher↔student audio (qorgau.class.audio.v1).

The hub never touches media: it authorizes, keeps the session state machine and relays WebRTC signaling between
exactly two endpoints of the active session. It is transport-agnostic so the class server (C1) can mount it:

    hub = AudioHub()
    hub.teacher_online(teacher_id, link)            # after the teacher WS is authenticated (loopback + cookie)
    await hub.on_teacher_message(link, msg)         # every audio_* message from /ws/teacher
    hub.student_online(student_id, link)            # after `welcome`
    if hub.owns_command(msg["command_id"]): await hub.on_student_ack(student_id, msg)   # v1 `ack`
    await hub.on_student_message(student_id, msg)   # audio_signal / audio_media from /ws/student
    await hub.teacher_offline(link) / teacher_auth_lost(teacher_id) / student_offline(student_id, link)

A Link is any object with `async send(dict)`; sending to a closed link must raise (treated as a disconnect).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from .protocol import (
    ERROR_RU,
    SESSION_ID_RE,
    BadMessage,
    clean_signal,
    direction,
    envelope,
    new_command_id,
    new_session_id,
    req_bool,
    req_id,
    utc_now,
)

log = logging.getLogger("qorgau.class.audio")

ACTIVE = {"requested", "accepted", "connected"}
TERMINAL = {"ended", "rejected"}


class Link(Protocol):
    async def send(self, msg: dict[str, Any]) -> None: ...


@dataclass
class AudioSession:
    id: str
    teacher_id: str
    teacher_link: Link
    student_id: str
    listen: bool
    talk: bool
    state: str = "requested"
    reason: str | None = None
    since: str = field(default_factory=utc_now)
    start_command_id: str = ""
    commands: dict[str, str] = field(default_factory=dict)  # command_id -> kind (audio_start/update/stop)
    student_media: dict[str, Any] | None = None
    teacher_media: dict[str, Any] | None = None
    timer: asyncio.TimerHandle | None = None
    created_mono: float = field(default_factory=time.monotonic)

    def public(self) -> dict[str, Any]:
        return {
            "audio_session_id": self.id,
            "student_id": self.student_id,
            "state": self.state,
            "listen": self.listen,
            "talk": self.talk,
            "reason": self.reason,
            "student_media": self.student_media,
            "since": self.since,
        }


class AudioHub:
    def __init__(
        self,
        *,
        accept_timeout_s: float = 15.0,
        connect_timeout_s: float = 20.0,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        self.accept_timeout_s = accept_timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.on_event = on_event  # audit hook: (event, fields) — ids/states only, never SDP or media
        self.sessions: dict[str, AudioSession] = {}
        self.by_teacher: dict[str, str] = {}  # teacher_id -> active session id
        self.by_student: dict[str, str] = {}  # student_id -> active session id
        self.command_owner: dict[str, str] = {}  # command_id -> session id
        self.teacher_links: dict[int, tuple[str, Link]] = {}  # id(link) -> (teacher_id, link)
        self.students: dict[str, Link] = {}  # student_id -> current link

    # ------------------------------------------------------------------ registry
    def teacher_online(self, teacher_id: str, link: Link) -> None:
        self.teacher_links[id(link)] = (teacher_id, link)

    async def teacher_offline(self, link: Link) -> None:
        entry = self.teacher_links.pop(id(link), None)
        if not entry:
            return
        for s in list(self.sessions.values()):
            if s.teacher_link is link and s.state in ACTIVE:
                await self._end(s, "teacher_disconnected", notify_teacher=False)

    async def teacher_auth_lost(self, teacher_id: str) -> None:
        """Logout / revoked cookie: every audio stream of this teacher is closed."""
        sid = self.by_teacher.get(teacher_id)
        if sid and sid in self.sessions:
            await self._end(self.sessions[sid], "teacher_auth_lost")

    def student_online(self, student_id: str, link: Link) -> None:
        self.students[student_id] = link

    async def student_offline(self, student_id: str, link: Link | None = None) -> None:
        if link is not None and self.students.get(student_id) is not link:
            return  # an older link of a student who already reconnected
        self.students.pop(student_id, None)
        sid = self.by_student.get(student_id)
        if sid and sid in self.sessions:
            await self._end(self.sessions[sid], "student_disconnected", notify_student=False)

    def owns_command(self, command_id: Any) -> bool:
        return isinstance(command_id, str) and command_id in self.command_owner

    def active_for_teacher(self, teacher_id: str) -> AudioSession | None:
        sid = self.by_teacher.get(teacher_id)
        return self.sessions.get(sid) if sid else None

    async def shutdown(self) -> None:
        for s in list(self.sessions.values()):
            if s.state in ACTIVE:
                await self._end(s, "server_shutdown")

    # ------------------------------------------------------------------ teacher side
    async def on_teacher_message(self, link: Link, msg: dict[str, Any]) -> None:
        entry = self.teacher_links.get(id(link))
        if entry is None:
            await self._safe_send(link, self._error("not_authorized"))
            return
        teacher_id = entry[0]
        t = msg.get("type")
        try:
            if t == "audio_request":
                await self._request(teacher_id, link, msg)
            elif t in ("audio_update", "audio_signal", "audio_media", "audio_stop"):
                s = self._teacher_session(teacher_id, link, msg)
                if s is None:
                    await self._safe_send(link, self._error("session_mismatch", msg.get("audio_session_id")))
                    return
                if t == "audio_update":
                    await self._update(s, msg)
                elif t == "audio_signal":
                    body = clean_signal(msg, {"offer", "ice"})
                    await self._to_student(s, envelope("audio_signal", audio_session_id=s.id, command_id=s.start_command_id, **body))
                elif t == "audio_media":
                    await self._teacher_media(s, msg)
                else:
                    await self._end(s, "teacher_stop")
        except BadMessage as e:
            await self._safe_send(link, self._error("bad_request", msg.get("audio_session_id"), detail=str(e)))

    def _teacher_session(self, teacher_id: str, link: Link, msg: dict[str, Any]) -> AudioSession | None:
        sid = msg.get("audio_session_id")
        s = self.sessions.get(sid) if isinstance(sid, str) and SESSION_ID_RE.match(sid) else None
        if s is None or s.teacher_id != teacher_id or s.teacher_link is not link or s.state not in ACTIVE:
            self._audit("dropped", {"from": "teacher", "type": msg.get("type"), "reason": "session_mismatch"})
            return None
        return s

    async def _request(self, teacher_id: str, link: Link, msg: dict[str, Any]) -> None:
        student_id = req_id(msg, "student_id")
        listen = req_bool(msg, "listen")
        talk = req_bool(msg, "talk")
        if not (listen or talk):
            raise BadMessage("listen or talk must be true")
        if teacher_id in self.by_teacher:
            await self._safe_send(link, self._error("teacher_busy", self.by_teacher[teacher_id]))
            return
        if student_id in self.by_student:
            await self._safe_send(link, self._error("student_busy"))
            return
        student_link = self.students.get(student_id)
        if student_link is None:
            await self._safe_send(link, self._error("student_offline"))
            return
        s = AudioSession(
            id=new_session_id(), teacher_id=teacher_id, teacher_link=link, student_id=student_id, listen=listen, talk=talk
        )
        self.sessions[s.id] = s
        self.by_teacher[teacher_id] = s.id
        self.by_student[student_id] = s.id
        s.start_command_id = self._command(s, "audio_start")
        await self._to_teacher(s, envelope("audio_state", **s.public()))
        self._audit("requested", {"audio_session_id": s.id, "student_id": student_id, "listen": listen, "talk": talk})
        sent = await self._to_student(
            s,
            envelope(
                "command",
                command_id=s.start_command_id,
                kind="audio_start",
                payload={"audio_session_id": s.id, "listen": listen, "talk": talk, "direction": direction(listen, talk)},
            ),
        )
        if sent:
            self._arm(s, self.accept_timeout_s, "accept_timeout")

    async def _update(self, s: AudioSession, msg: dict[str, Any]) -> None:
        listen = req_bool(msg, "listen")
        talk = req_bool(msg, "talk")
        if not (listen or talk):
            raise BadMessage("listen or talk must be true (use audio_stop to end)")
        s.listen, s.talk = listen, talk
        cid = self._command(s, "audio_update")
        await self._to_student(
            s,
            envelope(
                "command",
                command_id=cid,
                kind="audio_update",
                payload={"audio_session_id": s.id, "listen": listen, "talk": talk, "direction": direction(listen, talk)},
            ),
        )
        await self._to_teacher(s, envelope("audio_state", **s.public()))

    async def _teacher_media(self, s: AudioSession, msg: dict[str, Any]) -> None:
        conn = msg.get("connection")
        if conn not in ("new", "connecting", "connected", "disconnected", "failed", "closed"):
            raise BadMessage("connection malformed")
        s.teacher_media = {"connection": conn, "receiving": bool(msg.get("receiving")), "sending": bool(msg.get("sending"))}
        if conn == "connected" and s.state == "accepted":
            s.state = "connected"
            s.since = utc_now()
            self._disarm(s)
            await self._to_teacher(s, envelope("audio_state", **s.public()))
            self._audit("connected", {"audio_session_id": s.id})

    # ------------------------------------------------------------------ student side
    async def on_student_ack(self, student_id: str, msg: dict[str, Any]) -> None:
        cid = msg.get("command_id")
        sid = self.command_owner.get(cid) if isinstance(cid, str) else None
        s = self.sessions.get(sid) if sid else None
        if s is None or s.student_id != student_id:
            self._audit("dropped", {"from": "student", "type": "ack", "reason": "session_mismatch"})
            return
        kind = s.commands.get(cid)
        ok = msg.get("ok") is True
        if kind == "audio_start" and s.state == "requested":
            if ok:
                s.state = "accepted"
                s.since = utc_now()
                self._arm(s, self.connect_timeout_s, "connect_timeout")
                await self._to_teacher(s, envelope("audio_state", **s.public()))
                self._audit("accepted", {"audio_session_id": s.id})
            else:
                code = msg.get("error_code") if isinstance(msg.get("error_code"), str) else "declined"
                await self._end(s, str(code)[:64], state="rejected", notify_student=False)
        elif kind == "audio_update" and not ok and s.state in ACTIVE:
            code = msg.get("error_code") if isinstance(msg.get("error_code"), str) else "update_failed"
            await self._end(s, str(code)[:64])

    async def on_student_message(self, student_id: str, msg: dict[str, Any]) -> None:
        link = self.students.get(student_id)
        sid = msg.get("audio_session_id")
        s = self.sessions.get(sid) if isinstance(sid, str) else None
        if s is None or s.student_id != student_id or s.state not in ACTIVE:
            self._audit("dropped", {"from": "student", "type": msg.get("type"), "reason": "session_mismatch"})
            if link is not None:
                await self._safe_send(link, self._error("session_mismatch", sid if isinstance(sid, str) else None))
            return
        try:
            if msg.get("type") == "audio_signal":
                body = clean_signal(msg, {"answer", "ice"})
                await self._to_teacher(s, envelope("audio_signal", audio_session_id=s.id, **body))
            elif msg.get("type") == "audio_media":
                s.student_media = {
                    "mic_live": bool(msg.get("mic_live")),
                    "indicator_shown": bool(msg.get("indicator_shown")),
                    "teacher_audio_playing": bool(msg.get("teacher_audio_playing")),
                    "connection": str(msg.get("connection", ""))[:32],
                }
                await self._to_teacher(s, envelope("audio_state", **s.public()))
                if msg.get("stopped") is True:
                    await self._end(s, "student_stop", notify_student=False)
        except BadMessage as e:
            if link is not None:
                await self._safe_send(link, self._error("bad_request", s.id, detail=str(e)))

    # ------------------------------------------------------------------ internals
    def _command(self, s: AudioSession, kind: str) -> str:
        cid = new_command_id()
        s.commands[cid] = kind
        self.command_owner[cid] = s.id
        return cid

    async def _end(
        self,
        s: AudioSession,
        reason: str,
        *,
        state: str = "ended",
        notify_teacher: bool = True,
        notify_student: bool = True,
    ) -> None:
        if s.state in TERMINAL:
            return
        self._disarm(s)
        s.state = state
        s.reason = reason
        s.since = utc_now()
        if self.by_teacher.get(s.teacher_id) == s.id:
            del self.by_teacher[s.teacher_id]
        if self.by_student.get(s.student_id) == s.id:
            del self.by_student[s.student_id]
        self._audit(state, {"audio_session_id": s.id, "reason": reason})
        if notify_student and s.student_id in self.students:
            cid = self._command(s, "audio_stop")
            await self._to_student(
                s,
                envelope("command", command_id=cid, kind="audio_stop", payload={"audio_session_id": s.id, "reason": reason}),
                end_on_failure=False,
            )
        if notify_teacher:
            await self._safe_send(s.teacher_link, envelope("audio_state", **s.public()))
        # keep finished sessions only briefly (late acks are then recognised and ignored)
        asyncio.get_running_loop().call_later(60, self._forget, s.id)

    def _forget(self, sid: str) -> None:
        s = self.sessions.get(sid)
        if s and s.state in TERMINAL:
            self.sessions.pop(sid, None)
            for cid in list(s.commands):
                self.command_owner.pop(cid, None)

    def _arm(self, s: AudioSession, delay: float, reason: str) -> None:
        self._disarm(s)
        loop = asyncio.get_running_loop()
        s.timer = loop.call_later(delay, lambda: asyncio.ensure_future(self._end(s, reason)))

    @staticmethod
    def _disarm(s: AudioSession) -> None:
        if s.timer:
            s.timer.cancel()
            s.timer = None

    async def _to_student(self, s: AudioSession, msg: dict[str, Any], *, end_on_failure: bool = True) -> bool:
        link = self.students.get(s.student_id)
        if link is None:
            if end_on_failure:
                await self._end(s, "student_disconnected", notify_student=False)
            return False
        try:
            await link.send(msg)
            return True
        except Exception:  # closed socket
            if end_on_failure:
                await self.student_offline(s.student_id, link)
            return False

    async def _to_teacher(self, s: AudioSession, msg: dict[str, Any]) -> None:
        try:
            await s.teacher_link.send(msg)
        except Exception:
            await self.teacher_offline(s.teacher_link)

    @staticmethod
    async def _safe_send(link: Link, msg: dict[str, Any]) -> None:
        try:
            await link.send(msg)
        except Exception:
            pass

    @staticmethod
    def _error(code: str, sid: str | None = None, detail: str | None = None) -> dict[str, Any]:
        fields: dict[str, Any] = {"code": code, "message_ru": ERROR_RU.get(code, code)}
        if sid:
            fields["audio_session_id"] = sid
        if detail:
            fields["detail"] = detail[:200]
        return envelope("audio_error", **fields)

    def _audit(self, event: str, fields: dict[str, Any]) -> None:
        log.info("audio %s %s", event, fields)
        if self.on_event:
            r = self.on_event(event, fields)
            if asyncio.iscoroutine(r):
                asyncio.ensure_future(r)
