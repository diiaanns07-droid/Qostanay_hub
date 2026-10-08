"""Append-only journal: who (teacher), to whom (students), when, what (action/command) and outcome.

Entries are kept in memory (bounded) and, when a path is given, appended to a JSON Lines file
(one entry per line, flushed and fsynced per write). Nothing is ever edited or deleted. No join
codes, tokens or resume tokens are written.
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .clock import Clock, iso

ACTIONS_RU = {
    "exam_created": "Создан экзамен",
    "exam_updated": "Изменён экзамен",
    "policy_created": "Создана политика",
    "policy_updated": "Изменена политика",
    "policy_assigned": "Назначена политика",
    "student_joined": "Студент подключился к экзамену",
    "command_issued": "Отправлена команда",
    "command_duplicate_request": "Повтор запроса (команда не создана повторно)",
    "command_unavailable": "Команда недоступна для студента",
    "command_state": "Изменилось состояние команды",
    "command_cancelled": "Команда отменена",
    "ack_duplicate": "Повторное подтверждение клиента (проигнорировано)",
    "ack_unknown": "Подтверждение неизвестной команды (проигнорировано)",
    "access_denied": "Отказано в доступе",
}


@dataclass(frozen=True)
class JournalEntry:
    seq: int
    at: str
    action: str
    actor_id: str | None  # teacher id; None = system / student client
    actor_name: str | None
    exam_id: str | None
    student_ids: tuple[str, ...] = ()
    command_id: str | None = None
    kind: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["student_ids"] = list(self.student_ids)
        d["action_ru"] = ACTIONS_RU.get(self.action, self.action)
        return d


class Journal:
    def __init__(self, clock: Clock, path: Path | None = None, max_memory: int = 20000):
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: deque[JournalEntry] = deque(maxlen=max_memory)
        self._seq = 0
        self._path = Path(path) if path is not None else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._seq = self._last_seq_on_disk()

    def _last_seq_on_disk(self) -> int:
        if self._path is None or not self._path.exists():
            return 0
        last = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    last = max(last, int(json.loads(line).get("seq", 0)))
                except (ValueError, AttributeError, TypeError):
                    continue  # a torn last line after a crash is skipped, never rewritten
        return last

    def write(
        self,
        action: str,
        *,
        actor_id: str | None = None,
        actor_name: str | None = None,
        exam_id: str | None = None,
        student_ids: tuple[str, ...] | list[str] = (),
        command_id: str | None = None,
        kind: str | None = None,
        **details: Any,
    ) -> JournalEntry:
        with self._lock:
            self._seq += 1
            entry = JournalEntry(
                seq=self._seq,
                at=iso(self._clock.now()),
                action=action,
                actor_id=actor_id,
                actor_name=actor_name,
                exam_id=exam_id,
                student_ids=tuple(student_ids),
                command_id=command_id,
                kind=kind,
                details={k: v for k, v in details.items() if v is not None},
            )
            self._entries.append(entry)
            if self._path is not None:
                line = json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":"))
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            return entry

    def query(
        self,
        *,
        exam_id: str | None = None,
        student_id: str | None = None,
        command_id: str | None = None,
        since_seq: int = 0,
        limit: int = 200,
    ) -> list[JournalEntry]:
        with self._lock:
            items = [
                e
                for e in self._entries
                if e.seq > since_seq
                and (exam_id is None or e.exam_id == exam_id)
                and (student_id is None or student_id in e.student_ids)
                and (command_id is None or e.command_id == command_id)
            ]
        return list(reversed(items))[: max(1, min(limit, 1000))]
