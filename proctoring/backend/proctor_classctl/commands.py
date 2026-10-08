"""Teacher -> student commands with delivery tracking (qorgau.class.v1 `command` / `ack`).

Rules (normative for T04, mirrored in handoffs/T04/STUDENT_CLIENT.md):
  * Every command has a server-generated `command_id`, `issued_at`, `expires_at` (UTC) and, in each
    delivery, `ttl_ms` = time left. The server NEVER sends a command after `expires_at`; a command
    still undelivered at expiry becomes `expired` and is not executed later.
  * A command is `succeeded` only after the client's `ack{ok:true}`. Server acceptance is `queued`.
  * Re-delivery after a reconnect reuses the same `command_id` (client must de-duplicate).
  * One final ack wins; duplicates and acks for unknown commands are journaled and ignored.
  * Teacher requests are idempotent by (teacher, idempotency_key, student).
  * A newer lock/unlock (start/finish, policy) replaces older ones that were not sent yet.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Callable, Protocol

from .access import Principal
from .capabilities import KIND_LABEL_RU
from .clock import Clock, iso, parse_iso
from .journal import Journal

ACK_TIMEOUT_S = 10.0  # v1: "Нет ack 10 с -> команда не подтверждена"
EXEC_TIMEOUT_S = 60.0  # after command_progress{executing}
TTL_DEFAULT_S = {"lock": 120.0, "unlock": 1800.0, "start_exam": 600.0, "finish_exam": 1800.0, "apply_policy": 1800.0}
TTL_MIN_S, TTL_MAX_S = 10.0, 3600.0
GROUP = {"lock": "lock", "unlock": "lock", "start_exam": "exam", "finish_exam": "exam", "apply_policy": "policy"}
REJECT_CODES = {"unsupported", "invalid", "expired", "duplicate"}  # refused before execution


class CmdState(StrEnum):
    QUEUED = "queued"  # accepted by the server, not delivered yet
    SENT = "sent"  # written to the student's open connection, no answer yet
    EXECUTING = "executing"  # client reported received/executing
    SUCCEEDED = "succeeded"  # client ack ok
    FAILED = "failed"  # client ack not ok (execution failed)
    REJECTED = "rejected"  # client refused (unsupported / invalid / expired)
    EXPIRED = "expired"  # never delivered before expires_at
    UNCONFIRMED = "unconfirmed"  # sent while connected, no ack in time: outcome unknown
    LOST = "lost"  # connection dropped after sending: outcome unknown
    SUPERSEDED = "superseded"  # replaced by a newer command before delivery
    CANCELLED = "cancelled"  # cancelled by a teacher before delivery


FINAL = frozenset({CmdState.SUCCEEDED, CmdState.FAILED, CmdState.REJECTED, CmdState.EXPIRED, CmdState.SUPERSEDED, CmdState.CANCELLED})
OPEN_UNSENT = frozenset({CmdState.QUEUED})
IN_FLIGHT = frozenset({CmdState.SENT, CmdState.EXECUTING})
UNKNOWN_OUTCOME = frozenset({CmdState.UNCONFIRMED, CmdState.LOST})


class Transport(Protocol):
    def send(self, student_id: str, message: dict[str, Any]) -> bool:
        """Write one JSON message to the student's open WebSocket. False if not connected."""
        ...


class CommandConflict(Exception):
    def __init__(self, message_ru: str):
        super().__init__(message_ru)
        self.message_ru = message_ru


@dataclass
class Ack:
    ok: bool
    code: str | None
    error_ru: str | None
    result: dict[str, Any]
    executed_at: str | None
    received_at: str
    late: bool


@dataclass
class Command:
    command_id: str
    seq: int
    exam_id: str
    student_id: str
    kind: str
    payload: dict[str, Any]
    issued_by: str
    issued_by_name: str
    issued_at: datetime
    expires_at: datetime
    idempotency_key: str | None
    state: CmdState = CmdState.QUEUED
    history: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    sent_at: datetime | None = None
    deadline: datetime | None = None
    ack: Ack | None = None
    superseded_by: str | None = None
    note_ru: str | None = None
    outcome_was_unknown: bool = False  # was lost/unconfirmed at some point (a later ack is "late")

    @property
    def final(self) -> bool:
        return self.state in FINAL

    def expired_at(self, now: datetime) -> bool:
        return now >= self.expires_at


