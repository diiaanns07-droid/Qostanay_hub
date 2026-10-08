"""Fixtures for T04 tests: fake clock + simulated class + ClassControl (no network, no real clients)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from proctor_classctl import ClassControl, FakeClock, Principal, Role
from proctor_classctl.simulator import SimulatedClass

TEACHER = Principal("t-owner", "Айгерим (владелец)")
CO_TEACHER = Principal("t-co", "Болат (со-преподаватель)")
ASSISTANT = Principal("t-asst", "Ассистент", Role.TEACHER)
OBSERVER = Principal("t-obs", "Наблюдатель", Role.OBSERVER)
STRANGER = Principal("t-other", "Чужой преподаватель")
URL_POLICY = {"mode": "url", "start_url": "https://exam.example.kz/test/1", "allowed_urls": ["https://exam.example.kz/*"]}


@dataclass
class RecordingTransport:
    """Transport double that records sends; `online` decides whether a send succeeds."""

    online: set[str] = field(default_factory=set)
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def send(self, student_id: str, message: dict[str, Any]) -> bool:
        if student_id not in self.online:
            return False
        self.sent.append((student_id, message))
        return True

    def to(self, student_id: str) -> list[dict[str, Any]]:
        return [m for s, m in self.sent if s == student_id]


@dataclass
class Rig:
    clock: FakeClock
    control: ClassControl
    exam_id: str
    transport: Any
    sim: SimulatedClass | None = None

    def advance(self, seconds: float, step: float = 0.05) -> None:
        remaining = seconds
        while remaining > 1e-9:
            dt = min(step, remaining)
            self.clock.advance(dt)
            remaining -= dt
            if self.sim is not None:
                self.sim.step()
            self.control.tick()

    def views(self, principal: Principal = TEACHER) -> dict[str, dict[str, Any]]:
        return {v["student_id"]: v for v in self.control.student_views(principal, self.exam_id)}

    def lock(self, student_ids: list[str], key: str | None = None, reason: str = "Проверка документов", **kw: Any) -> dict[str, Any]:
        return self.control.send_command(TEACHER, self.exam_id, kind="lock", student_ids=student_ids,
                                         payload={"reason_ru": reason}, idempotency_key=key, **kw)


def _staff() -> dict[str, str]:
    return {CO_TEACHER.teacher_id: "teacher", ASSISTANT.teacher_id: "assistant", OBSERVER.teacher_id: "observer"}


@pytest.fixture()
def sim_rig() -> Rig:
    clock = FakeClock()
    sim = SimulatedClass(clock)
    control = ClassControl(sim, clock=clock)
    exam = control.exams.create(TEACHER, title="Экзамен", policy=URL_POLICY, staff=_staff())
    sim.attach(control, exam.exam_id)
    return Rig(clock, control, exam.exam_id, sim, sim)


@pytest.fixture()
def raw_rig() -> Rig:
    """Manual client: tests craft hello/ack/status messages themselves."""
    clock = FakeClock()
    transport = RecordingTransport()
    control = ClassControl(transport, clock=clock)
    exam = control.exams.create(TEACHER, title="Экзамен", policy=URL_POLICY, staff=_staff())
    return Rig(clock, control, exam.exam_id, transport)


FULL_CAPS = {"commands": ["start_exam", "finish_exam", "lock", "unlock", "apply_policy"], "modes": ["url", "app"],
             "command_progress": True, "command_expiry": True}


def connect(rig: Rig, student_id: str, caps: dict[str, Any] | None = FULL_CAPS) -> dict[str, Any] | None:
    rig.transport.online.add(student_id)
    hello: dict[str, Any] = {"type": "hello", "protocol": "qorgau.class.v1", "student_label": f"Студент {student_id}",
                             "computer_name": f"PC-{student_id}", "app_version": "test"}
    if caps is not None:
        hello["capabilities"] = caps
    return rig.control.student_connected(student_id, hello, rig.exam_id)


def disconnect(rig: Rig, student_id: str) -> None:
    rig.transport.online.discard(student_id)
    rig.control.student_disconnected(student_id)


def ack(rig: Rig, student_id: str, command_id: str, ok: bool = True, **extra: Any) -> None:
    rig.control.handle_student_message(student_id, {"type": "ack", "command_id": command_id, "ok": ok, **extra})


def status(rig: Rig, student_id: str, locked: bool) -> None:
    rig.control.handle_student_message(student_id, {"type": "status", "exam_state": "running", "camera": "ok",
                                                    "monitoring": "ok", "locked": locked})
