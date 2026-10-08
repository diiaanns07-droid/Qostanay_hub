"""Teacher permissions. Authentication (PIN cookie, loopback) is the class server's job (T01/C1);
this module only decides what an authenticated principal may do.

Roles:
  * teacher   — may create exams; on exams they own or co-teach: edit, assign policies, send commands;
  * assistant — on exams where they are listed: view, send commands (no edits of exam/policies);
  * observer  — on exams where they are listed: view only (students, commands, journal).
Nobody acts on an exam they are not listed on, or on a student who is not in that exam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class Role(StrEnum):
    TEACHER = "teacher"
    ASSISTANT = "assistant"
    OBSERVER = "observer"


class Action(StrEnum):
    VIEW = "view"
    EDIT = "edit"  # exam settings, policies, assignments
    COMMAND = "command"


class AccessDenied(Exception):
    def __init__(self, code: str, message_ru: str):
        super().__init__(message_ru)
        self.code = code
        self.message_ru = message_ru


@dataclass(frozen=True)
class Principal:
    teacher_id: str
    display_name: str
    role: Role = Role.TEACHER

    def __post_init__(self) -> None:
        if not ID_RE.match(self.teacher_id):
            raise ValueError("teacher_id must match ^[A-Za-z0-9._:-]{1,64}$")
        if not self.display_name.strip() or len(self.display_name) > 80:
            raise ValueError("display_name must be 1..80 characters")


def require_can_create(principal: Principal) -> None:
    if principal.role != Role.TEACHER:
        raise AccessDenied("forbidden", "Создавать экзамены может только преподаватель")


_RANK = {Role.OBSERVER: 0, Role.ASSISTANT: 1, Role.TEACHER: 2}


def check(principal: Principal, action: Action, *, owner_id: str, staff: dict[str, Role]) -> None:
    """staff = {teacher_id: role} of the exam (the owner is always a teacher). The effective role is
    the lower of the exam role and the principal's own role (an observer account never commands)."""
    if principal.teacher_id == owner_id:
        exam_role: Role | None = Role.TEACHER
    else:
        exam_role = staff.get(principal.teacher_id)
    if exam_role is None:
        raise AccessDenied("forbidden", "Нет доступа к этому экзамену")
    role = min(exam_role, principal.role, key=_RANK.__getitem__)
    if action == Action.VIEW:
        return
    if action == Action.COMMAND and role in (Role.TEACHER, Role.ASSISTANT):
        return
    if action == Action.EDIT and role == Role.TEACHER:
        return
    raise AccessDenied("forbidden", "Недостаточно прав для этого действия")