def display(cmd: Command, connected: bool, now: datetime) -> dict[str, str]:
    """The five teacher-facing groups: pending / executing / done / error / no_connection
    (+ cancelled for replaced or cancelled commands), with an exact Russian label."""
    s = cmd.state
    until = cmd.expires_at.strftime("%H:%M:%S")
    if s == CmdState.QUEUED:
        if connected:
            return {"group": "pending", "label_ru": "Ожидает доставки"}
        return {"group": "no_connection", "label_ru": f"Нет связи — будет доставлена при подключении до {until} UTC"}
    if s == CmdState.SENT:
        return {"group": "pending", "label_ru": "Ожидает доставки: отправлена, ждём ответа клиента"}
    if s == CmdState.EXECUTING:
        return {"group": "executing", "label_ru": "Выполняется"}
    if s == CmdState.SUCCEEDED:
        late = " (подтверждено после восстановления связи)" if cmd.ack and cmd.ack.late else ""
        return {"group": "done", "label_ru": f"Выполнено{late}"}
    if s == CmdState.FAILED:
        why = cmd.ack.error_ru if cmd.ack and cmd.ack.error_ru else "клиент сообщил об ошибке"
        return {"group": "error", "label_ru": f"Ошибка: {why}"}
    if s == CmdState.REJECTED:
        code = cmd.ack.code if cmd.ack else None
        why = {
            "unsupported": "клиент не поддерживает это действие",
            "invalid": "клиент счёл команду некорректной",
            "expired": "срок действия истёк до выполнения",
            "duplicate": "повтор уже выполненной команды",
        }.get(code or "", (cmd.ack.error_ru if cmd.ack and cmd.ack.error_ru else "отклонено клиентом"))
        return {"group": "error", "label_ru": f"Ошибка: {why}"}
    if s == CmdState.EXPIRED:
        return {"group": "error", "label_ru": "Ошибка: срок действия истёк — команда не доставлена и не будет выполнена"}
    if s == CmdState.UNCONFIRMED:
        return {"group": "no_connection", "label_ru": f"Нет ответа клиента за {int(ACK_TIMEOUT_S)} с — результат неизвестен"}
    if s == CmdState.LOST:
        tail = " (повторно не отправлялась: срок истёк)" if cmd.expired_at(now) else " (будет отправлена повторно при подключении)"
        return {"group": "no_connection", "label_ru": f"Нет связи после отправки — результат неизвестен{tail}"}
    if s == CmdState.SUPERSEDED:
        return {"group": "cancelled", "label_ru": "Заменена более новой командой (не отправлялась повторно)"}
    return {"group": "cancelled", "label_ru": "Отменена преподавателем до доставки"}


