"""Exam sessions and their policies (in memory; the class server owns persistence of sessions).

An exam has an owner (teacher), optional staff (teacher / assistant / observer), a default policy
and optional extra policies (e.g. a different allow-list for some students). Each policy edit
creates a new version; students keep the version that was actually applied until the teacher
sends the new one. Edits use optimistic concurrency (revision / version) -> 409 on a stale edit.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .access import AccessDenied, Action, Principal, Role, check, require_can_create
from .clock import Clock, iso
from .journal import Journal
from .policies import MODE_LABEL_RU, PolicyContent, PolicyError, build_policy_content

MAX_TITLE = 200
MAX_POLICIES = 20


class StaleEdit(Exception):
    def __init__(self, message_ru: str, current: int):
        super().__init__(message_ru)
        self.message_ru = message_ru
        self.current = current


@dataclass
class Policy:
    policy_id: str
    exam_id: str
    name: str
    version: int
    content: PolicyContent
    updated_at: datetime
    updated_by: str

    def view(self) -> dict[str, Any]:
        c = self.content
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "version": self.version,
            "mode": c.mode.value,
            "mode_ru": MODE_LABEL_RU[c.mode],
            "start_url": c.start_url,
            "allowed_urls": list(c.allowed_urls),
            "auth_domains": list(c.auth_domains),
            "allowed_apps": list(c.allowed_apps),
            "instructions_ru": c.instructions_ru,
            "warnings_ru": list(c.warnings_ru),
            "updated_at": iso(self.updated_at),
            "updated_by": self.updated_by,
        }

    def client_payload(self) -> dict[str, Any]:
        return {"policy_id": self.policy_id, "version": self.version, "name": self.name, **self.content.client_payload()}


@dataclass
class Exam:
    exam_id: str
    title: str
    owner_id: str
    owner_name: str
    staff: dict[str, Role]
    revision: int
    created_at: datetime
    updated_at: datetime
    default_policy_id: str
    policies: dict[str, Policy] = field(default_factory=dict)
    assignments: dict[str, str] = field(default_factory=dict)  # student_id -> policy_id
    students: list[str] = field(default_factory=list)

    def policy_for(self, student_id: str) -> Policy:
        return self.policies[self.assignments.get(student_id, self.default_policy_id)]


def _title(value: str) -> str:
    title = (value or "").strip()
    if not title or len(title) > MAX_TITLE:
        raise PolicyError("title", "invalid", f"Название экзамена: от 1 до {MAX_TITLE} символов")
    return title


def _staff(raw: dict[str, str] | None, owner_id: str) -> dict[str, Role]:
    out: dict[str, Role] = {}
    for tid, role in (raw or {}).items():
        if tid == owner_id:
            continue
        try:
            out[str(tid)[:64]] = Role(role)
        except ValueError as exc:
            raise PolicyError("staff", "invalid_role", f"Неизвестная роль: {role}") from exc
    return out


class ExamService:
    def __init__(self, clock: Clock, journal: Journal):
        self._clock = clock
        self._journal = journal
        self._lock = threading.RLock()
        self._exams: dict[str, Exam] = {}

    # ------------------------------------------------------------------ access helpers
    def get(self, principal: Principal, exam_id: str, action: Action = Action.VIEW) -> Exam:
        with self._lock:
            exam = self._exams.get(exam_id)
            if exam is None:
                raise KeyError(exam_id)
            try:
                check(principal, action, owner_id=exam.owner_id, staff=exam.staff)
            except AccessDenied:
                self._journal.write("access_denied", actor_id=principal.teacher_id, actor_name=principal.display_name,
                                    exam_id=exam_id, attempted=action.value)
                raise
            return exam

    def visible(self, principal: Principal) -> list[Exam]:
        with self._lock:
            return [e for e in self._exams.values() if e.owner_id == principal.teacher_id or principal.teacher_id in e.staff]

    def exam_of_student(self, student_id: str) -> Exam | None:
        with self._lock:
            for exam in self._exams.values():
                if student_id in exam.students:
                    return exam
            return None

    def raw(self, exam_id: str) -> Exam | None:
        with self._lock:
            return self._exams.get(exam_id)

    # ------------------------------------------------------------------ exams
    def create(self, principal: Principal, *, title: str, policy: dict[str, Any], staff: dict[str, str] | None = None) -> Exam:
        require_can_create(principal)
        now = self._clock.now()
        content = build_policy_content(**policy)
        with self._lock:
            exam_id = f"exam-{uuid.uuid4().hex[:12]}"
            pol = Policy(f"pol-{uuid.uuid4().hex[:12]}", exam_id, "Основная", 1, content, now, principal.display_name)
            exam = Exam(exam_id, _title(title), principal.teacher_id, principal.display_name, _staff(staff, principal.teacher_id),
                        1, now, now, pol.policy_id, {pol.policy_id: pol})
            self._exams[exam_id] = exam
        self._journal.write("exam_created", actor_id=principal.teacher_id, actor_name=principal.display_name,
                            exam_id=exam_id, title=exam.title, mode=content.mode.value)
        return exam

    def update(self, principal: Principal, exam_id: str, *, revision: int, title: str | None = None,
               staff: dict[str, str] | None = None) -> Exam:
        with self._lock:
            exam = self.get(principal, exam_id, Action.EDIT)
            if revision != exam.revision:
                raise StaleEdit("Экзамен уже изменён другим действием — обновите страницу", exam.revision)
            changed = []
            if title is not None:
                exam.title = _title(title)
                changed.append("title")
            if staff is not None:
                if principal.teacher_id != exam.owner_id:
                    raise AccessDenied("forbidden", "Состав преподавателей меняет только владелец экзамена")
                exam.staff = _staff(staff, exam.owner_id)
                changed.append("staff")
            exam.revision += 1
            exam.updated_at = self._clock.now()
        self._journal.write("exam_updated", actor_id=principal.teacher_id, actor_name=principal.display_name,
                            exam_id=exam_id, fields=",".join(changed), revision=exam.revision)
        return exam

    # ------------------------------------------------------------------ policies
    def create_policy(self, principal: Principal, exam_id: str, *, name: str, policy: dict[str, Any]) -> Policy:
        content = build_policy_content(**policy)
        name = (name or "").strip()[:80]
        if not name:
            raise PolicyError("name", "invalid", "Укажите название политики")
        with self._lock:
            exam = self.get(principal, exam_id, Action.EDIT)
            if len(exam.policies) >= MAX_POLICIES:
                raise PolicyError("policies", "too_many", f"Не больше {MAX_POLICIES} политик в экзамене")
            pol = Policy(f"pol-{uuid.uuid4().hex[:12]}", exam_id, name, 1, content, self._clock.now(), principal.display_name)
            exam.policies[pol.policy_id] = pol
            exam.revision += 1
        self._journal.write("policy_created", actor_id=principal.teacher_id, actor_name=principal.display_name,
                            exam_id=exam_id, policy_id=pol.policy_id, name=name, mode=content.mode.value)
        return pol

    def update_policy(self, principal: Principal, exam_id: str, policy_id: str, *, version: int,
                      name: str | None, policy: dict[str, Any]) -> Policy:
        content = build_policy_content(**policy)
        with self._lock:
            exam = self.get(principal, exam_id, Action.EDIT)
            pol = exam.policies.get(policy_id)
            if pol is None:
                raise KeyError(policy_id)
            if version != pol.version:
                raise StaleEdit("Политика уже изменена — обновите страницу", pol.version)
            pol.content = content
            if name is not None and name.strip():
                pol.name = name.strip()[:80]
            pol.version += 1
            pol.updated_at = self._clock.now()
            pol.updated_by = principal.display_name
            exam.revision += 1
        self._journal.write("policy_updated", actor_id=principal.teacher_id, actor_name=principal.display_name,
                            exam_id=exam_id, policy_id=policy_id, version=pol.version, mode=content.mode.value)
        return pol

    def assign(self, principal: Principal, exam_id: str, policy_id: str, student_ids: list[str]) -> tuple[Exam, Policy]:
        with self._lock:
            exam = self.get(principal, exam_id, Action.EDIT)
            pol = exam.policies.get(policy_id)
            if pol is None:
                raise KeyError(policy_id)
            unknown = [s for s in student_ids if s not in exam.students]
            if unknown:
                raise AccessDenied("not_in_exam", "Студент не подключён к этому экзамену: " + ", ".join(unknown[:5]))
            for sid in student_ids:
                exam.assignments[sid] = policy_id
            exam.revision += 1
        self._journal.write("policy_assigned", actor_id=principal.teacher_id, actor_name=principal.display_name,
                            exam_id=exam_id, student_ids=student_ids, policy_id=policy_id, version=pol.version)
        return exam, pol

    # ------------------------------------------------------------------ students
    def add_student(self, exam_id: str, student_id: str) -> Exam:
        with self._lock:
            exam = self._exams.get(exam_id)
            if exam is None:
                raise KeyError(exam_id)
            for other in self._exams.values():  # one exam per student connection
                if other is not exam and student_id in other.students:
                    other.students.remove(student_id)
                    other.assignments.pop(student_id, None)
            if student_id not in exam.students:
                exam.students.append(student_id)
                self._journal.write("student_joined", exam_id=exam_id, student_ids=[student_id])
            return exam

    def view(self, exam: Exam) -> dict[str, Any]:
        return {
            "exam_id": exam.exam_id,
            "title": exam.title,
            "owner_id": exam.owner_id,
            "owner_name": exam.owner_name,
            "staff": {k: v.value for k, v in exam.staff.items()},
            "revision": exam.revision,
            "created_at": iso(exam.created_at),
            "updated_at": iso(exam.updated_at),
            "default_policy_id": exam.default_policy_id,
            "policies": [p.view() for p in exam.policies.values()],
            "assignments": dict(exam.assignments),
            "students": list(exam.students),
        }
