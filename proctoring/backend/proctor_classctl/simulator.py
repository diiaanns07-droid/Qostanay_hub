"""SIMULATOR of student clients (T04). For checking the teacher interface and the command flow only.

It is NOT a student client and never touches a real computer: no lock screen, no restrictions,
no camera. Every simulated student is labelled "СИМУЛЯТОР", is flagged `simulated=True` in all teacher
views, and every error text it produces starts with "СИМУЛЯТОР:".

Each simulated student follows a behaviour (changeable at runtime):
  success                 progress (if declared) + ack ok after `delay_s`, status reflects the state
  delay                   ack ok after 12 s (longer than the 10 s ack timeout -> "нет ответа", then late ack)
  error                   ack ok=false (code "failed")
  unsupported             declares capabilities without lock/unlock/apply_policy (buttons must be disabled)
  disconnect_before_exec  drops the connection on receipt, reconnects after `reconnect_s`, then executes
                          the re-delivered command (if it has not expired)
  disconnect_after_exec   executes, drops before acking, reconnects after `reconnect_s` and sends the
                          queued ack (late confirmation)
  no_ack                  never answers (stays connected)
  v1                      plain qorgau.class.v1 client: no `capabilities` in hello
  offline                 not connected until set_online(True)
The simulated client honours `expires_at`/`ttl_ms` and de-duplicates by `command_id` exactly as
handoffs/T04/STUDENT_CLIENT.md requires from the real client.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from .clock import Clock, iso, parse_iso

BEHAVIOURS = ("success", "delay", "error", "unsupported", "disconnect_before_exec", "disconnect_after_exec", "no_ack", "v1", "offline")
FULL_CAPS = {
    "commands": ["start_exam", "finish_exam", "lock", "unlock", "apply_policy"],
    "modes": ["url", "app"],
    "command_progress": True,
    "command_expiry": True,
    "site_timer_pause": False,
}
LABEL_PREFIX = "СИМУЛЯТОР"


@dataclass
class SimStudent:
    student_id: str
    label: str
    behaviour: str = "success"
    delay_s: float = 0.6
    reconnect_s: float = 8.0
    connected: bool = False
    locked: bool = False
    exam_state: str = "idle"
    policy: dict[str, Any] | None = None
    clock_offset_s: float = 0.0  # simulated wrong clock on the student's computer
    done: dict[str, dict[str, Any]] = field(default_factory=dict)  # command_id -> ack sent (dedup)
    outbox: list[dict[str, Any]] = field(default_factory=list)  # queued while offline (acks)
    received_local: dict[str, datetime] = field(default_factory=dict)
    server_offset: timedelta = timedelta(0)
    log: list[str] = field(default_factory=list)


class SimulatedClass:
    """Transport + fake students. Drive it with step() (devserver loop or tests with FakeClock)."""

    def __init__(self, clock: Clock, *, latency_s: float = 0.05, status_every_s: float = 2.0):
        self.clock = clock
        self.latency = timedelta(seconds=latency_s)
        self.status_every = timedelta(seconds=status_every_s)
        self._lock = threading.RLock()
        self._events: list[tuple[datetime, int, Callable[[], None]]] = []
        self._counter = itertools.count()
        self.students: dict[str, SimStudent] = {}
        self.control = None  # ClassControl, set by attach()
        self.exam_id: str | None = None
        self._next_status: dict[str, datetime] = {}

    # ------------------------------------------------------------------ wiring
    def attach(self, control, exam_id: str | None = None) -> None:
        self.control = control
        self.exam_id = exam_id

    def add_student(self, student_id: str, name: str, behaviour: str = "success", *, connect: bool = True, **kw: Any) -> SimStudent:
        if behaviour not in BEHAVIOURS:
            raise ValueError(f"unknown behaviour {behaviour}")
        with self._lock:
            st = SimStudent(student_id, f"{LABEL_PREFIX}: {name}", behaviour, **kw)
            self.students[student_id] = st
        if connect and behaviour != "offline":
            self._connect(st)
        return st

    def set_behaviour(self, student_id: str, behaviour: str) -> None:
        if behaviour not in BEHAVIOURS:
            raise ValueError(f"unknown behaviour {behaviour}")
        st = self.students[student_id]
        old = st.behaviour
        st.behaviour = behaviour
        if behaviour == "offline" and st.connected:
            self._disconnect(st)
        elif old == "offline" and behaviour != "offline" and not st.connected:
            self._connect(st)
        elif {old, behaviour} & {"unsupported", "v1"} and st.connected:
            # capabilities are sent in hello: reconnect so the server sees the new ones
            self._disconnect(st)
            self._connect(st)

    def set_online(self, student_id: str, online: bool) -> None:
        st = self.students[student_id]
        if online and not st.connected:
            if st.behaviour == "offline":
                st.behaviour = "success"
            self._connect(st)
        elif not online and st.connected:
            self._disconnect(st)

    # ------------------------------------------------------------------ Transport
    def send(self, student_id: str, message: dict[str, Any]) -> bool:
        with self._lock:
            st = self.students.get(student_id)
            if st is None or not st.connected:
                return False
            self._at(self.clock.now() + self.latency, lambda: self._receive(st, message))
            return True

    # ------------------------------------------------------------------ scheduler
    def _at(self, when: datetime, fn: Callable[[], None]) -> None:
        heapq.heappush(self._events, (when, next(self._counter), fn))

    def step(self) -> int:
        """Run due events and periodic status messages. Returns the number of events run."""
        n = 0
        while True:
            with self._lock:
                if not self._events or self._events[0][0] > self.clock.now():
                    break
                _, _, fn = heapq.heappop(self._events)
            fn()
            n += 1
        now = self.clock.now()
        for st in list(self.students.values()):
            if st.connected and now >= self._next_status.get(st.student_id, now):
                self._send_status(st)
        return n

    # ------------------------------------------------------------------ client behaviour
    def _hello(self, st: SimStudent) -> dict[str, Any]:
        hello: dict[str, Any] = {"type": "hello", "v": 1, "protocol": "qorgau.class.v1", "computer_name": f"SIM-{st.student_id}",
                                 "student_label": st.label, "app_version": "simulator-1"}
        if st.behaviour == "unsupported":
            hello["capabilities"] = {**FULL_CAPS, "commands": ["start_exam", "finish_exam"], "modes": ["url"]}
        elif st.behaviour != "v1":
            hello["capabilities"] = dict(FULL_CAPS)
        return hello

    def _connect(self, st: SimStudent) -> None:
        st.connected = True
        st.log.append(f"{iso(self.clock.now())} connected")
        if self.control is not None:
            block = self.control.student_connected(st.student_id, self._hello(st), self.exam_id, simulated=True)
            st.server_offset = timedelta(0)  # welcome.server_time == simulated server clock
            if block:
                st.policy = block
            for msg in st.outbox:  # v1 §3.1: queued acks are sent after reconnect, in order
                self.control.handle_student_message(st.student_id, msg)
            st.outbox.clear()
        self._send_status(st)

    def _disconnect(self, st: SimStudent) -> None:
        st.connected = False
        st.log.append(f"{iso(self.clock.now())} disconnected")
        if self.control is not None:
            self.control.student_disconnected(st.student_id)

    def _to_server(self, st: SimStudent, msg: dict[str, Any]) -> None:
        msg = {"v": 1, "msg_id": str(uuid.uuid4()), "sent_at": iso(self.clock.now()), **msg}
        if st.connected and self.control is not None:
            self.control.handle_student_message(st.student_id, msg)
        elif msg["type"] == "ack":
            st.outbox.append(msg)

    def _send_status(self, st: SimStudent) -> None:
        self._next_status[st.student_id] = self.clock.now() + self.status_every
        self._to_server(st, {"type": "status", "exam_state": st.exam_state, "camera": "ok", "monitoring": "ok",
                             "zone": "green", "zone_reasons_ru": [], "incidents_total": 0,
                             "incidents_by_priority": {"low": 0, "medium": 0, "high": 0}, "locked": st.locked, "mic_active": False})

    def _expired(self, st: SimStudent, msg: dict[str, Any]) -> bool:
        cid = msg["command_id"]
        local_now = self.clock.now() + timedelta(seconds=st.clock_offset_s)
        received = st.received_local.get(cid, local_now)
        ttl = msg.get("ttl_ms")
        if isinstance(ttl, int) and local_now - received > timedelta(milliseconds=ttl):
            return True
        exp = parse_iso(str(msg.get("expires_at") or ""))
        server_now = local_now - timedelta(seconds=st.clock_offset_s) + st.server_offset
        return exp is not None and server_now > exp

    def _receive(self, st: SimStudent, msg: dict[str, Any]) -> None:
        if not st.connected or msg.get("type") != "command":
            return
        cid = msg["command_id"]
        st.log.append(f"{iso(self.clock.now())} got {msg['kind']} {cid} attempt {msg.get('attempt')}")
        if cid in st.done:  # re-delivery: never execute twice, repeat the stored answer
            self._to_server(st, dict(st.done[cid]))
            return
        st.received_local.setdefault(cid, self.clock.now() + timedelta(seconds=st.clock_offset_s))
        b = st.behaviour
        if b == "no_ack":
            return
        if b == "disconnect_before_exec":
            self._disconnect(st)
            st.behaviour = "success"  # the next delivery succeeds
            self._at(self.clock.now() + timedelta(seconds=st.reconnect_s), lambda: self._connect(st))
            return
        if self._expired(st, msg):
            self._finish(st, msg, ok=False, code="expired", error_ru="СИМУЛЯТОР: срок действия команды истёк — не выполнена")
            return
        if b != "v1":
            self._to_server(st, {"type": "command_progress", "command_id": cid, "state": "received"})
        delay = {"delay": 12.0}.get(b, st.delay_s)
        if b == "disconnect_after_exec":
            self._execute(st, msg)
            st.behaviour = "success"
            self._disconnect(st)
            self._finish(st, msg, ok=True)  # goes to the outbox (offline)
            self._at(self.clock.now() + timedelta(seconds=st.reconnect_s), lambda: self._connect(st))
            return
        self._at(self.clock.now() + timedelta(seconds=delay), lambda: self._complete(st, msg))

    def _complete(self, st: SimStudent, msg: dict[str, Any]) -> None:
        if st.behaviour == "error":
            self._finish(st, msg, ok=False, code="failed", error_ru="СИМУЛЯТОР: ошибка выполнения (пример ошибки клиента)")
            return
        kind = msg["kind"]
        if st.behaviour == "unsupported" and kind not in ("start_exam", "finish_exam"):
            self._finish(st, msg, ok=False, code="unsupported", error_ru="СИМУЛЯТОР: действие не поддерживается")
            return
        if self._expired(st, msg):
            self._finish(st, msg, ok=False, code="expired", error_ru="СИМУЛЯТОР: срок действия команды истёк — не выполнена")
            return
        self._execute(st, msg)
        self._finish(st, msg, ok=True)

    def _execute(self, st: SimStudent, msg: dict[str, Any]) -> None:
        kind, payload = msg["kind"], msg.get("payload") or {}
        if kind == "lock":
            st.locked = True
        elif kind == "unlock":
            st.locked = False
        elif kind == "start_exam":
            st.exam_state = "running"
        elif kind == "finish_exam":
            st.exam_state, st.locked = "finished", False
        elif kind == "apply_policy":
            st.policy = dict(payload)
        st.log.append(f"{iso(self.clock.now())} executed {kind}")

    def _finish(self, st: SimStudent, msg: dict[str, Any], *, ok: bool, code: str | None = None, error_ru: str | None = None) -> None:
        result: dict[str, Any] = {"locked": st.locked, "exam_state": st.exam_state}
        if msg["kind"] == "apply_policy" and ok:
            result.update(policy_id=(msg.get("payload") or {}).get("policy_id"), policy_version=(msg.get("payload") or {}).get("version"))
        ack: dict[str, Any] = {"type": "ack", "command_id": msg["command_id"], "ok": ok, "result": result,
                               "executed_at": iso(self.clock.now()) if ok else None}
        if code:
            ack["code"] = code
        if error_ru:
            ack["error_ru"] = error_ru
        st.done[msg["command_id"]] = ack
        self._to_server(st, ack)
        if st.connected:
            self._send_status(st)

    def describe(self) -> list[dict[str, Any]]:
        return [{"student_id": s.student_id, "label": s.label, "behaviour": s.behaviour, "connected": s.connected,
                 "locked": s.locked, "exam_state": s.exam_state, "log": s.log[-8:]} for s in self.students.values()]
