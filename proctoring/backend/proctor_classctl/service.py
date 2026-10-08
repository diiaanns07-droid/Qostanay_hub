"""ClassControl: the single object the class server (T01/C1) talks to for T04 features.

Server -> ClassControl (from its WebSocket handlers, any thread):
    exam_block = control.student_connected(student_id, hello, exam_id)   # include in `welcome.exam`
    control.handle_student_message(student_id, msg)   # `ack`, `status`, `command_progress`
    control.student_disconnected(student_id)
    control.tick()                                    # every ~500 ms
Teacher API -> ClassControl via api.create_teacher_router().
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .access import AccessDenied, Action, Principal
from .capabilities import KIND_LABEL_RU, T04_KINDS, UNKNOWN, ClientCapabilities
from .clock import Clock, SystemClock, iso
from .commands import ACK_TIMEOUT_S, GROUP, TTL_DEFAULT_S, CmdState, Command, CommandConflict, CommandService, Transport, display
from .exams import Exam, ExamService
from .journal import Journal
from .policies import AUTH_DOMAINS_NOTE_RU, MODE_LABEL_RU, SSO_SUGGESTIONS, TIMER_NOTE_RU, PolicyError

REASON_MAX = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
TEACHER_KINDS = ("start_exam", "lock", "unlock", "finish_exam")  # sent through /commands
MAX_BULK = 200


@dataclass
class StudentState:
    student_id: str
    exam_id: str | None
    label: str
    computer_name: str
    connected: bool = False
    capabilities: ClientCapabilities = UNKNOWN
    status: dict[str, Any] | None = None
    status_at: datetime | None = None
    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
    simulated: bool = False
    applied_policy: dict[str, Any] | None = None  # {policy_id, version, via, confirmed, at}
    history: list[str] = field(default_factory=list)


class ClassControl:
    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock | None = None,
        journal_path: Path | None = None,
        ack_timeout_s: float = ACK_TIMEOUT_S,
        on_change: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.clock = clock or SystemClock()
        self.journal = Journal(self.clock, journal_path)
        self._lock = threading.RLock()
        self._students: dict[str, StudentState] = {}
        self._on_change = on_change
        self.exams = ExamService(self.clock, self.journal)
        self.commands = CommandService(
            self.clock, self.journal, transport, self.is_connected, ack_timeout_s=ack_timeout_s, on_change=self._command_changed
        )

    # ================================================================== server side
    def is_connected(self, student_id: str) -> bool:
        with self._lock:
            st = self._students.get(student_id)
            return bool(st and st.connected)

    def student_connected(self, student_id: str, hello: dict[str, Any], exam_id: str | None, *, simulated: bool = False) -> dict[str, Any] | None:
        """Register a (re)connected student. Returns the `exam` block for `welcome` (policy assigned
        to this student) or None when the student is not attached to an exam."""
        now = self.clock.now()
        with self._lock:
            st = self._students.get(student_id)
            label = str(hello.get("student_label") or "")[:64] or student_id
            computer = str(hello.get("computer_name") or "")[:64]
            if st is None:
                st = self._students[student_id] = StudentState(student_id, exam_id, label, computer, simulated=simulated)
            st.label, st.computer_name, st.simulated = label, computer, simulated
            st.capabilities = ClientCapabilities.from_hello(hello)
            st.connected, st.connected_at = True, now
            st.status, st.status_at = None, None  # a fresh status is required after every (re)connect
            block = None
            if exam_id is not None:
                exam = self.exams.add_student(exam_id, student_id)
                st.exam_id = exam_id
                pol = exam.policy_for(student_id)
                block = {"exam_id": exam.exam_id, "title": exam.title, **pol.client_payload()}
                st.applied_policy = {"policy_id": pol.policy_id, "version": pol.version, "via": "welcome",
                                     "confirmed": False, "at": iso(now)}
        self.commands.deliver_pending(student_id)
        self._changed(student_id)
        return block

    def student_disconnected(self, student_id: str) -> None:
        with self._lock:
            st = self._students.get(student_id)
            if st is None or not st.connected:
                return
            st.connected, st.disconnected_at = False, self.clock.now()
        self.commands.connection_lost(student_id)
        self._changed(student_id)

    def handle_student_message(self, student_id: str, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "ack":
            self.commands.handle_ack(student_id, msg)
        elif kind == "command_progress":
            self.commands.handle_progress(student_id, msg)
        elif kind == "status":
            with self._lock:
                st = self._students.get(student_id)
                if st is None:
                    return
                st.status = {k: msg.get(k) for k in ("exam_state", "camera", "monitoring", "locked", "zone")}
                st.status_at = self.clock.now()
            self._changed(student_id)

    def tick(self) -> None:
        self.commands.tick()

    # ================================================================== teacher side
    def meta(self) -> dict[str, Any]:
        return {
            "kinds": {k: KIND_LABEL_RU[k] for k in T04_KINDS},
            "modes": {k.value: v for k, v in MODE_LABEL_RU.items()},
            "ttl_default_s": TTL_DEFAULT_S,
            "ack_timeout_s": ACK_TIMEOUT_S,
            "auth_domains_note_ru": AUTH_DOMAINS_NOTE_RU,
            "sso_suggestions": SSO_SUGGESTIONS,
            "site_timer_note_ru": TIMER_NOTE_RU,
            "reason_max": REASON_MAX,
            "status_groups_ru": {
                "pending": "ожидает доставки",
                "executing": "выполняется",
                "done": "выполнено",
                "error": "ошибка",
                "no_connection": "нет связи",
                "cancelled": "отменена",
            },
        }

    def _student(self, student_id: str) -> StudentState | None:
        with self._lock:
            return self._students.get(student_id)

    def _require_students(self, exam: Exam, student_ids: list[str]) -> list[str]:
        ids = list(dict.fromkeys(str(s) for s in student_ids))
        if not ids:
            raise PolicyError("student_ids", "empty", "Выберите хотя бы одного студента")
        if len(ids) > MAX_BULK:
            raise PolicyError("student_ids", "too_many", f"Не больше {MAX_BULK} студентов за раз")
        foreign = [s for s in ids if s not in exam.students]
        if foreign:
            raise AccessDenied("not_in_exam", "Студенты не из этого экзамена: " + ", ".join(foreign[:5]))
        return ids

    def availability(self, exam: Exam, student_id: str, kind: str) -> str | None:
        """None = available; otherwise an explicit Russian reason (shown on the disabled button)."""
        st = self._student(student_id)
        caps = st.capabilities if st else UNKNOWN
        if st is None or st.capabilities is UNKNOWN:
            return "Клиент студента ещё не подключался — его возможности неизвестны"
        reason = caps.unsupported_reason_ru(kind)
        if reason:
            return reason
        if kind == "start_exam":
            mode = exam.policy_for(student_id).content.mode.value
            return caps.mode_reason_ru(mode)
        return None

    def send_command(
        self,
        principal: Principal,
        exam_id: str,
        *,
        kind: str,
        student_ids: list[str],
        payload: dict[str, Any] | None,
        idempotency_key: str | None,
        ttl_s: float | None = None,
    ) -> dict[str, Any]:
        if kind not in TEACHER_KINDS:
            raise PolicyError("kind", "invalid_kind", "Неизвестная команда")
        exam = self.exams.get(principal, exam_id, Action.COMMAND)
        ids = self._require_students(exam, student_ids)
        payload = self._validate_payload(kind, payload or {})
        results = []
        for sid in ids:
            reason = self.availability(exam, sid, kind)
            if reason:
                self.journal.write("command_unavailable", actor_id=principal.teacher_id, actor_name=principal.display_name,
                                   exam_id=exam_id, student_ids=[sid], kind=kind, reason_ru=reason)
                results.append({"student_id": sid, "created": False, "unavailable_ru": reason, "command": None})
                continue
            cmd, created = self.commands.issue(principal, exam_id, sid, kind, payload, idempotency_key=idempotency_key, ttl_s=ttl_s)
            results.append({"student_id": sid, "created": created, "unavailable_ru": None, "command": self.command_view(cmd)})
        return {"kind": kind, "results": results}

    def _validate_payload(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind == "lock":
            extra = set(payload) - {"reason_ru"}
            reason = payload.get("reason_ru")
            if extra or not isinstance(reason, str):
                raise PolicyError("payload", "invalid", "Для блокировки нужна причина (reason_ru)")
            reason = _CONTROL_CHARS.sub("", reason).strip()
            if not 1 <= len(reason) <= REASON_MAX:
                raise PolicyError("reason_ru", "invalid", f"Причина блокировки: от 1 до {REASON_MAX} символов")
            return {"reason_ru": reason}
        if payload:
            raise PolicyError("payload", "invalid", "У этой команды нет параметров")
        return {}

    def assign_policy(
        self,
        principal: Principal,
        exam_id: str,
        *,
        policy_id: str,
        student_ids: list[str],
        idempotency_key: str | None,
        deliver_now: bool = True,
    ) -> dict[str, Any]:
        exam = self.exams.get(principal, exam_id, Action.EDIT)
        ids = self._require_students(exam, student_ids)
        pol = exam.policies.get(policy_id)
        if pol is None:
            raise KeyError(policy_id)
        mode = pol.content.mode.value
        blocked = []
        for sid in ids:
            st = self._student(sid)
            reason = st.capabilities.mode_reason_ru(mode) if st else None
            if reason:
                blocked.append({"student_id": sid, "unavailable_ru": reason})
        ok_ids = [s for s in ids if s not in {b["student_id"] for b in blocked}]
        if ok_ids:
            self.exams.assign(principal, exam_id, policy_id, ok_ids)
        results = [{"student_id": b["student_id"], "assigned": False, "unavailable_ru": b["unavailable_ru"], "delivery_ru": None,
                    "command": None} for b in blocked]
        for sid in ok_ids:
            st = self._student(sid)
            caps = st.capabilities if st else UNKNOWN
            entry: dict[str, Any] = {"student_id": sid, "assigned": True, "unavailable_ru": None, "command": None}
            if not deliver_now:
                entry["delivery_ru"] = "Назначена; будет отправлена отдельной командой или при подключении"
            elif caps.supports("apply_policy"):
                cmd, _ = self.commands.issue(principal, exam_id, sid, "apply_policy", pol.client_payload(),
                                             idempotency_key=f"{idempotency_key}:{policy_id}:{pol.version}" if idempotency_key else None)
                entry["command"] = self.command_view(cmd)
                entry["delivery_ru"] = "Отправлена клиенту командой «Применить политику»"
            else:
                entry["delivery_ru"] = caps.unsupported_reason_ru("apply_policy")
            results.append(entry)
            self._changed(sid)
        return {"policy_id": policy_id, "version": pol.version, "results": results}

    # ================================================================== views
    def command_view(self, cmd: Command) -> dict[str, Any]:
        now = self.clock.now()
        st = self._student(cmd.student_id)
        disp = display(cmd, bool(st and st.connected), now)
        ack = None
        if cmd.ack is not None:
            ack = {"ok": cmd.ack.ok, "code": cmd.ack.code, "error_ru": cmd.ack.error_ru, "result": cmd.ack.result,
                   "executed_at": cmd.ack.executed_at, "received_at": cmd.ack.received_at, "late": cmd.ack.late}
        return {
            "command_id": cmd.command_id,
            "seq": cmd.seq,
            "exam_id": cmd.exam_id,
            "student_id": cmd.student_id,
            "student_label": st.label if st else cmd.student_id,
            "kind": cmd.kind,
            "kind_ru": KIND_LABEL_RU.get(cmd.kind, cmd.kind),
            "payload": cmd.payload,
            "issued_by": cmd.issued_by,
            "issued_by_name": cmd.issued_by_name,
            "issued_at": iso(cmd.issued_at),
            "expires_at": iso(cmd.expires_at),
            "state": cmd.state.value,
            "group": disp["group"],
            "label_ru": disp["label_ru"],
            "note_ru": cmd.note_ru,
            "attempts": cmd.attempts,
            "ack": ack,
            "superseded_by": cmd.superseded_by,
            "history": list(cmd.history),
            "cancellable": cmd.state == CmdState.QUEUED,
        }

    def _lock_view(self, st: StudentState) -> dict[str, Any]:
        cmds = [c for c in self.commands.for_student(st.student_id) if GROUP.get(c.kind) == "lock"]
        pending = [c for c in cmds if c.state in (CmdState.QUEUED, CmdState.SENT, CmdState.EXECUTING, CmdState.UNCONFIRMED, CmdState.LOST)]
        if pending:
            last = pending[-1]
            disp = display(last, st.connected, self.clock.now())
            state = "lock_pending" if last.kind == "lock" else "unlock_pending"
            what = "Блокировка" if last.kind == "lock" else "Разблокировка"
            return {"state": state, "label_ru": f"{what}: {disp['label_ru'].lower()}", "source": "command", "command_id": last.command_id}
        if not st.connected:
            known = st.status.get("locked") if st.status else None
            tail = "" if known is None else (" (последнее известное: заблокирован)" if known else " (последнее известное: не заблокирован)")
            return {"state": "unknown", "label_ru": "Нет связи — текущее состояние неизвестно" + tail, "source": None, "command_id": None}
        if st.status is not None and isinstance(st.status.get("locked"), bool):
            locked = st.status["locked"]
            done = [c for c in cmds if c.state == CmdState.SUCCEEDED]
            note = ""
            if done and (done[-1].kind == "lock") != locked and done[-1].ack and st.status_at and done[-1].ack.received_at <= iso(st.status_at):
                note = " — расходится с последней подтверждённой командой"
            label = ("Заблокирован (по статусу клиента)" if locked else "Не заблокирован (по статусу клиента)") + note
            return {"state": "locked" if locked else "unlocked", "label_ru": label, "source": "status", "command_id": None}
        done = [c for c in cmds if c.state == CmdState.SUCCEEDED]
        if done:
            locked = done[-1].kind == "lock"
            label = "Заблокирован (подтверждено клиентом)" if locked else "Разблокирован (подтверждено клиентом)"
            return {"state": "locked" if locked else "unlocked", "label_ru": label, "source": "ack", "command_id": done[-1].command_id}
        return {"state": "unknown", "label_ru": "Состояние неизвестно: клиент ещё не прислал статус", "source": None, "command_id": None}

    def _policy_view(self, exam: Exam, st: StudentState) -> dict[str, Any]:
        pol = exam.policy_for(st.student_id)
        assigned = {"policy_id": pol.policy_id, "name": pol.name, "version": pol.version, "mode": pol.content.mode.value}
        applied = st.applied_policy
        cmds = [c for c in self.commands.for_student(st.student_id) if c.kind == "apply_policy" and c.state == CmdState.SUCCEEDED]
        if cmds:
            last = cmds[-1]
            if applied is None or applied.get("via") == "welcome" or applied.get("at", "") <= (last.ack.received_at if last.ack else ""):
                applied = {"policy_id": last.payload.get("policy_id"), "version": last.payload.get("version"),
                           "via": "command", "confirmed": True, "at": last.ack.received_at if last.ack else None}
        if applied is None:
            label = "Ещё не передана клиенту"
        elif applied["policy_id"] == pol.policy_id and applied["version"] == pol.version:
            label = "Применена, подтверждено клиентом" if applied["confirmed"] else "Передана при подключении (клиент не подтверждает отдельно)"
        else:
            label = f"Назначена «{pol.name}» v{pol.version}, у клиента — v{applied['version']}" + (
                "" if applied["policy_id"] == pol.policy_id else " другой политики")
        return {"assigned": assigned, "applied": applied, "label_ru": label}

    def student_views(self, principal: Principal, exam_id: str) -> list[dict[str, Any]]:
        exam = self.exams.get(principal, exam_id, Action.VIEW)
        out = []
        for sid in exam.students:
            st = self._student(sid)
            if st is None:
                continue
            last_cmds = self.commands.for_student(sid)[-5:]
            out.append({
                "student_id": sid,
                "label": st.label,
                "computer_name": st.computer_name,
                "simulated": st.simulated,
                "connected": st.connected,
                "connected_at": iso(st.connected_at) if st.connected_at else None,
                "disconnected_at": iso(st.disconnected_at) if st.disconnected_at else None,
                "capabilities": st.capabilities.to_dict(),
                "status": st.status,
                "status_at": iso(st.status_at) if st.status_at else None,
                "lock": self._lock_view(st),
                "policy": self._policy_view(exam, st),
                "actions": {k: {"available": (r := self.availability(exam, sid, k)) is None, "reason_ru": r} for k in TEACHER_KINDS},
                "site_timer_ru": TIMER_NOTE_RU if not st.capabilities.site_timer_pause else
                "Клиент сообщает о паузе таймера сайта, но интеграция с сайтом не настроена — пауза не выполняется",
                "commands": [self.command_view(c) for c in reversed(last_cmds)],
            })
        return out

    def command_list(self, principal: Principal, exam_id: str, student_id: str | None = None) -> list[dict[str, Any]]:
        self.exams.get(principal, exam_id, Action.VIEW)
        cmds = self.commands.for_student(student_id) if student_id else self.commands.for_exam(exam_id)
        return [self.command_view(c) for c in sorted(cmds, key=lambda c: -c.seq) if c.exam_id == exam_id]

    def cancel_command(self, principal: Principal, exam_id: str, command_id: str) -> dict[str, Any]:
        self.exams.get(principal, exam_id, Action.COMMAND)
        cmd = self.commands.get(command_id)
        if cmd is None or cmd.exam_id != exam_id:
            raise KeyError(command_id)
        return self.command_view(self.commands.cancel(principal, command_id))

    def journal_view(self, principal: Principal, exam_id: str, *, student_id: str | None = None,
                     since_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        self.exams.get(principal, exam_id, Action.VIEW)
        labels = {s: (st.label if (st := self._student(s)) else s) for s in self.exams.raw(exam_id).students}
        out = []
        for e in self.journal.query(exam_id=exam_id, student_id=student_id, since_seq=since_seq, limit=limit):
            d = e.to_dict()
            d["student_labels"] = [labels.get(s, s) for s in e.student_ids]
            out.append(d)
        return out

    # ================================================================== change notifications
    def _command_changed(self, cmd: Command) -> None:
        self._changed(cmd.student_id)

    def _changed(self, student_id: str) -> None:
        if self._on_change is not None:
            try:
                self._on_change("student", {"student_id": student_id})
            except Exception:
                pass


__all__ = ["ClassControl", "CommandConflict", "StudentState"]
