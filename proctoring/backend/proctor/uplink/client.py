"""Student uplink client for qorgau.class.v1 (owner: C2).

Runs in its own daemon thread with its own asyncio loop, so the network can never block or crash the
local exam backend. Lifecycle per connection: ``hello`` → ``welcome`` (resume_token persisted, never
logged) → periodic ``status`` (2 s + on change), ``incident`` (open/close, queued with ``seq``),
``preview`` (320×240 ≤ 30 KB every 2 s while running), ``pong`` on ``ping``, ``ack`` for every command.
Reconnect with backoff 1 → 2 → 4 → 8 → 15 s using the resume_token; a wrong join code stops the uplink
(state ``rejected``) and the exam continues locally.

Electron gets the class state through the existing WS ``/v1/stream`` as message ``class_state``
(``ClassStateMsg`` below, see handoffs/C2/STATUS.md).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .backend_view import Snapshot
from .config import PROTOCOL, UplinkConfig
from .outbox import Outbox

log = logging.getLogger("proctor.uplink")

APP_VERSION = "qorgau-exam-uplink-0.1.0"
MAX_MESSAGE = 256 * 1024
CLIP_WAIT_ON_REQUEST_S = 15.0
CLASS_STATE_REPUBLISH_S = 5.0  # class_state is re-published so a renderer that subscribed late still learns it
AUDIO_NOT_SUPPORTED_RU = "Аудиосвязь в приложении студента ещё не подключена"
NO_CLIP_RULES = {"monitoring_degraded"}


class ClassStateMsg(BaseModel):
    """Stream message for Electron (WS /v1/stream, envelope.message). Additive, type = "class_state"."""

    type: Literal["class_state"] = "class_state"
    connection: Literal["connecting", "connected", "reconnecting", "rejected", "stopped"]
    server: str
    computer_name: str | None = None
    student_label: str | None = None
    student_id: str | None = None
    locked: bool = False
    lock_reason_ru: str | None = None
    mic_active: bool = False  # stays False until WebRTC audio exists (audio_start is refused: not_supported)
    audio_direction: Literal["listen", "talk", "both"] | None = None
    exam: dict[str, Any] | None = None  # welcome.exam: exam_id, title, mode, allowed_urls, allowed_apps, instructions_ru
    last_command: dict[str, Any] | None = None  # {"command_id", "kind", "ok"} of the last executed command
    message_ru: str | None = Field(default=None, max_length=300)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def envelope(type_: str, **fields: Any) -> dict[str, Any]:
    return {"type": type_, "v": 1, "msg_id": str(uuid.uuid4()), "sent_at": _now(), **fields}


def http_post_file(url: str, path: Path, token: str, content_type: str, timeout_s: float = 30.0) -> tuple[bool, str]:
    data = path.read_bytes()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": content_type, "Content-Length": str(len(data)),
        "X-Qorgau-Clip-Source": "a02.export_clip",  # optional header understood by T03
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 (LAN class server, http by protocol)
            return 200 <= resp.status < 300, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, type(exc).__name__


class Uplink:
    def __init__(
        self,
        cfg: UplinkConfig,
        view: Any,  # BackendView-like: snapshot(), preview_jpeg(), export_clip(), start_exam(), finish_exam()
        publish: Callable[[BaseModel], None] | None = None,
        *,
        http_post: Callable[[str, Path, str, str], tuple[bool, str]] = http_post_file,
    ):
        self.cfg = cfg
        self.view = view
        self._publish = publish
        self._http_post = http_post
        self.outbox = Outbox(cfg.state_dir / "outbox.sqlite", cfg.outbox_max)
        if self.outbox.get_meta("server") != cfg.server:  # a token is only valid for its server
            self.outbox.set_meta("resume_token", None)
            self.outbox.set_meta("student_id", None)
            self.outbox.set_meta("server", cfg.server)
        self.connection = "connecting"
        self.locked = False
        self.lock_reason_ru: str | None = None
        self.mic_active = False
        self.audio_direction: str | None = None
        self.exam: dict[str, Any] | None = None
        self.last_command: dict[str, Any] | None = None
        self.connects = 0
        self.sent: list[dict[str, Any]] = []  # messages sent on the wire (tests/diagnostics, bounded)
        self._done_commands: dict[str, dict[str, Any] | None] = {}  # command_id -> ack fields (T04 re-delivers by command_id)
        self._incident_sent: dict[str, tuple[str, bool]] = {}  # incident_id -> (state, clip_available)
        self._clips: dict[str, Any] = {}  # incident_id -> Path | "pending" | "failed"
        self._clip_events: dict[str, threading.Event] = {}
        self._snap = Snapshot()
        self._outbox_signal: asyncio.Event | None = None
        self._last_status: dict[str, Any] | None = None
        self._last_status_t = 0.0
        self._last_preview_t = 0.0
        self._last_state_publish = 0.0
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._stopping = threading.Event()
        self._send_lock: asyncio.Lock | None = None
        self._flush_lock: asyncio.Lock | None = None

    # ================================================================== thread
    @property
    def student_id(self) -> str | None:
        return self.outbox.get_meta("student_id")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._thread_main, name="class-uplink", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stopping.set()
        loop, ev = self._loop, self._stop
        if loop is not None and ev is not None:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout_s)
        self._set_connection("stopped")
        self.outbox.close()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:  # never propagate into the backend
            log.exception("uplink thread failed")

    # ================================================================== connection loop
    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        if self._stopping.is_set():
            return
        self._send_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._outbox_signal = asyncio.Event()
        poller = asyncio.create_task(self._poller())  # incidents + clips also while offline
        try:
            await self._connection_loop()
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)

    async def _connection_loop(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import WebSocketException

        assert self._stop is not None
        attempt = 0
        use_code = False
        while not self._stop.is_set():
            self._set_connection("connecting" if self.connects == 0 and attempt == 0 else "reconnecting")
            result = "error"
            try:
                async with connect(self.cfg.ws_url, open_timeout=5, max_size=MAX_MESSAGE, ping_interval=None, close_timeout=2) as ws:
                    result = await self._handshake(ws, force_code=use_code)
                    if result == "ok":
                        attempt, use_code = 0, False
                        self.connects += 1
                        self._set_connection("connected")
                        await self._session(ws)
            except (OSError, asyncio.TimeoutError, WebSocketException) as exc:
                log.info("uplink: connection to %s failed/lost (%s)", self.cfg.server, type(exc).__name__)
            except Exception:
                log.exception("uplink: unexpected error")
            if self._stop.is_set():
                break
            if result == "rejected":
                self._set_connection("rejected", "Сервер класса отклонил код подключения. Экзамен продолжается локально.")
                return
            if result == "retry_with_code":  # resume token not accepted: join again with the code, no wait
                use_code = True
                continue
            delay = min(self.cfg.backoff_max_s, float(2 ** attempt))
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _handshake(self, ws: Any, force_code: bool = False) -> str:
        token = None if force_code else self.outbox.get_meta("resume_token")
        hello = envelope(
            "hello", protocol=PROTOCOL, computer_name=self.cfg.computer_name, student_label=self.cfg.student_label, app_version=APP_VERSION,
            source_mode=self._snap.source_mode, source_session_id=self._snap.session_id,
            **({"resume_token": token} if token else {"join_code": self.cfg.join_code}),
        )
        await ws.send(json.dumps(hello, ensure_ascii=False))
        self._remember({**hello, **({"resume_token": "***"} if token else {"join_code": "***"})})
        deadline = time.monotonic() + self.cfg.welcome_timeout_s
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
            msg = json.loads(raw)
            if msg.get("type") == "welcome":
                self.outbox.set_meta("resume_token", str(msg["resume_token"]))
                self.outbox.set_meta("student_id", str(msg["student_id"]))
                self.exam = msg.get("exam")
                return "ok"
            if msg.get("type") == "error":
                code = msg.get("code")
                log.warning("uplink: server error during hello: %s", code)
                # C1 distinguishes a stale resume token from an invalid join code.
                # Keep join_rejected compatibility with older class servers.
                if code in ("join_rejected", "resume_rejected"):
                    if token:
                        self.outbox.set_meta("resume_token", None)
                        return "retry_with_code"
                    return "rejected"
                return "error"
            if msg.get("type") == "ping":
                await ws.send(json.dumps(envelope("pong")))

    async def _session(self, ws: Any) -> None:
        self._last_status = None  # a fresh status right after (re)connect
        self._last_status_t = 0.0
        tasks = [asyncio.create_task(self._receiver(ws)), asyncio.create_task(self._ticker(ws)), asyncio.create_task(self._stop.wait())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if not t.cancelled() and t.exception() is not None:
                    raise t.exception()  # type: ignore[misc]
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # ================================================================== sending
    async def _send(self, ws: Any, msg: dict[str, Any]) -> None:
        assert self._send_lock is not None
        async with self._send_lock:
            await ws.send(json.dumps(msg, ensure_ascii=False))
        self._remember(msg)

    def _remember(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)
        if len(self.sent) > 2000:
            del self.sent[:1000]

    async def _flush_outbox(self, ws: Any) -> None:
        assert self._flush_lock is not None
        async with self._flush_lock:  # ticker and command tasks never send the same seq twice
            while True:
                batch = self.outbox.pending(50)
                if not batch:
                    return
                for msg in batch:
                    await self._send(ws, msg)
                    self.outbox.done(int(msg["seq"]))

    def _queue(self, type_: str, **fields: Any) -> int:
        return self.outbox.put(envelope(type_, **fields))

    async def _poller(self) -> None:
        """Always running (online or not): snapshot, incident diff -> outbox, clip export at open."""
        while True:
            try:
                snap: Snapshot = await asyncio.to_thread(self.view.snapshot)
                self._snap = snap
                if self._diff_incidents(snap):
                    self._outbox_signal.set()
            except Exception:
                log.exception("uplink: snapshot failed")
            if time.monotonic() - self._last_state_publish >= CLASS_STATE_REPUBLISH_S:
                self._publish_state()
            await asyncio.sleep(self.cfg.poll_interval_s)

    async def _ticker(self, ws: Any) -> None:
        while True:
            await self._flush_outbox(ws)
            snap = self._snap
            status = {**snap.status_fields(), "locked": self.locked, "mic_active": self.mic_active}
            now = time.monotonic()
            if status != self._last_status or now - self._last_status_t >= self.cfg.status_interval_s:
                await self._send(ws, envelope("status", **status))
                self._last_status, self._last_status_t = status, now
            if snap.exam_state == "running" and now - self._last_preview_t >= self.cfg.preview_interval_s:
                self._last_preview_t = now
                if hasattr(self.view, "preview_packet"):
                    packet = await asyncio.to_thread(self.view.preview_packet)
                else:  # legacy view adapters have no frame provenance; never infer it from status
                    jpeg = await asyncio.to_thread(self.view.preview_jpeg)
                    packet = (jpeg, {"source_mode": "unknown", "source_session_id": None, "frame_wall": _now()}) if jpeg else None
                if packet:
                    jpeg, metadata = packet
                    await self._send(ws, envelope("preview", jpeg_b64=base64.b64encode(jpeg).decode("ascii"), **metadata))
            self._outbox_signal.clear()
            try:
                await asyncio.wait_for(self._outbox_signal.wait(), timeout=self.cfg.poll_interval_s)
            except asyncio.TimeoutError:
                pass

    # ================================================================== incidents + clips
    def _diff_incidents(self, snap: Snapshot) -> bool:
        queued = False
        for inc in snap.incidents:
            iid = inc["incident_id"]
            clip = self._clips.get(iid)
            clip_ok = isinstance(clip, Path)
            key = (inc["state"], clip_ok)
            if iid not in self._incident_sent and inc["rule_id"] not in NO_CLIP_RULES and snap.exam_state == "running":
                self._start_clip(iid, inc["t_start_ms"])
            if self._incident_sent.get(iid) == key:
                continue
            self._incident_sent[iid] = key
            self._queue(
                "incident",
                incident_id=iid,
                source_mode=inc.get("source_mode", "unknown"),
                source_session_id=inc.get("source_session_id"),
                rule_id=inc["rule_id"],
                category=inc["category"],
                priority=inc["priority"],
                state=inc["state"],
                t_start_wall=inc["t_start_wall"],
                duration_ms=inc["duration_ms"],
                explanation_ru=inc["explanation_ru"],
                clip_available=clip_ok,
            )
            queued = True
        return queued

    def _start_clip(self, incident_id: str, t_start_ms: float) -> None:
        self._clips[incident_id] = "pending"
        ev = self._clip_events[incident_id] = threading.Event()

        def work() -> None:
            try:
                path = self.view.export_clip(t_start_ms, self.cfg.clip_before_s, self.cfg.clip_after_s)
                self._clips[incident_id] = Path(path)
            except Exception as exc:
                self._clips[incident_id] = "failed"
                log.info("uplink: clip for %s not available (%s)", incident_id, getattr(exc, "code", type(exc).__name__))
            finally:
                ev.set()

        threading.Thread(target=work, name=f"clip-{incident_id[-8:]}", daemon=True).start()

    # ================================================================== receiving / commands
    async def _receiver(self, ws: Any) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                log.info("uplink: non-JSON message ignored")
                continue
            kind = msg.get("type")
            if kind == "ping":
                await self._send(ws, envelope("pong"))
            elif kind == "command":
                asyncio.create_task(self._command(ws, msg))
            elif kind == "error":
                log.warning("uplink: server error %s", msg.get("code"))
            else:
                log.info("uplink: message type %r ignored", kind)

    async def _command(self, ws: Any, msg: dict[str, Any]) -> None:
        cid = str(msg.get("command_id", ""))
        kind = msg.get("kind")
        payload = msg.get("payload") or {}
        if cid and cid in self._done_commands:  # re-delivery (T04 reuses command_id): never execute twice
            prev = self._done_commands[cid]
            if prev is not None:  # finished: repeat the same ack (the server keeps the first one)
                self._queue("ack", **prev)
                await self._flush_outbox(ws)
            return  # still executing: the original will send the only ack
        if cid:
            self._done_commands[cid] = None
        ok, err, code = False, "Неизвестная команда", "unsupported"
        error_code: str | None = None  # T05 audio ack field
        try:
            if kind == "start_exam":
                ok, err = await asyncio.to_thread(self.view.start_exam)
            elif kind == "finish_exam":
                ok, err = await asyncio.to_thread(self.view.finish_exam)
            elif kind == "lock":
                reason = payload.get("reason_ru")
                if isinstance(reason, str) and 1 <= len(reason.strip()) <= 200:
                    self.locked, self.lock_reason_ru = True, reason.strip()
                    ok, err = True, None
                else:
                    err, code = "Причина блокировки должна содержать 1–200 символов", "invalid"
            elif kind == "unlock":
                self.locked, self.lock_reason_ru = False, None
                ok, err = True, None
            elif kind in ("audio_start", "audio_update"):
                # No WebRTC in the student app yet: never claim a live microphone (T05: ok:true = mic obtained).
                ok, err, code = False, AUDIO_NOT_SUPPORTED_RU, "unsupported"
                error_code = "not_supported"
            elif kind == "audio_stop":
                self.mic_active, self.audio_direction = False, None  # nothing is captured; stopping is always ok
                ok, err = True, None
            elif kind == "request_clip":
                ok, err = await self._upload_clip(str(payload.get("incident_id", "")))
                code = "failed"
        except Exception as exc:  # a failing command must never break the session
            log.exception("uplink: command %s failed", kind)
            ok, err = False, f"Ошибка выполнения: {type(exc).__name__}"
        if not ok and kind in ("start_exam", "finish_exam", "request_clip", "lock") and code == "unsupported":
            code = "failed"
        self.last_command = {"command_id": cid, "kind": kind, "ok": ok}
        if kind in ("lock", "unlock", "audio_start", "audio_stop", "start_exam", "finish_exam"):
            self._publish_state()
        ack: dict[str, Any] = {"command_id": cid, "ok": ok}
        if not ok:
            ack.update(error_ru=(err or "Ошибка")[:200], code=code)  # code: additive (T04 R2)
            if error_code:
                ack["error_code"] = error_code  # additive (T05 PROTOCOL_AUDIO)
        if kind in ("lock", "unlock", "start_exam", "finish_exam"):
            ack["result"] = {"locked": self.locked, "exam_state": self._snap.exam_state}  # additive (T04 R2)
        if cid:
            self._done_commands[cid] = ack
            if len(self._done_commands) > 500:
                self._done_commands.pop(next(iter(self._done_commands)))
        self._queue("ack", **ack)
        await self._flush_outbox(ws)

    async def _upload_clip(self, incident_id: str) -> tuple[bool, str | None]:
        ev = self._clip_events.get(incident_id)
        if ev is not None and not ev.is_set():
            await asyncio.to_thread(ev.wait, CLIP_WAIT_ON_REQUEST_S)
        clip = self._clips.get(incident_id)
        if not isinstance(clip, Path) or not clip.is_file():
            return False, "Клип для этого эпизода недоступен"
        token = self.outbox.get_meta("resume_token") or ""
        ok, detail = await asyncio.to_thread(self._http_post, self.cfg.clip_url(incident_id), clip, token, "video/x-msvideo")
        return (True, None) if ok else (False, f"Не удалось загрузить клип ({detail})")

    # ================================================================== Electron state
    def _set_connection(self, value: str, message_ru: str | None = None) -> None:
        if value == self.connection and message_ru is None:
            return
        self.connection = value
        self._publish_state(message_ru)

    def _publish_state(self, message_ru: str | None = None) -> None:
        if self._publish is None:
            return
        try:
            self._last_state_publish = time.monotonic()
            self._publish(ClassStateMsg(
                connection=self.connection, server=self.cfg.server, computer_name=self.cfg.computer_name,
                student_label=self.cfg.student_label, student_id=self.student_id,
                locked=self.locked, lock_reason_ru=self.lock_reason_ru, mic_active=self.mic_active,
                audio_direction=self.audio_direction, exam=self.exam, last_command=self.last_command, message_ru=message_ru,
            ))
        except Exception:
            log.exception("uplink: publishing class_state failed")
