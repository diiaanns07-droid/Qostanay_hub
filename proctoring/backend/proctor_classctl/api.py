"""Teacher HTTP API for exams, policies and commands (FastAPI router, mounted by the class server).

Mount (T01/C1, teacher endpoints are loopback-only per qorgau.class.v1 §1):
    app.include_router(create_teacher_router(control, get_principal), prefix="/api/teacher")
get_principal(request) -> Principal | None comes from the server's teacher authentication (PIN
cookie). This router never authenticates by itself and adds no bypass.

All paths are under /control/ so they cannot collide with the v1 teacher routes. Errors:
    {"error": {"code": ..., "message_ru": ..., "details": {...}}}
"""

# no `from __future__ import annotations`: FastAPI must see the real Annotated dependency types
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .access import AccessDenied, Principal
from .commands import TTL_MAX_S, TTL_MIN_S, CommandConflict
from .exams import StaleEdit
from .policies import PolicyError
from .service import ClassControl

KeyStr = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")]
IdStr = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyIn(_In):
    mode: str
    start_url: str | None = None
    allowed_urls: list[str] = Field(default_factory=list, max_length=50)
    auth_domains: list[str] = Field(default_factory=list, max_length=50)
    allowed_apps: list[str] = Field(default_factory=list, max_length=50)
    instructions_ru: str = Field(default="", max_length=2000)


class ExamCreate(_In):
    title: str = Field(max_length=200)
    policy: PolicyIn
    staff: dict[IdStr, str] = Field(default_factory=dict)


class ExamUpdate(_In):
    revision: int
    title: str | None = Field(default=None, max_length=200)
    staff: dict[IdStr, str] | None = None


class PolicyCreate(_In):
    name: str = Field(max_length=80)
    policy: PolicyIn


class PolicyUpdate(_In):
    version: int
    name: str | None = Field(default=None, max_length=80)
    policy: PolicyIn


class AssignIn(_In):
    policy_id: IdStr
    student_ids: list[IdStr] = Field(min_length=1, max_length=200)
    idempotency_key: KeyStr | None = None
    deliver_now: bool = True


class CommandIn(_In):
    kind: str
    student_ids: list[IdStr] = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: KeyStr | None = None
    ttl_s: float | None = Field(default=None, ge=TTL_MIN_S, le=TTL_MAX_S)


def _err(status: int, code: str, message_ru: str, **details: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message_ru": message_ru, "details": details}})


def create_teacher_router(control: ClassControl, get_principal: Callable[[Request], Principal | None]) -> APIRouter:
    router = APIRouter(prefix="/control", tags=["T04 exams and commands"])

    def principal(request: Request) -> Principal:
        p = get_principal(request)
        if p is None:
            raise AccessDenied("unauthorized", "Нужен вход преподавателя")
        return p

    P = Annotated[Principal, Depends(principal)]

    def guarded(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except AccessDenied as exc:
            return _err(401 if exc.code == "unauthorized" else 403, exc.code, exc.message_ru)
        except PolicyError as exc:
            return _err(422, exc.code, exc.message_ru, field=exc.field)
        except StaleEdit as exc:
            return _err(409, "stale_edit", exc.message_ru, current=exc.current)
        except CommandConflict as exc:
            return _err(409, "conflict", exc.message_ru)
        except KeyError:
            return _err(404, "not_found", "Не найдено")

    @router.get("/meta")
    def meta(p: P):
        return control.meta()

    @router.get("/exams")
    def list_exams(p: P):
        return [control.exams.view(e) for e in control.exams.visible(p)]

    @router.post("/exams", status_code=201)
    def create_exam(body: ExamCreate, p: P):
        return guarded(lambda: control.exams.view(control.exams.create(
            p, title=body.title, policy=body.policy.model_dump(), staff=body.staff)))

    @router.get("/exams/{exam_id}")
    def get_exam(exam_id: str, p: P):
        return guarded(lambda: control.exams.view(control.exams.get(p, exam_id)))

    @router.patch("/exams/{exam_id}")
    def update_exam(exam_id: str, body: ExamUpdate, p: P):
        return guarded(lambda: control.exams.view(control.exams.update(
            p, exam_id, revision=body.revision, title=body.title, staff=body.staff)))

    @router.post("/exams/{exam_id}/policies", status_code=201)
    def create_policy(exam_id: str, body: PolicyCreate, p: P):
        return guarded(lambda: control.exams.create_policy(p, exam_id, name=body.name, policy=body.policy.model_dump()).view())

    @router.patch("/exams/{exam_id}/policies/{policy_id}")
    def update_policy(exam_id: str, policy_id: str, body: PolicyUpdate, p: P):
        return guarded(lambda: control.exams.update_policy(
            p, exam_id, policy_id, version=body.version, name=body.name, policy=body.policy.model_dump()).view())

    @router.post("/exams/{exam_id}/assignments")
    def assign(exam_id: str, body: AssignIn, p: P):
        return guarded(lambda: control.assign_policy(
            p, exam_id, policy_id=body.policy_id, student_ids=body.student_ids,
            idempotency_key=body.idempotency_key, deliver_now=body.deliver_now))

    @router.get("/exams/{exam_id}/students")
    def students(exam_id: str, p: P):
        return guarded(lambda: control.student_views(p, exam_id))

    @router.post("/exams/{exam_id}/commands", status_code=202)
    def send(exam_id: str, body: CommandIn, p: P):
        return guarded(lambda: control.send_command(
            p, exam_id, kind=body.kind, student_ids=body.student_ids, payload=body.payload,
            idempotency_key=body.idempotency_key, ttl_s=body.ttl_s))

    @router.get("/exams/{exam_id}/commands")
    def list_commands(exam_id: str, p: P, student_id: str | None = None):
        return guarded(lambda: control.command_list(p, exam_id, student_id))

    @router.post("/exams/{exam_id}/commands/{command_id}/cancel")
    def cancel(exam_id: str, command_id: str, p: P):
        return guarded(lambda: control.cancel_command(p, exam_id, command_id))

    @router.get("/exams/{exam_id}/journal")
    def journal(exam_id: str, p: P, student_id: str | None = None,
                since_seq: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=1000)] = 200):
        return guarded(lambda: control.journal_view(p, exam_id, student_id=student_id, since_seq=since_seq, limit=limit))

    return router


def install_error_handlers(app) -> None:
    """Map AccessDenied raised inside dependencies (no principal) to the API error format."""

    @app.exception_handler(AccessDenied)
    async def _denied(request: Request, exc: AccessDenied):  # noqa: ARG001
        return _err(401 if exc.code == "unauthorized" else 403, exc.code, exc.message_ru)
