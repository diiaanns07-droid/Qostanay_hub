"""DEV server for the T04 teacher interface with SIMULATED students (no real class server, no students).

    python -m proctor_classctl.devserver [--port 8791] [--journal PATH]
    open http://127.0.0.1:8791/

* Loopback only (binds 127.0.0.1 and rejects non-loopback peers and foreign Host headers).
* Teacher identity: DEV selector header `X-Qorgau-Dev-Teacher` (t-aigerim / t-bolat / t-observer).
  The real class server (T01/C1) authenticates teachers with its PIN cookie instead.
* Every student here is a SIMULATOR (labelled "СИМУЛЯТОР"); nothing is sent to real computers.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .access import Principal, Role
from .api import create_teacher_router, install_error_handlers
from .clock import SystemClock
from .service import ClassControl
from .simulator import BEHAVIOURS, SimulatedClass

UI_DIR = Path(__file__).resolve().parents[2] / "class-control-ui"
DEV_TEACHERS = {
    "t-aigerim": Principal("t-aigerim", "Айгерим Сейтова (преподаватель)", Role.TEACHER),
    "t-bolat": Principal("t-bolat", "Болат Ахметов (другой преподаватель)", Role.TEACHER),
    "t-observer": Principal("t-observer", "Наблюдатель (только просмотр)", Role.OBSERVER),
}
DEMO_STUDENTS = [
    ("sim-01", "Студент 1 — успех", "success"),
    ("sim-02", "Студент 2 — задержка ответа", "delay"),
    ("sim-03", "Студент 3 — ошибка клиента", "error"),
    ("sim-04", "Студент 4 — обрыв до выполнения", "disconnect_before_exec"),
    ("sim-05", "Студент 5 — обрыв после выполнения", "disconnect_after_exec"),
    ("sim-06", "Студент 6 — нет связи", "offline"),
    ("sim-07", "Студент 7 — клиент v1 без возможностей", "v1"),
    ("sim-08", "Студент 8 — блокировка не поддерживается", "unsupported"),
]


class BehaviourIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    behaviour: str


class OnlineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    online: bool


def _loopback(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "testclient")


def build_app(*, journal_path: Path | None = None, tick_s: float = 0.1) -> FastAPI:
    clock = SystemClock()
    sim = SimulatedClass(clock)
    control = ClassControl(sim, clock=clock, journal_path=journal_path)
    owner = DEV_TEACHERS["t-aigerim"]
    exam = control.exams.create(
        owner,
        title="Демо-экзамен (СИМУЛЯТОР)",
        policy={"mode": "url", "start_url": "https://exam.example.kz/test/1", "allowed_urls": ["https://exam.example.kz/*"],
                "auth_domains": [], "instructions_ru": "Откройте тест и отвечайте на вопросы."},
        staff={"t-observer": "observer"},
    )
    sim.attach(control, exam.exam_id)
    for sid, name, behaviour in DEMO_STUDENTS:
        sim.add_student(sid, name, behaviour)
        if behaviour == "offline":  # register it once so the teacher sees it, then take it offline
            sim.set_online(sid, True)
            sim.set_behaviour(sid, "offline")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async def loop() -> None:
            while True:
                sim.step()
                control.tick()
                await asyncio.sleep(tick_s)

        task = asyncio.create_task(loop())
        yield
        task.cancel()

    app = FastAPI(title="Qorgau Class T04 DEV (SIMULATOR)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    app.state.control, app.state.sim, app.state.exam_id = control, sim, exam.exam_id

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        host_header = (request.headers.get("host") or "").split(":")[0]
        if not _loopback(request.client.host if request.client else None) or host_header not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message_ru": "Только с этого компьютера", "details": {}}})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def principal(request: Request) -> Principal | None:
        return DEV_TEACHERS.get(request.headers.get("x-qorgau-dev-teacher", ""))

    app.include_router(create_teacher_router(control, principal), prefix="/api/teacher")

    @app.get("/sim/info")
    def sim_info() -> dict[str, Any]:
        return {"simulator": True, "exam_id": exam.exam_id, "behaviours": list(BEHAVIOURS),
                "teachers": {k: {"name": v.display_name, "role": v.role.value} for k, v in DEV_TEACHERS.items()},
                "students": sim.describe()}

    @app.post("/sim/students/{student_id}/behaviour")
    def sim_behaviour(student_id: str, body: BehaviourIn):
        if student_id not in sim.students or body.behaviour not in BEHAVIOURS:
            return JSONResponse(status_code=422, content={"error": {"code": "invalid", "message_ru": "Неизвестный студент или поведение", "details": {}}})
        sim.set_behaviour(student_id, body.behaviour)
        return {"ok": True}

    @app.post("/sim/students/{student_id}/online")
    def sim_online(student_id: str, body: OnlineIn):
        if student_id not in sim.students:
            return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message_ru": "Нет такого студента", "details": {}}})
        sim.set_online(student_id, body.online)
        return {"ok": True}

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

        @app.get("/")
        def index():
            return FileResponse(UI_DIR / "index.html")

    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T04 dev server with SIMULATED students (loopback only)")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--journal", type=Path, default=None, help="append the journal to this JSONL file")
    args = ap.parse_args(argv)
    import uvicorn

    print(f"Qorgau Class T04 DEV — СИМУЛЯТОР студентов: http://127.0.0.1:{args.port}/  (не для реального экзамена)", flush=True)
    uvicorn.run(build_app(journal_path=args.journal), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
