"""What a student client can do, from its `hello` (additive field `capabilities`, see
handoffs/T04/DEPENDENCIES.txt). A command the client did not declare is unavailable, explicitly.

A plain v1 client (no `capabilities`) gets the protocol-mandated v1 set only:
start_exam, finish_exam, lock, unlock. Policy changes then reach it only through the next
`welcome` (reconnect), and modes are "not confirmed by the client".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

T04_KINDS = ("start_exam", "finish_exam", "lock", "unlock", "apply_policy")
V1_MANDATORY = frozenset({"start_exam", "finish_exam", "lock", "unlock"})
KNOWN_MODES = frozenset({"url", "app"})

KIND_LABEL_RU = {
    "start_exam": "Начать экзамен и наблюдение",
    "finish_exam": "Завершить экзамен",
    "lock": "Заблокировать",
    "unlock": "Разблокировать",
    "apply_policy": "Применить политику",
}


@dataclass(frozen=True)
class ClientCapabilities:
    declared: bool  # False = v1 client without the capabilities block
    commands: frozenset[str]
    modes: frozenset[str] | None  # None = not reported by the client
    command_progress: bool  # sends command_progress {received|executing}
    command_expiry: bool  # checks expires_at / ttl_ms before executing
    site_timer_pause: bool  # can pause the external site's timer (needs a site integration)
    app_version: str | None = None

    @classmethod
    def from_hello(cls, hello: dict[str, Any] | None) -> "ClientCapabilities":
        hello = hello or {}
        app_version = str(hello.get("app_version"))[:64] if hello.get("app_version") is not None else None
        caps = hello.get("capabilities")
        if not isinstance(caps, dict):
            return cls(False, V1_MANDATORY, None, False, False, False, app_version)
        commands = caps.get("commands")
        kinds = frozenset(str(k) for k in commands) if isinstance(commands, list) else V1_MANDATORY
        modes = caps.get("modes")
        mode_set = frozenset(str(m) for m in modes) & KNOWN_MODES if isinstance(modes, list) else None
        return cls(
            True,
            kinds,
            mode_set,
            caps.get("command_progress") is True,
            caps.get("command_expiry") is True,
            caps.get("site_timer_pause") is True,
            app_version,
        )

    def supports(self, kind: str) -> bool:
        return kind in self.commands

    def unsupported_reason_ru(self, kind: str) -> str | None:
        if self.supports(kind):
            return None
        if kind == "apply_policy" and not self.declared:
            return "Клиент не сообщил о смене политики на лету: политика применится при его следующем подключении"
        return f"Клиент студента не поддерживает действие «{KIND_LABEL_RU.get(kind, kind)}»"

    def mode_reason_ru(self, mode: str) -> str | None:
        if self.modes is None or mode in self.modes:
            return None
        label = "«Внешний сайт»" if mode == "url" else "«Отдельная программа»"
        return f"Клиент студента не поддерживает режим {label}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared": self.declared,
            "commands": sorted(self.commands),
            "modes": sorted(self.modes) if self.modes is not None else None,
            "command_progress": self.command_progress,
            "command_expiry": self.command_expiry,
            "site_timer_pause": self.site_timer_pause,
            "app_version": self.app_version,
        }


UNKNOWN = ClientCapabilities(False, frozenset(), None, False, False, False)
