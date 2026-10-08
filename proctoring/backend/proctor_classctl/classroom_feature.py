"""Class server (C1/T01) adapter for T04 exams, policies and commands — classroom/coordination/INTERFACES.md §3.

    QORGAU_CLASS_FEATURES="...,proctor_classctl.classroom_feature:create"

Minimal shape from the INTERFACES role mapping: transport = ctx.send_raw_to_student, journal under the feature dir,
teacher routes under /api/teacher/control/ (reserved for T04), principal = the logged-in PIN teacher. Students are
registered without an exam (exam_id None), so welcome.exam is NOT overridden and the core command bus (start/lock/
unlock/finish/request_clip) keeps working exactly as before; T04 exams attach students through its own API.
Acks of T04's own command_ids reach it through on_student_message (the core ignores acks it did not issue).
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .access import Principal
from .api import create_teacher_router
from .service import ClassControl


class _Transport:
    def __init__(self, send):
        self._send = send

    def send(self, student_id: str, message: dict[str, Any]) -> bool:
        return bool(self._send(student_id, message))


class ClassControlFeature:
    name = "exams"
    owner = "T04"

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.control = ClassControl(transport=_Transport(ctx.send_raw_to_student), journal_path=ctx.feature_dir("exams") / "journal.jsonl")
        self.router = APIRouter(prefix="/api/teacher")
        self.router.include_router(create_teacher_router(self.control, self._principal))
        # The production drawer controls the one current C1 class through C1's command API.
        # Its assets are deliberately fixed, authenticated and independent of the T04 demo exam store.
        assets = {
            "classroom-module.js": "text/javascript", "classroom-model.js": "text/javascript",
            "t02-module.css": "text/css", "src/api.js": "text/javascript",
            "src/model.js": "text/javascript", "src/dom.js": "text/javascript",
        }
        root = Path(__file__).resolve().parents[2] / "class-control-ui"

        @self.router.get("/control/assets/{name:path}")
        def asset(name: str, request: Request):
            self.ctx.teacher(request)
            if name not in assets:
                raise HTTPException(404)
            return FileResponse(root / name, media_type=assets[name], headers={"Cache-Control": "no-store"})

    def _principal(self, request: Request) -> Principal | None:
        teacher = self.ctx.teacher(request)  # raises 401 for a request without the teacher cookie
        return Principal(teacher.teacher_id, "Преподаватель") if teacher is not None else None

    def on_student_connected(self, student: Any, hello: dict[str, Any]) -> dict[str, Any] | None:
        return self.control.student_connected(student.student_id, hello, None)

    def on_student_message(self, student_id: str, message: dict[str, Any]) -> None:
        self.control.handle_student_message(student_id, message)

    def on_student_disconnected(self, student_id: str) -> None:
        self.control.student_disconnected(student_id)

    def tick(self) -> None:
        self.control.tick()


def create(ctx: Any) -> ClassControlFeature:
    return ClassControlFeature(ctx)


create_classroom_feature = create
