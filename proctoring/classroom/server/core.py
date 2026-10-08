"""Class server state machine (owner: T01). Transport-agnostic: WebSocket/HTTP handlers live in app.py.

All mutations run on the server's event loop thread (handlers are async); a re-entrant lock additionally
protects state for feature threads. Every change is written through to SQLite before it is published.

Semantics kept here (and tested):
* pairing: join code (open session) or resume token -> one stable student_id; a second socket of the same
  student supersedes the first; wrong code/token is rate limited per IP.
* events: deduplicated by (student_id, event_id); (student_id, client_run_id, seq) seen with a DIFFERENT event is
  a seq conflict -> the new event is still kept and flagged (never silently dropped, never double counted).
* commands: queued -> sent (written to the socket) -> [received] -> succeeded|failed by the student's ack;
  expired when no ack before expires_at; queued/sent commands are re-delivered (same id) after reconnect and
  after a server restart, never after expiry. `unconfirmed` = ack window (v1: 10 s) passed, still pending.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from pydantic import ValidationError

from ..contracts import models as m
from ..contracts.models import (
    AudioSession,
    AudioState,
    CameraState,
    Command,
    CommandAck,
    CommandKind,
    CommandStatus,
    CommandStatusChange,
    ConnectionState,
    DataOrigin,
    DeviceStatus,
    ExamPolicy,
    Incident,
    IncidentState,
    ObservationEvent,
    Session,
    SessionCreate,
    SessionCreated,
    Student,
    StudentCard,
    Zone,
    ZoneSource,
    utc_now,
)
from .auth import RateLimiter, new_join_code, new_token, token_hash
from .config import ServerConfig
from .db import Database
from .hub import TeacherHub

log = logging.getLogger("classroom.core")
SIM_PREFIX = "qorgau-class-simulator"


def _source_origin(mode: m.SourceMode | None, legacy_simulated: bool = False) -> DataOrigin:
    """Client-declared provenance; an absent/unknown source can never establish live capture."""
    if legacy_simulated:
        return DataOrigin.SIMULATED
    return {m.SourceMode.LIVE: DataOrigin.REAL, m.SourceMode.SYNTHETIC: DataOrigin.SIMULATED,
            m.SourceMode.REPLAY: DataOrigin.REPLAY}.get(mode, DataOrigin.UNKNOWN)


class ClassroomError(Exception):
    """Maps to an ApiError / wire error. status = HTTP status for REST callers."""

    def __init__(self, code: str, message_ru: str, status: int = 409, **details: Any):
        super().__init__(message_ru)
        self.code = code
        self.message_ru = message_ru
        self.status = status
        self.details = details


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


_FINGERPRINT_KEYS = ("incident_id", "rule_id", "category", "priority", "state", "t_start_wall", "duration_ms", "explanation_ru", "clip_available", "source_mode", "source_session_id")


def _incident_fingerprint(payload: dict[str, Any]) -> str:
    """Content of an incident message without transport fields (msg_id, sent_at, seq): equal = the same event."""
    return hashlib.sha256(json.dumps([payload.get(k) for k in _FINGERPRINT_KEYS], ensure_ascii=False).encode()).hexdigest()[:16]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


# ------------------------------------------------------------------------------------------- connection
class StudentConnection:
    """Outgoing side of one student socket. enqueue() is non-blocking; the writer task in app.py drains it and
    reports back with on_written so that 'sent' really means 'written to the socket'."""

    def __init__(self, student_id: str, epoch: int, loop: asyncio.AbstractEventLoop, maxsize: int = 256):
        self.student_id = student_id
        self.epoch = epoch
        self.loop = loop
        self.queue: asyncio.Queue[tuple[dict[str, Any], Callable[[], None] | None]] = asyncio.Queue(maxsize=maxsize)
        self.last_pong = time.monotonic()
        self.closed = False
        self.close_reason: str | None = None
        self.close_code = 1000
        self.close_event = asyncio.Event()

    def enqueue(self, message: dict[str, Any], on_written: Callable[[], None] | None = None) -> bool:
        if self.closed:
            return False
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            try:
                self.queue.put_nowait((message, on_written))
                return True
            except asyncio.QueueFull:
                self.request_close(4408, "send_queue_full")
                return False
        self.loop.call_soon_threadsafe(self._put_threadsafe, message, on_written)
        return True

    def _put_threadsafe(self, message: dict[str, Any], on_written: Callable[[], None] | None) -> None:
        try:
            self.queue.put_nowait((message, on_written))
        except asyncio.QueueFull:
            self.request_close(4408, "send_queue_full")

    def request_close(self, code: int, reason: str) -> None:
        if not self.closed:
            self.closed = True
            self.close_code = code
            self.close_reason = reason
        self.close_event.set()


def envelope(**fields: Any) -> dict[str, Any]:
    return {"v": 1, "msg_id": secrets.token_hex(16), "sent_at": utc_now().isoformat(), **fields}


# -------------------------------------------------------------------------------------------- records
@dataclass
class SessionRec:
    session_id: str
    title: str
    state: str
    created_at: datetime
    exam: ExamPolicy
    join_code: str | None
    closed_at: datetime | None = None


@dataclass
class StudentRec:
    student_id: str
    session_id: str
    token_hash: str
    student_label: str
    computer_name: str
    app_version: str
    origin: DataOrigin
    paired_at: datetime
    paired_ip: str
    last_seen_at: datetime | None = None
    reconnects: int = 0
    capabilities: list[str] = field(default_factory=list)
    client_run_id: str | None = None
    # runtime only
    legacy_simulated: bool = False  # explicitly declared by this connection's hello, not inferred from status
    conn: StudentConnection | None = None
    epoch: int = 0
    connected_since: datetime | None = None
    status: dict[str, Any] | None = None  # last Status fields + received_at/sent_at (persisted)
    preview: bytes | None = None
    preview_meta: dict[str, Any] | None = None
    preview_seq: int = 0
    preview_mono: float = 0.0
    last_event_at: datetime | None = None
    published_zone: tuple[str, str, bool, bool] | None = None


@dataclass
class CommandRec:
    command_id: str
    student_id: str
    kind: CommandKind
    payload: dict[str, Any]
    issued_by: str
    issued_at: datetime
    expires_at: datetime
    status: CommandStatus
    status_at: datetime
    attempts: int = 0
    sent_at: datetime | None = None
    ack_deadline_at: datetime | None = None
    unconfirmed: bool = False
    ack: CommandAck | None = None
    history: list[CommandStatusChange] = field(default_factory=list)
    in_flight: bool = False  # enqueued on the current connection, not yet written

    def model(self) -> Command:
        return Command(
            command_id=self.command_id,
            student_id=self.student_id,
            kind=self.kind,
            payload=self.payload,
            issued_by=self.issued_by,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            status=self.status,
            status_at=self.status_at,
            attempts=self.attempts,
            sent_at=self.sent_at,
            ack_deadline_at=self.ack_deadline_at,
            unconfirmed=self.unconfirmed,
            ack=self.ack,
            history=list(self.history),
        )


# ------------------------------------------------------------------------------------------------ core
class ClassroomCore:
    def __init__(self, config: ServerConfig, db: Database, hub: TeacherHub, *, clock: Callable[[], datetime] = utc_now):
        self.config = config
        self.db = db
        self.hub = hub
        self.now = clock
        self.lock = threading.RLock()
        self.join_limiter = RateLimiter(config.join_fail_limit, config.join_block_s)
        self.sessions: dict[str, SessionRec] = {}
        self.current_session_id: str | None = None
        self.students: dict[str, StudentRec] = {}
        self.by_token: dict[str, str] = {}
        self.commands: dict[str, CommandRec] = {}
        self.audio: dict[str, AudioSession] = {}
        self.incidents: dict[tuple[str, str], Incident] = {}
        self.counters = {"duplicates": 0, "seq_conflicts": 0, "invalid_messages": 0, "unknown_types": 0, "join_rejected": 0, "superseded": 0, "late_acks": 0, "foreign_acks": 0}
        self.features: Any = None  # FeatureManager, set by app
        self._load()

    # ======================================================================================= persistence
    def _load(self) -> None:
        for r in self.db.query("SELECT * FROM sessions"):
            rec = SessionRec(r["session_id"], r["title"], r["state"], _dt(r["created_at"]), ExamPolicy.model_validate_json(r["exam_json"]), r["join_code"], _dt(r["closed_at"]))  # type: ignore[arg-type]
            self.sessions[rec.session_id] = rec
            if rec.state == "open":
                self.current_session_id = rec.session_id
        for r in self.db.query("SELECT * FROM students"):
            rec = StudentRec(
                r["student_id"], r["session_id"], r["token_hash"], r["student_label"], r["computer_name"], r["app_version"],
                DataOrigin(r["origin"]), _dt(r["paired_at"]), r["paired_ip"], _dt(r["last_seen_at"]), r["reconnects"],  # type: ignore[arg-type]
                json.loads(r["capabilities_json"]), r["client_run_id"],
            )
            self.students[rec.student_id] = rec
            self.by_token[rec.token_hash] = rec.student_id
        for r in self.db.query("SELECT student_id, json FROM device_status"):
            if r["student_id"] in self.students:
                self.students[r["student_id"]].status = json.loads(r["json"])
        for r in self.db.query("SELECT json FROM incidents"):
            inc = Incident.model_validate_json(r["json"])
            self.incidents[(inc.student_id, inc.incident_id)] = inc
            st = self.students.get(inc.student_id)
            if st and (st.last_event_at is None or inc.last_received_at > st.last_event_at):
                st.last_event_at = inc.last_received_at
        for r in self.db.query("SELECT json FROM commands"):
            cmd = Command.model_validate_json(r["json"])
            self.commands[cmd.command_id] = CommandRec(
                cmd.command_id, cmd.student_id, cmd.kind, cmd.payload, cmd.issued_by, cmd.issued_at, cmd.expires_at,
                cmd.status, cmd.status_at, cmd.attempts, cmd.sent_at, cmd.ack_deadline_at, cmd.unconfirmed, cmd.ack, list(cmd.history),
            )
        now = self.now()
        for r in self.db.query("SELECT json FROM audio_sessions"):
            au = AudioSession.model_validate_json(r["json"])
            if au.state in (AudioState.REQUESTED, AudioState.ACTIVE):  # a live call cannot survive a server restart
                au = au.model_copy(update={"state": AudioState.ENDED, "ended_at": now, "end_reason": "server_restart"})
                self._save_audio(au)
            self.audio[au.audio_session_id] = au

    def _save_session(self, s: SessionRec) -> None:
        self.db.execute(
            "INSERT INTO sessions(session_id,title,state,created_at,closed_at,exam_json,join_code) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET state=excluded.state, closed_at=excluded.closed_at, join_code=excluded.join_code, exam_json=excluded.exam_json",
            (s.session_id, s.title, s.state, _iso(s.created_at), _iso(s.closed_at), s.exam.model_dump_json(), s.join_code),
        )

    def _save_student(self, s: StudentRec) -> None:
        self.db.execute(
            "INSERT INTO students(student_id,session_id,token_hash,student_label,computer_name,app_version,origin,paired_at,paired_ip,last_seen_at,reconnects,capabilities_json,client_run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(student_id) DO UPDATE SET student_label=excluded.student_label, computer_name=excluded.computer_name, "
            "app_version=excluded.app_version, origin=excluded.origin, last_seen_at=excluded.last_seen_at, reconnects=excluded.reconnects, capabilities_json=excluded.capabilities_json, client_run_id=excluded.client_run_id",
            (s.student_id, s.session_id, s.token_hash, s.student_label, s.computer_name, s.app_version, s.origin.value, _iso(s.paired_at), s.paired_ip,
             _iso(s.last_seen_at), s.reconnects, json.dumps(s.capabilities), s.client_run_id),
        )

    def _save_command(self, c: CommandRec) -> None:
        self.db.execute(
            "INSERT INTO commands(command_id,student_id,status,json) VALUES (?,?,?,?) ON CONFLICT(command_id) DO UPDATE SET status=excluded.status, json=excluded.json",
            (c.command_id, c.student_id, c.status.value, c.model().model_dump_json()),
        )

    def _save_audio(self, a: AudioSession) -> None:
        self.db.execute(
            "INSERT INTO audio_sessions(audio_session_id,student_id,json) VALUES (?,?,?) ON CONFLICT(audio_session_id) DO UPDATE SET json=excluded.json",
            (a.audio_session_id, a.student_id, a.model_dump_json()),
        )

    # ========================================================================================== sessions
    def session_model(self, s: SessionRec | None) -> Session | None:
        if s is None:
            return None
        total = sum(1 for st in self.students.values() if st.session_id == s.session_id)
        return Session(
            session_id=s.session_id, title=s.title, state=s.state, created_at=s.created_at, closed_at=s.closed_at,  # type: ignore[arg-type]
            exam=s.exam, join_code=s.join_code if s.state == "open" else None, students_total=total,
        )

    def current_session(self) -> SessionRec | None:
        return self.sessions.get(self.current_session_id) if self.current_session_id else None

    def create_session(self, body: SessionCreate) -> SessionCreated:
        with self.lock:
            self._close_current("replaced")
            now = self.now()
            sid = _new_id("cs")
            exam = ExamPolicy(
                exam_id=f"exam-{sid}", title=body.title, mode=body.mode, allowed_urls=body.allowed_urls, allowed_apps=body.allowed_apps,
                instructions_ru=body.instructions_ru, start_url=body.start_url,
            )
            rec = SessionRec(sid, body.title, "open", now, exam, new_join_code())
            self.sessions[sid] = rec
            self.current_session_id = sid
            self._save_session(rec)
            model = self.session_model(rec)
            assert model is not None
            self.hub.publish({"type": "session_update", "session": model.model_dump(mode="json")})
            return SessionCreated(session_id=sid, join_code=rec.join_code, session=model)  # type: ignore[arg-type]

    def close_session(self) -> Session | None:
        with self.lock:
            s = self.current_session()
            if s is None:
                raise ClassroomError("no_open_session", "Нет открытой сессии", 409)
            self._close_current("closed_by_teacher")
            return self.session_model(s)

    def _close_current(self, reason: str) -> None:
        s = self.current_session()
        if s is None:
            return
        s.state, s.closed_at, s.join_code = "closed", self.now(), None
        self._save_session(s)
        self.current_session_id = None
        self.hub.publish({"type": "session_update", "session": None})
        log.info("session %s closed (%s)", s.session_id, reason)

    # =========================================================================================== pairing
    def pair(self, hello: m.Hello, ip: str) -> tuple[StudentRec, str, bool]:
        """Returns (student, plaintext resume token, resumed). Raises ClassroomError on rejection."""
        with self.lock:
            left = self.join_limiter.blocked(ip)
            if left:
                raise ClassroomError("join_rate_limited", f"Слишком много неудачных попыток. Повторите через {int(left) + 1} с", 429, retry_after_s=int(left) + 1)
            simulated = hello.simulated or hello.app_version.startswith(SIM_PREFIX)
            origin = _source_origin(hello.source_mode, simulated)
            caps = sorted({k.value for k in m.V1_COMMAND_KINDS} | set((hello.capabilities.commands if hello.capabilities else [])) & {k.value for k in CommandKind})
            if hello.resume_token is not None:
                sid = self.by_token.get(token_hash(hello.resume_token))
                st = self.students.get(sid) if sid else None
                session = self.sessions.get(st.session_id) if st else None
                if st is None or session is None or session.state != "open":
                    self.counters["join_rejected"] += 1
                    self.join_limiter.fail(ip)
                    raise ClassroomError("resume_rejected", "Сессия не найдена или завершена. Введите код подключения заново", 403)
                self.join_limiter.succeed(ip)
                st.reconnects += 1
                st.student_label = hello.student_label or st.student_label
                st.computer_name = hello.computer_name or st.computer_name
                st.app_version = hello.app_version or st.app_version
                st.capabilities = caps
                st.origin, st.legacy_simulated = origin, simulated
                if hello.client_run_id:
                    st.client_run_id = hello.client_run_id
                self._save_student(st)
                return st, hello.resume_token, True
            session = self.current_session()
            if session is None or session.join_code is None or not secrets.compare_digest(hello.join_code or "", session.join_code):
                self.counters["join_rejected"] += 1
                self.join_limiter.fail(ip)
                raise ClassroomError("join_rejected", "Неверный код подключения", 403)
            self.join_limiter.succeed(ip)
            token = new_token()
            st = StudentRec(
                student_id=_new_id("st"), session_id=session.session_id, token_hash=token_hash(token),
                student_label=hello.student_label or hello.computer_name or "без имени", computer_name=hello.computer_name,
                app_version=hello.app_version, origin=origin, paired_at=self.now(), paired_ip=ip, capabilities=caps,
                client_run_id=hello.client_run_id, legacy_simulated=simulated,
            )
            self.students[st.student_id] = st
            self.by_token[st.token_hash] = st.student_id
            self._save_student(st)
            return st, token, False

    def student_by_token(self, token: str | None) -> StudentRec | None:
        if not token or len(token) != 64:
            return None
        sid = self.by_token.get(token_hash(token))
        return self.students.get(sid) if sid else None

    def exam_for(self, st: StudentRec) -> ExamPolicy:
        s = self.sessions.get(st.session_id)
        assert s is not None
        return s.exam

    def attach(self, st: StudentRec, conn: StudentConnection) -> None:
        with self.lock:
            old = st.conn
            if old is not None and not old.closed:
                self.counters["superseded"] += 1
                old.request_close(4409, "superseded_by_new_connection")
            st.conn = conn
            st.epoch = conn.epoch
            st.connected_since = self.now()
            st.last_seen_at = self.now()
            self._save_student(st)
            self._publish_card(st, force=True)
        self.deliver_pending(st)

    def detach(self, st: StudentRec, conn: StudentConnection) -> bool:
        """Returns True if this connection was the current one (the student is offline now)."""
        with self.lock:
            if st.conn is not conn:
                return False
            st.conn = None
            st.connected_since = None
            for c in self.commands.values():
                if c.student_id == st.student_id:
                    c.in_flight = False
            for au in list(self.audio.values()):
                if au.student_id == st.student_id and au.state in (AudioState.REQUESTED, AudioState.ACTIVE):
                    self._audio_end(au, "student_offline")
            self._save_student(st)
            self._publish_card(st, force=True)
            return True

    def touch(self, st: StudentRec) -> None:
        st.last_seen_at = self.now()

    # ============================================================================================ status
    def on_status(self, st: StudentRec, msg: m.Status) -> None:
        with self.lock:
            now = self.now()
            data = msg.model_dump(mode="json", exclude={"type", "v", "msg_id"})
            data["received_at"] = now.isoformat()
            st.status = data
            origin = _source_origin(msg.source_mode, st.legacy_simulated)
            if origin != st.origin:
                st.origin = origin
                self._save_student(st)
            self.db.execute(
                "INSERT INTO device_status(student_id,json) VALUES (?,?) ON CONFLICT(student_id) DO UPDATE SET json=excluded.json",
                (st.student_id, json.dumps(data)),
            )
            self._publish_card(st, force=True)

    def device_status(self, st: StudentRec) -> DeviceStatus:
        s = st.status
        now = self.now()
        online = st.conn is not None and not st.conn.closed
        if s is None:
            zone, source = Zone.GREY, (ZoneSource.SERVER_OFFLINE if not online else ZoneSource.NONE)
            return DeviceStatus(student_id=st.student_id, zone=zone, zone_source=source, stale=True)
        received = datetime.fromisoformat(s["received_at"])
        stale = (now - received).total_seconds() > self.config.status_stale_s
        # Persisted/old-connection receipts describe history, never the current screen.
        lock_fresh = online and not stale and st.connected_since is not None and received >= st.connected_since
        camera = CameraState(s["camera"])
        reported = Zone(s["zone"]) if s.get("zone") else None
        if not online:
            zone, source = Zone.GREY, ZoneSource.SERVER_OFFLINE
        elif stale:
            zone, source = Zone.GREY, ZoneSource.SERVER_STALE
        elif camera != CameraState.OK:
            zone, source = Zone.GREY, ZoneSource.SERVER_CAMERA
        elif reported is None:
            zone, source = Zone.GREY, ZoneSource.NONE
        else:
            zone, source = reported, ZoneSource.STUDENT
        return DeviceStatus(
            student_id=st.student_id, received_at=received, sent_at=_dt(s.get("sent_at")), exam_state=s.get("exam_state"),
            camera=camera, monitoring=s.get("monitoring"), zone_reported=reported, zone=zone, zone_source=source,
            zone_reasons_ru=list(s.get("zone_reasons_ru") or [])[:3], incidents_total=s.get("incidents_total", 0),
            incidents_by_priority=s.get("incidents_by_priority") or {}, locked=s.get("locked"), mic_active=s.get("mic_active"), stale=stale,
            lock_state=s.get("lock_state") if lock_fresh else "unconfirmed",
            lock_confirmed=lock_fresh and s.get("lock_confirmed") is True and s.get("lock_scope") == "app_overlay",
            lock_requested=s.get("lock_requested"), lock_scope=s.get("lock_scope"),
        )

    def student_model(self, st: StudentRec) -> Student:
        online = st.conn is not None and not st.conn.closed
        return Student(
            student_id=st.student_id, session_id=st.session_id, student_label=st.student_label, computer_name=st.computer_name,
            app_version=st.app_version, origin=st.origin, connection=ConnectionState.ONLINE if online else ConnectionState.OFFLINE,
            paired_at=st.paired_at, last_seen_at=st.last_seen_at, connected_since=st.connected_since, reconnects=st.reconnects,
            capabilities=list(st.capabilities),
        )

    def card(self, st: StudentRec) -> StudentCard:
        status = self.device_status(st)
        open_n = sum(1 for (sid, _), inc in self.incidents.items() if sid == st.student_id and inc.state == IncidentState.OPEN)
        unreviewed = self.features.incidents_unreviewed(st.student_id) if self.features is not None else None
        student = self.student_model(st)
        return StudentCard(
            **student.model_dump(include={"student_id", "session_id", "student_label", "computer_name", "app_version", "origin", "capabilities",
                                          "connection", "connected_since", "last_seen_at", "reconnects"}),
            **status.model_dump(include={"exam_state", "camera", "monitoring", "zone", "zone_reported", "zone_source", "zone_reasons_ru",
                                         "incidents_total", "incidents_by_priority", "locked", "mic_active", "stale",
                                         "lock_state", "lock_confirmed", "lock_requested", "lock_scope"}),
            connected=st.conn is not None and not st.conn.closed,
            last_status_at=status.received_at, last_event_at=st.last_event_at, incidents_open=open_n, incidents_unreviewed=unreviewed,
            preview_url=(f"/api/teacher/students/{st.student_id}/preview.jpg?seq={st.preview_seq}" if st.preview else None),
            preview_at=_dt(st.preview_meta["received_at"]) if st.preview_meta else None,
        )

    def cards(self, session_id: str | None = None) -> list[StudentCard]:
        sid = session_id or self.current_session_id
        return [self.card(st) for st in self.students.values() if sid is None or st.session_id == sid]

    def _publish_card(self, st: StudentRec, force: bool = False) -> None:
        card = self.card(st)
        key = (card.zone.value, card.zone_source.value, card.stale, card.connected)
        if not force and key == st.published_zone:
            return
        st.published_zone = key
        self.hub.publish({"type": "student_update", "student": card.model_dump(mode="json")})

    # ============================================================================================ events
    def on_incident(self, st: StudentRec, msg: m.IncidentMsg) -> tuple[Incident, bool, bool]:
        """Returns (incident, duplicate, seq_conflict)."""
        with self.lock:
            now = self.now()
            run = st.client_run_id or ""
            payload = msg.model_dump(mode="json", exclude={"snapshot_jpeg_b64"})
            fingerprint = _incident_fingerprint(payload)
            seq_row = self.db.query("SELECT event_id FROM event_seq WHERE student_id=? AND client_run_id=? AND seq=?", (st.student_id, run, msg.seq))
            if msg.event_id:  # v1.1: the client names the event
                event_id = msg.event_id
            elif not seq_row:  # plain v1: protocol §3.1 dedups by (student_id, seq)
                event_id = f"v1:{run}:{msg.seq}"
            else:
                first = self.db.query("SELECT json FROM events WHERE student_id=? AND event_id=?", (st.student_id, seq_row[0]["event_id"]))
                same = bool(first) and _incident_fingerprint(json.loads(first[0]["json"])["payload"]) == fingerprint
                # same seq + same content = re-delivery; same seq + other content = the client lost its counter
                event_id = seq_row[0]["event_id"] if same else f"v1:{run}:{msg.seq}:{fingerprint}"
            existing = self.db.query("SELECT 1 FROM events WHERE student_id=? AND event_id=?", (st.student_id, event_id))
            if existing:
                self.counters["duplicates"] += 1
                inc = self.incidents.get((st.student_id, msg.incident_id))
                assert inc is not None
                return inc, True, False
            seq_conflict = bool(seq_row) and seq_row[0]["event_id"] != event_id
            if seq_conflict:
                self.counters["seq_conflicts"] += 1
                log.warning("student %s: seq %s (run %r) reused by a different event %s (was %s); both kept", st.student_id, msg.seq, run, event_id, seq_row[0]["event_id"])
            event_time = msg.t_start_wall + (timedelta(milliseconds=msg.duration_ms) if msg.state == IncidentState.CLOSED else timedelta(0))
            payload["has_snapshot"] = msg.snapshot_jpeg_b64 is not None
            origin = _source_origin(msg.source_mode, st.legacy_simulated)
            event = ObservationEvent(
                event_id=event_id, student_id=st.student_id, session_id=st.session_id, kind=m.EventKind.INCIDENT, seq=msg.seq,
                client_run_id=st.client_run_id, event_time=event_time, sent_at=msg.sent_at, received_at=now, origin=origin,
                seq_conflict=seq_conflict, payload=payload,
            )
            prev = self.incidents.get((st.student_id, msg.incident_id))
            regress = prev is not None and prev.state == IncidentState.CLOSED and msg.state == IncidentState.OPEN
            base = prev if regress else None
            inc = Incident(
                incident_id=msg.incident_id, student_id=st.student_id, session_id=st.session_id,
                rule_id=(base or msg).rule_id, category=(base or msg).category, priority=(base or msg).priority,
                state=prev.state if regress else msg.state, t_start_wall=(base or msg).t_start_wall,
                duration_ms=base.duration_ms if base else msg.duration_ms, explanation_ru=(base or msg).explanation_ru,
                clip_available=(prev.clip_available if prev else False) or msg.clip_available,
                has_snapshot=(prev.has_snapshot if prev else False) or msg.snapshot_jpeg_b64 is not None,
                origin=prev.origin if prev else origin, first_received_at=prev.first_received_at if prev else now, last_received_at=now,
                events=(prev.events + 1) if prev else 1,
            )
            with self.db.transaction() as tx:
                tx.execute(
                    "INSERT INTO events(student_id,event_id,session_id,kind,seq,client_run_id,received_at,json) VALUES (?,?,?,?,?,?,?,?)",
                    (st.student_id, event_id, st.session_id, "incident", msg.seq, run, now.isoformat(), event.model_dump_json()),
                )
                tx.execute("INSERT OR IGNORE INTO event_seq(student_id,client_run_id,seq,event_id) VALUES (?,?,?,?)", (st.student_id, run, msg.seq, event_id))
                tx.execute(
                    "INSERT INTO incidents(student_id,incident_id,json) VALUES (?,?,?) ON CONFLICT(student_id,incident_id) DO UPDATE SET json=excluded.json",
                    (st.student_id, msg.incident_id, inc.model_dump_json()),
                )
            self.incidents[(st.student_id, msg.incident_id)] = inc
            st.last_event_at = now
            self.hub.publish({"type": "incident", "student_id": st.student_id, "incident": inc.model_dump(mode="json"), "duplicate": False})
            self._publish_card(st, force=True)
            return inc, False, seq_conflict

    def events_for(self, student_id: str, limit: int = 500) -> list[ObservationEvent]:
        rows = self.db.query("SELECT json FROM events WHERE student_id=? ORDER BY received_at DESC LIMIT ?", (student_id, limit))
        return [ObservationEvent.model_validate_json(r["json"]) for r in rows]

    def incidents_for(self, student_id: str) -> list[Incident]:
        return sorted((i for (sid, _), i in self.incidents.items() if sid == student_id), key=lambda i: i.t_start_wall)

    # =========================================================================================== preview
    def on_preview(self, st: StudentRec, msg: m.Preview) -> bool:
        mono = time.monotonic()
        if st.preview_seq and mono - st.preview_mono < self.config.preview_min_interval_s:
            return False
        try:
            data = base64.b64decode(msg.jpeg_b64, validate=True)
        except (binascii.Error, ValueError):
            raise ClassroomError("invalid_preview", "Превью не является base64", 422)
        if not (data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9") or len(data) > m.MAX_PREVIEW_JPEG_BYTES:
            raise ClassroomError("invalid_preview", "Превью должно быть JPEG не больше 30 КБ", 422)
        with self.lock:
            st.preview, st.preview_seq, st.preview_mono = data, st.preview_seq + 1, mono
            origin = _source_origin(msg.source_mode, st.legacy_simulated)
            st.preview_meta = {"frame_wall": msg.frame_wall.isoformat(), "received_at": self.now().isoformat(), "byte_length": len(data),
                               "origin": origin.value, "source_mode": msg.source_mode, "source_session_id": msg.source_session_id}
            self.hub.publish({
                "type": "preview", "student_id": st.student_id, "preview_seq": st.preview_seq, "frame_wall": st.preview_meta["frame_wall"],
                "received_at": st.preview_meta["received_at"], "byte_length": len(data), "origin": origin.value,
                "url": f"/api/teacher/students/{st.student_id}/preview.jpg?seq={st.preview_seq}", "jpeg_b64": msg.jpeg_b64,
            })
        return True

    # ========================================================================================== commands
    def submit_command(self, student_id: str, kind: CommandKind, payload: dict[str, Any], *, issued_by: str, ttl_ms: int | None = None) -> Command:
        with self.lock:
            if kind in (CommandKind.AUDIO_START, CommandKind.AUDIO_STOP):
                raise ClassroomError("audio_extension_required", "Используйте проверенную аудиосвязь панели преподавателя", 422)
            st = self.students.get(student_id)
            if st is None:
                raise ClassroomError("student_not_found", "Студент не найден", 404)
            session = self.sessions.get(st.session_id)
            if session is None or session.state != "open":
                raise ClassroomError("session_closed", "Сессия этого студента завершена", 409)
            if kind.value not in st.capabilities:
                raise ClassroomError("command_unsupported", f"Клиент студента не поддерживает команду {kind.value}", 422)
            try:
                clean = m.COMMAND_PAYLOADS[kind].model_validate(payload).model_dump(mode="json", exclude_none=kind != CommandKind.APPLY_POLICY)
            except ValidationError as exc:
                raise ClassroomError("invalid_payload", "Неверные параметры команды", 422, problem=str(exc.errors()[0].get("msg", ""))[:200])
            if kind == CommandKind.AUDIO_STOP and self._live_audio(student_id) is None:
                raise ClassroomError("no_active_audio", "Аудиосвязь с этим студентом не активна", 409)
            if kind == CommandKind.AUDIO_START and self._live_audio(student_id) is not None:
                raise ClassroomError("audio_already_active", "Аудиосвязь уже запрошена или активна", 409)
            now = self.now()
            ttl = timedelta(milliseconds=ttl_ms) if ttl_ms else timedelta(seconds=self.config.command_ttl_s)
            rec = CommandRec(_new_id("cmd"), student_id, kind, clean, issued_by, now, now + ttl, CommandStatus.QUEUED, now)
            rec.history.append(CommandStatusChange(status=CommandStatus.QUEUED, at=now))
            self.commands[rec.command_id] = rec
            if kind == CommandKind.AUDIO_START:
                au = AudioSession(
                    audio_session_id=_new_id("au"), student_id=student_id, direction=clean["direction"], state=AudioState.REQUESTED,
                    started_by=issued_by, start_command_id=rec.command_id, requested_at=now,
                )
                self.audio[au.audio_session_id] = au
                self._save_audio(au)
                self.hub.publish({"type": "audio_session_update", "audio": au.model_dump(mode="json")})
            elif kind == CommandKind.AUDIO_STOP:
                au = self._live_audio(student_id)
                assert au is not None
                au = au.model_copy(update={"stop_command_id": rec.command_id})
                self.audio[au.audio_session_id] = au
                self._save_audio(au)
            self._save_command(rec)
            self._publish_command(rec)
            self._send_command(st, rec)
            return rec.model()

    def _send_command(self, st: StudentRec, rec: CommandRec) -> None:
        conn = st.conn
        if conn is None or conn.closed or rec.in_flight or rec.status in m.TERMINAL_COMMAND_STATUSES:
            return
        now = self.now()
        if now >= rec.expires_at:
            return
        attempt = rec.attempts + 1
        msg = envelope(
            type="command", command_id=rec.command_id, kind=rec.kind.value, payload=rec.payload, issued_at=rec.issued_at.isoformat(),
            expires_at=rec.expires_at.isoformat(), ttl_ms=int((rec.expires_at - now).total_seconds() * 1000), attempt=attempt,
        )
        epoch = conn.epoch
        rec.in_flight = conn.enqueue(msg, lambda: self._on_command_written(rec.command_id, epoch))

    def _on_command_written(self, command_id: str, epoch: int) -> None:
        with self.lock:
            rec = self.commands.get(command_id)
            if rec is None:
                return
            rec.in_flight = False
            st = self.students.get(rec.student_id)
            if st is None or st.epoch != epoch:
                return
            rec.attempts += 1
            now = self.now()
            rec.sent_at = now
            rec.ack_deadline_at = now + timedelta(seconds=self.config.ack_window_s)
            rec.unconfirmed = False
            if rec.status == CommandStatus.QUEUED:
                self._set_status(rec, CommandStatus.SENT, note=f"attempt {rec.attempts}")
            else:
                rec.history.append(CommandStatusChange(status=rec.status, at=now, note=f"re-sent, attempt {rec.attempts}"))
                self._save_command(rec)
                self._publish_command(rec)

    def deliver_pending(self, st: StudentRec) -> None:
        with self.lock:
            pending = sorted(
                (c for c in self.commands.values() if c.student_id == st.student_id and c.status in (CommandStatus.QUEUED, CommandStatus.SENT, CommandStatus.RECEIVED)),
                key=lambda c: c.issued_at,
            )
            for rec in pending:
                self._send_command(st, rec)

    def on_progress(self, st: StudentRec, msg: m.CommandProgress) -> None:
        with self.lock:
            rec = self.commands.get(msg.command_id)
            if rec is None or rec.student_id != st.student_id:
                self.counters["foreign_acks"] += 1
                return
            if rec.status == CommandStatus.SENT:
                self._set_status(rec, CommandStatus.RECEIVED, note=msg.state)

    def on_ack(self, st: StudentRec, msg: m.Ack) -> Command | None:
        with self.lock:
            rec = self.commands.get(msg.command_id)
            if rec is None or rec.student_id != st.student_id:
                self.counters["foreign_acks"] += 1  # never let one student confirm another student's command
                return None
            if rec.ack is not None:
                return rec.model()  # one ack per command: a repeated ack (re-delivery) changes nothing
            now = self.now()
            late = now > rec.expires_at
            if late:
                self.counters["late_acks"] += 1
            rec.ack = CommandAck(
                command_id=rec.command_id, ok=msg.ok, code=msg.code, error_ru=msg.error_ru, executed_at=msg.executed_at,
                received_at=now, late=late, result=msg.result,
            )
            rec.unconfirmed = False
            self._set_status(rec, CommandStatus.SUCCEEDED if msg.ok else CommandStatus.FAILED, note="late ack (after expiry)" if late else None)
            self.hub.publish({"type": "ack", "student_id": st.student_id, "command_id": rec.command_id, "ok": msg.ok, "error_ru": msg.error_ru})
            self._audio_on_command(rec)
            return rec.model()

    def cancel_command(self, command_id: str) -> Command:
        with self.lock:
            rec = self.commands.get(command_id)
            if rec is None:
                raise ClassroomError("command_not_found", "Команда не найдена", 404)
            if rec.status != CommandStatus.QUEUED:
                raise ClassroomError("command_not_cancellable", "Отменить можно только команду, которая ещё не отправлена", 409, command_status=rec.status.value)
            self._set_status(rec, CommandStatus.CANCELLED)
            self._audio_on_command(rec)
            return rec.model()

    def _set_status(self, rec: CommandRec, status: CommandStatus, note: str | None = None) -> None:
        now = self.now()
        rec.status, rec.status_at = status, now
        rec.history.append(CommandStatusChange(status=status, at=now, note=note))
        self._save_command(rec)
        self._publish_command(rec)

    def _publish_command(self, rec: CommandRec) -> None:
        self.hub.publish({"type": "command_update", "command": rec.model().model_dump(mode="json")})
        if self.features is not None:
            self.features.command_update(rec.model())

    def commands_for(self, student_id: str) -> list[Command]:
        return [c.model() for c in sorted(self.commands.values(), key=lambda c: c.issued_at) if c.student_id == student_id]

    # ============================================================================================= audio
    def _live_audio(self, student_id: str) -> AudioSession | None:
        for au in self.audio.values():
            if au.student_id == student_id and au.state in (AudioState.REQUESTED, AudioState.ACTIVE):
                return au
        return None

    def _audio_on_command(self, rec: CommandRec) -> None:
        if rec.kind == CommandKind.AUDIO_START:
            au = next((a for a in self.audio.values() if a.start_command_id == rec.command_id), None)
            if au is None or au.state not in (AudioState.REQUESTED, AudioState.ACTIVE):
                return
            if rec.status == CommandStatus.SUCCEEDED and au.state == AudioState.REQUESTED:
                self._audio_put(au.model_copy(update={"state": AudioState.ACTIVE, "active_at": self.now()}))
            elif rec.status in (CommandStatus.FAILED, CommandStatus.EXPIRED, CommandStatus.CANCELLED):
                self._audio_put(au.model_copy(update={"state": AudioState.FAILED, "ended_at": self.now(), "end_reason": f"start_{rec.status.value}"}))
        elif rec.kind == CommandKind.AUDIO_STOP and rec.status == CommandStatus.SUCCEEDED:
            au = next((a for a in self.audio.values() if a.stop_command_id == rec.command_id), None)
            if au is not None and au.state in (AudioState.REQUESTED, AudioState.ACTIVE):
                self._audio_end(au, "stopped")

    def _audio_end(self, au: AudioSession, reason: str) -> None:
        self._audio_put(au.model_copy(update={"state": AudioState.ENDED, "ended_at": self.now(), "end_reason": reason}))

    def _audio_put(self, au: AudioSession) -> None:
        self.audio[au.audio_session_id] = au
        self._save_audio(au)
        self.hub.publish({"type": "audio_session_update", "audio": au.model_dump(mode="json")})

    def _audio_for_signal(self, student_id: str, command_id: str) -> AudioSession:
        au = self._live_audio(student_id)
        if au is None or au.start_command_id != command_id:
            raise ClassroomError("no_audio_session", "Нет активной аудиосвязи для этой команды", 409)
        return au

    def relay_teacher_signal(self, sig: m.TeacherAudioSignal) -> None:
        with self.lock:
            st = self.students.get(sig.student_id)
            if st is None:
                raise ClassroomError("student_not_found", "Студент не найден", 404)
            au = self._audio_for_signal(st.student_id, sig.command_id)
            if st.conn is None or st.conn.closed:
                raise ClassroomError("student_offline", "Студент не в сети", 409)
            st.conn.enqueue(envelope(type="audio_signal", command_id=sig.command_id, audio_session_id=au.audio_session_id, sdp=sig.sdp, ice=sig.ice))

    def relay_student_signal(self, st: StudentRec, msg: m.AudioSignalIn) -> None:
        with self.lock:
            au = self._audio_for_signal(st.student_id, msg.command_id)
            self.hub.publish({"type": "audio_signal", "student_id": st.student_id, "command_id": msg.command_id, "audio_session_id": au.audio_session_id, "sdp": msg.sdp, "ice": msg.ice})

    # ============================================================================================== tick
    def tick(self) -> None:
        with self.lock:
            now = self.now()
            for rec in list(self.commands.values()):
                if rec.status in m.TERMINAL_COMMAND_STATUSES:
                    continue
                if now >= rec.expires_at:
                    rec.unconfirmed = False
                    self._set_status(rec, CommandStatus.EXPIRED, note="never delivered" if rec.status == CommandStatus.QUEUED else "no ack before expiry")
                    self._audio_on_command(rec)
                elif rec.status in (CommandStatus.SENT, CommandStatus.RECEIVED) and rec.ack_deadline_at and now >= rec.ack_deadline_at and not rec.unconfirmed:
                    rec.unconfirmed = True
                    self._save_command(rec)
                    self._publish_command(rec)
            for st in self.students.values():
                if st.session_id == self.current_session_id:
                    self._publish_card(st)

    def info_counts(self) -> dict[str, int]:
        sid = self.current_session_id
        students = [s for s in self.students.values() if s.session_id == sid]
        return {
            "students_total": len(students),
            "students_online": sum(1 for s in students if s.conn is not None and not s.conn.closed),
            "simulated_students": sum(1 for s in students if s.origin == DataOrigin.SIMULATED),
        }