class CommandService:
    def __init__(
        self,
        clock: Clock,
        journal: Journal,
        transport: Transport,
        is_connected: Callable[[str], bool],
        *,
        ack_timeout_s: float = ACK_TIMEOUT_S,
        exec_timeout_s: float = EXEC_TIMEOUT_S,
        on_change: Callable[[Command], None] | None = None,
    ):
        self._clock = clock
        self._journal = journal
        self._transport = transport
        self._is_connected = is_connected
        self._ack_timeout = timedelta(seconds=ack_timeout_s)
        self._exec_timeout = timedelta(seconds=exec_timeout_s)
        self._on_change = on_change
        self._lock = threading.RLock()
        self._commands: dict[str, Command] = {}
        self._by_student: dict[str, list[str]] = {}
        self._idem: dict[tuple[str, str, str], str] = {}
        self._seq = 0

    # ------------------------------------------------------------------ queries
    def get(self, command_id: str) -> Command | None:
        with self._lock:
            return self._commands.get(command_id)

    def for_student(self, student_id: str) -> list[Command]:
        with self._lock:
            return [self._commands[c] for c in self._by_student.get(student_id, [])]

    def for_exam(self, exam_id: str) -> list[Command]:
        with self._lock:
            return [c for c in self._commands.values() if c.exam_id == exam_id]

    # ------------------------------------------------------------------ issue
    def issue(
        self,
        principal: Principal,
        exam_id: str,
        student_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None,
        ttl_s: float | None = None,
    ) -> tuple[Command, bool]:
        """Returns (command, created). created=False: same idempotency key seen before."""
        now = self._clock.now()
        with self._lock:
            if idempotency_key:
                key = (principal.teacher_id, idempotency_key, student_id)
                prev_id = self._idem.get(key)
                if prev_id is not None:
                    prev = self._commands[prev_id]
                    if prev.kind != kind or prev.payload != payload or prev.exam_id != exam_id:
                        raise CommandConflict("Этот ключ повтора уже использован для другой команды")
                    self._journal.write(
                        "command_duplicate_request", actor_id=principal.teacher_id, actor_name=principal.display_name,
                        exam_id=exam_id, student_ids=[student_id], command_id=prev.command_id, kind=kind,
                    )
                    return prev, False
            ttl = TTL_DEFAULT_S.get(kind, 300.0) if ttl_s is None else float(ttl_s)
            ttl = min(TTL_MAX_S, max(TTL_MIN_S, ttl))
            self._seq += 1
            cmd = Command(
                command_id=f"cmd-{uuid.uuid4().hex}",
                seq=self._seq,
                exam_id=exam_id,
                student_id=student_id,
                kind=kind,
                payload=dict(payload),
                issued_by=principal.teacher_id,
                issued_by_name=principal.display_name,
                issued_at=now,
                expires_at=now + timedelta(seconds=ttl),
                idempotency_key=idempotency_key,
            )
            self._commands[cmd.command_id] = cmd
            self._by_student.setdefault(student_id, []).append(cmd.command_id)
            if idempotency_key:
                self._idem[(principal.teacher_id, idempotency_key, student_id)] = cmd.command_id
            self._set(cmd, CmdState.QUEUED, "принята сервером")
            self._journal.write(
                "command_issued", actor_id=principal.teacher_id, actor_name=principal.display_name, exam_id=exam_id,
                student_ids=[student_id], command_id=cmd.command_id, kind=kind,
                kind_ru=KIND_LABEL_RU.get(kind, kind), reason_ru=payload.get("reason_ru"),
                policy_id=payload.get("policy_id"), policy_version=payload.get("version"),
                expires_at=iso(cmd.expires_at),
            )
            self._supersede_older(cmd)
            self._try_send(cmd, now)
            return cmd, True

    def _supersede_older(self, cmd: Command) -> None:
        group = GROUP.get(cmd.kind)
        for cid in self._by_student.get(cmd.student_id, []):
            old = self._commands[cid]
            if old is cmd or GROUP.get(old.kind) != group or old.seq > cmd.seq:
                continue
            if old.state == CmdState.QUEUED or old.state == CmdState.LOST:
                old.superseded_by = cmd.command_id
                was_lost = old.state == CmdState.LOST
                self._set(old, CmdState.SUPERSEDED, f"заменена командой {cmd.command_id}"
                          + ("; результат прежней отправки неизвестен" if was_lost else ""))

    def cancel(self, principal: Principal, command_id: str) -> Command:
        with self._lock:
            cmd = self._commands.get(command_id)
            if cmd is None:
                raise KeyError(command_id)
            if cmd.state != CmdState.QUEUED:
                raise CommandConflict("Отменить можно только команду, которая ещё не доставлена")
            self._set(cmd, CmdState.CANCELLED, f"отменена: {principal.display_name}")
            self._journal.write(
                "command_cancelled", actor_id=principal.teacher_id, actor_name=principal.display_name,
                exam_id=cmd.exam_id, student_ids=[cmd.student_id], command_id=cmd.command_id, kind=cmd.kind,
            )
            return cmd

    # ------------------------------------------------------------------ delivery
    def _message(self, cmd: Command, now: datetime) -> dict[str, Any]:
        return {
            "type": "command",
            "v": 1,
            "msg_id": str(uuid.uuid4()),
            "sent_at": iso(now),
            "command_id": cmd.command_id,
            "kind": cmd.kind,
            "payload": cmd.payload,
            # additive to qorgau.class.v1 (requested from T01, see handoffs/T04/DEPENDENCIES.txt)
            "issued_at": iso(cmd.issued_at),
            "expires_at": iso(cmd.expires_at),
            "ttl_ms": max(0, int((cmd.expires_at - now).total_seconds() * 1000)),
            "attempt": cmd.attempts + 1,
        }

    def _try_send(self, cmd: Command, now: datetime) -> None:
        if cmd.state not in (CmdState.QUEUED, CmdState.LOST):
            return
        if cmd.expired_at(now):
            if cmd.state == CmdState.QUEUED:
                self._set(cmd, CmdState.EXPIRED, "срок истёк до доставки")
            return
        if not self._is_connected(cmd.student_id):
            return
        resend = cmd.state == CmdState.LOST
        if not self._transport.send(cmd.student_id, self._message(cmd, now)):
            return
        cmd.attempts += 1
        cmd.sent_at = now
        cmd.deadline = now + self._ack_timeout
        self._set(cmd, CmdState.SENT, "повторная отправка после переподключения" if resend else "отправлена клиенту")

    def deliver_pending(self, student_id: str) -> None:
        """After (re)connect: send everything still valid, oldest first."""
        now = self._clock.now()
        with self._lock:
            for cid in list(self._by_student.get(student_id, [])):
                self._try_send(self._commands[cid], now)

    def connection_lost(self, student_id: str) -> None:
        with self._lock:
            for cid in self._by_student.get(student_id, []):
                cmd = self._commands[cid]
                if cmd.state in IN_FLIGHT or cmd.state == CmdState.UNCONFIRMED:
                    self._set(cmd, CmdState.LOST, "связь со студентом потеряна до подтверждения")

    # ------------------------------------------------------------------ client messages
    def handle_ack(self, student_id: str, msg: dict[str, Any]) -> None:
        now = self._clock.now()
        command_id = str(msg.get("command_id") or "")
        with self._lock:
            cmd = self._commands.get(command_id)
            if cmd is None or cmd.student_id != student_id:
                self._journal.write("ack_unknown", student_ids=[student_id], command_id=command_id or None)
                return
            ok = msg.get("ok") is True
            code = msg.get("code") if isinstance(msg.get("code"), str) else None
            error_ru = str(msg.get("error_ru"))[:300] if msg.get("error_ru") else None
            result = msg.get("result") if isinstance(msg.get("result"), dict) else {}
            executed_at = msg.get("executed_at") if parse_iso(str(msg.get("executed_at") or "")) else None
            if cmd.ack is not None or cmd.state in (CmdState.SUCCEEDED, CmdState.FAILED, CmdState.REJECTED):
                self._journal.write(
                    "ack_duplicate", student_ids=[student_id], exam_id=cmd.exam_id, command_id=cmd.command_id,
                    kind=cmd.kind, ok=ok, first_ok=cmd.ack.ok if cmd.ack else None,
                )
                return
            late = cmd.outcome_was_unknown or cmd.state in UNKNOWN_OUTCOME
            cmd.ack = Ack(ok, code, error_ru, result, executed_at, iso(now), late)
            if cmd.state in (CmdState.SUPERSEDED, CmdState.CANCELLED, CmdState.EXPIRED):
                # outcome of an earlier delivery arrived after the command was replaced: keep the state,
                # record what the client did
                cmd.note_ru = "клиент сообщил: выполнено" if ok else f"клиент сообщил: не выполнено ({code or error_ru or 'ошибка'})"
                self._emit(cmd)
                self._journal.write(
                    "command_state", student_ids=[student_id], exam_id=cmd.exam_id, command_id=cmd.command_id,
                    kind=cmd.kind, state=cmd.state.value, late_ack_ok=ok, code=code,
                )
                return
            if ok:
                self._set(cmd, CmdState.SUCCEEDED, "подтверждено клиентом" + (" с опозданием" if late else ""))
            elif code in REJECT_CODES:
                self._set(cmd, CmdState.REJECTED, f"отклонено клиентом: {code}")
            else:
                self._set(cmd, CmdState.FAILED, error_ru or code or "ошибка на клиенте")

    def handle_progress(self, student_id: str, msg: dict[str, Any]) -> None:
        now = self._clock.now()
        with self._lock:
            cmd = self._commands.get(str(msg.get("command_id") or ""))
            if cmd is None or cmd.student_id != student_id:
                return
            if cmd.state in (CmdState.SENT, CmdState.UNCONFIRMED, CmdState.LOST) and msg.get("state") in ("received", "executing"):
                cmd.deadline = now + self._exec_timeout
                self._set(cmd, CmdState.EXECUTING, "клиент получил команду и выполняет")

    # ------------------------------------------------------------------ time
    def tick(self) -> None:
        now = self._clock.now()
        with self._lock:
            for cmd in list(self._commands.values()):
                if cmd.state == CmdState.QUEUED and cmd.expired_at(now):
                    self._set(cmd, CmdState.EXPIRED, "срок истёк до доставки")
                elif cmd.state in IN_FLIGHT and cmd.deadline is not None and now >= cmd.deadline:
                    self._set(cmd, CmdState.UNCONFIRMED, "нет подтверждения клиента в отведённое время")

    # ------------------------------------------------------------------ internals
    def _set(self, cmd: Command, state: CmdState, detail_ru: str) -> None:
        prev = cmd.state if cmd.history else None
        cmd.state = state
        if state in UNKNOWN_OUTCOME:
            cmd.outcome_was_unknown = True
        cmd.history.append({"state": state.value, "at": iso(self._clock.now()), "detail_ru": detail_ru})
        if prev is not None:
            self._journal.write(
                "command_state", exam_id=cmd.exam_id, student_ids=[cmd.student_id], command_id=cmd.command_id,
                kind=cmd.kind, state=state.value, previous=prev.value, detail_ru=detail_ru,
                code=cmd.ack.code if cmd.ack else None,
            )
        self._emit(cmd)

    def _emit(self, cmd: Command) -> None:
        if self._on_change is not None:
            try:
                self._on_change(cmd)
            except Exception:  # a listener must never break command handling
                pass
