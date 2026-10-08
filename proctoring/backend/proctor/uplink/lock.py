"""Teacher app-overlay commands require a scoped receipt from the local UI.

This proves only the Adal overlay, never an operating-system lockdown. The API
credential stays in Electron main. Pending intent and effective state are distinct.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .outbox import Outbox

_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SCOPE_KEYS = ("student_id", "class_session_id", "source_session_id", "backend_instance_id")
RECEIPT_KEYS = (*SCOPE_KEYS, "command_id", "request_token", "locked")


class LockReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    student_id: str = Field(min_length=1, max_length=128)
    class_session_id: str | None
    source_session_id: str | None
    backend_instance_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    request_token: str = Field(min_length=1, max_length=128)
    locked: StrictBool
    applied: StrictBool
    reason_ru: str | None = Field(default=None, max_length=200)


class LockUiLost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    backend_instance_id: str = Field(min_length=1, max_length=128)


@dataclass
class PendingLock:
    request: dict[str, Any]
    deadline: float
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None


def refusal(code: str, reason: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "error_ru": reason}


class LockCoordinator:
    def __init__(self, outbox: Outbox, changed: Callable[[], None], timeout_s: float = 5.0):
        self.outbox, self.changed, self.timeout_s = outbox, changed, timeout_s
        self.instance_id = str(uuid.uuid4())
        self._mutex = threading.RLock()
        self._scope: dict[str, Any] = dict(student_id=None, class_session_id=None, source_session_id=None,
                                          backend_instance_id=self.instance_id)
        self._pending: PendingLock | None = None
        self._locked = False
        self._confirmed = False
        self._reason: str | None = None
        self._state = "unconfirmed"
        self._detail: str | None = None
        try:
            self._journal = json.loads(outbox.get_meta("lock_commands_v1") or "{}")
            self._active = json.loads(outbox.get_meta("lock_active_v1") or "null")
        except (ValueError, TypeError):
            self._journal, self._active = {}, None

    def snapshot(self) -> dict[str, Any]:
        with self._mutex:
            return {"locked": self._locked, "lock_reason_ru": self._reason,
                    "lock_confirmed": self._confirmed,
                    "lock_requested": self._pending.request["locked"] if self._pending else bool(self._active),
                    "lock_requested_reason_ru": self._pending.request["reason_ru"] if self._pending else self._active.get("reason_ru") if self._active else None,
                    "lock_state": self._state, "lock_error_ru": self._detail,
                    "lock_request": dict(self._pending.request) if self._pending else None,
                    "backend_instance_id": self.instance_id,
                    "class_session_id": self._scope["class_session_id"],
                    "source_session_id": self._scope["source_session_id"]}

    def bind(self, student_id: str | None, class_session_id: str | None, source_session_id: str | None) -> None:
        scope = dict(student_id=student_id, class_session_id=class_session_id,
                     source_session_id=source_session_id, backend_instance_id=self.instance_id)
        with self._mutex:
            if scope == self._scope:
                return
            self._finish_locked(refusal("failed", "Сессия изменилась до подтверждения экрана"))
            self._scope = scope
            self._locked, self._reason, self._state = False, None, "unconfirmed"
            self._confirmed = False
            # A previously confirmed lock is re-rendered after restart/session change,
            # but is never reported effective before a receipt from this process.
            active = self._active
            if student_id and active and all(active.get(k) == scope[k] for k in ("student_id", "class_session_id")):
                self._start_locked(active["command_id"], True, active["reason_ru"], self.timeout_s, recovery=True)
            elif active:
                self._active = None
                self.outbox.set_meta("lock_active_v1", None)
        self.changed()

    def _key(self, cid: str) -> str:
        return json.dumps([self.outbox.get_meta("server"), self._scope["student_id"], self._scope["class_session_id"], cid])

    def begin(self, msg: dict[str, Any]) -> PendingLock | dict[str, Any]:
        cid, kind, payload = msg.get("command_id"), msg.get("kind"), msg.get("payload") or {}
        if not isinstance(cid, str) or not _ID.fullmatch(cid) or not isinstance(payload, dict):
            return refusal("invalid", "Некорректная команда блокировки")
        reason = payload.get("reason_ru") if kind == "lock" else None
        if kind == "lock" and (not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 200):
            return refusal("invalid", "Причина блокировки должна содержать 1–200 символов")
        if reason is not None:
            reason = reason.strip()
        remaining = self.timeout_s
        try:
            if msg.get("expires_at") is not None:
                expiry = datetime.fromisoformat(str(msg["expires_at"]).replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    raise ValueError("timezone required")
                remaining = min(remaining, expiry.timestamp() - time.time())
            if msg.get("ttl_ms") is not None:
                ttl = msg["ttl_ms"]
                if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
                    raise ValueError("invalid ttl")
                remaining = min(remaining, ttl / 1000)
        except (ValueError, TypeError, OverflowError):
            return refusal("invalid", "Некорректный срок команды")
        if remaining <= 0:
            return refusal("expired", "Срок команды истёк до отображения экрана")
        with self._mutex:
            if not self._scope["student_id"]:
                return refusal("failed", "Ученик ещё не подключён к классу")
            for key in ("student_id", "class_session_id", "source_session_id"):
                if key in msg and msg[key] != self._scope[key]:
                    return refusal("invalid", "Команда относится к другому ученику или сессии")
            key = self._key(cid)
            if key in self._journal:
                return refusal("duplicate", "Команда уже обработана; требуется новая команда преподавателя")
            self._journal[key] = True
            self._journal = dict(list(self._journal.items())[-500:])
            self.outbox.set_meta("lock_commands_v1", json.dumps(self._journal))
            self._finish_locked(refusal("failed", "Команда заменена более новой командой преподавателя"))
            pending = self._start_locked(cid, kind == "lock", reason, remaining)
        self.changed()
        return pending

    def _start_locked(self, cid: str, locked: bool, reason: str | None, remaining: float, recovery: bool = False) -> PendingLock:
        request = {**self._scope, "command_id": cid, "request_token": str(uuid.uuid4()), "locked": locked,
                   "reason_ru": reason, "expires_at": datetime.fromtimestamp(time.time() + remaining, timezone.utc).isoformat(),
                   "recovery": recovery}
        self._pending = PendingLock(request, time.monotonic() + remaining)
        self._state, self._detail = "requested", None
        return self._pending

    def wait(self, pending: PendingLock) -> dict[str, Any]:
        # Windows waits may return fractionally before a monotonic deadline.
        while not pending.event.is_set():
            remaining = pending.deadline - time.monotonic()
            if remaining <= 0:
                break
            pending.event.wait(remaining)
        self.expire()
        return pending.result or refusal("failed", "Экран приложения не подтвердил команду")

    def expire(self) -> None:
        changed = False
        with self._mutex:
            if self._pending and time.monotonic() >= self._pending.deadline:
                self._finish_locked(refusal("expired", "Нет подтверждения экрана приложения в срок"))
                changed = True
        if changed:
            self.changed()

    def confirm(self, receipt: LockReceipt) -> dict[str, Any]:
        self.expire()
        body = receipt.model_dump()
        with self._mutex:
            pending = self._pending
            if pending is None or any(body[k] != pending.request[k] for k in RECEIPT_KEYS):
                return {"accepted": False, "reason": "stale_or_mismatched_receipt"}
            if not receipt.applied or (receipt.locked and receipt.reason_ru != pending.request["reason_ru"]):
                self._finish_locked(refusal("failed", "Приложение не смогло показать запрошенный экран"))
            else:
                self._locked, self._reason = receipt.locked, pending.request["reason_ru"]
                self._confirmed = True
                self._active = {**self._scope, "command_id": receipt.command_id, "reason_ru": self._reason} if self._locked else None
                self.outbox.set_meta("lock_active_v1", json.dumps(self._active))
                self._finish_locked({"ok": True})
        self.changed()
        return {"accepted": True}

    def _finish_locked(self, result: dict[str, Any]) -> None:
        if self._pending is None:
            return
        pending, self._pending = self._pending, None
        pending.result = result
        self._state = "applied" if result["ok"] else "failed"
        self._detail = result.get("error_ru")
        pending.event.set()

    def stop(self) -> None:
        with self._mutex:
            self._finish_locked(refusal("failed", "Приложение завершает работу"))

    def renderer_lost(self, instance_id: str) -> dict[str, Any]:
        with self._mutex:
            if instance_id != self.instance_id:
                return {"accepted": False, "reason": "stale_backend"}
            self._finish_locked(refusal("failed", "Интерфейс приложения перезапускается"))
            self._locked, self._reason, self._state = False, None, "unconfirmed"
            self._confirmed = False
            if self._active:
                self._start_locked(self._active["command_id"], True, self._active["reason_ru"], self.timeout_s, recovery=True)
        self.changed()
        return {"accepted": True}
