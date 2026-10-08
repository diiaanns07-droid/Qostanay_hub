"""Class server composition root: HTTP + WebSocket API (owner: T01).

    /ws/student                student app (C2), LAN; first message `hello` (code or resume token, never in the URL)
    /api/student/*             student HTTP, `Authorization: Bearer <resume_token>` (clips: T03 feature)
    /api/teacher/*, /ws/teacher, /   teacher console — this computer only + PIN cookie (v1 §1, §2.6)

Order of composition (create_app): database + core migrations -> core state -> features (+ their namespaced
migrations, reserved routes) -> core fallback routes for v1 paths no feature took -> static teacher UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from itertools import count
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from ..contracts import models as m
from .auth import TEACHER_COOKIE, RateLimiter, TeacherAuth, TeacherPrincipal, host_header_ok, is_loopback_ip, origin_ok
from .config import SERVER_VERSION, ServerConfig
from .core import ClassroomCore, ClassroomError, StudentConnection, StudentRec, envelope
from .db import CORE_MIGRATIONS, Database
from .features import FeatureContext, FeatureManager, ROUTE_RESERVATIONS, V1_DELEGATED, under
from .hub import TeacherHub, dumps

log = logging.getLogger("classroom.server")
STUDENT_MESSAGE = TypeAdapter(m.StudentMessage)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss:; font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


def api_error(code: str, message_ru: str, status: int, **details: Any) -> JSONResponse:
    body = m.ApiError(error=m.ApiErrorBody(code=code, message_ru=message_ru[:500], details=details))
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


class Gate:
    """Pure ASGI gate in front of every route (features cannot bypass it)."""

    def __init__(self, app: Any, state: dict[str, Any]):
        self.app = app
        self.state = state

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        cfg: ServerConfig = self.state["config"]
        path: str = scope.get("path", "")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = (scope.get("client") or ("", 0))[0]
        problem: tuple[str, str, int] | None = None
        if path == "/ws/student" or under(path, "/api/student"):
            if scope["type"] == "http":
                length = headers.get("content-length")
                limit = cfg.max_clip_bytes if under(path, "/api/student/clips") else cfg.max_ws_message_bytes
                if "chunked" in headers.get("transfer-encoding", "").lower():
                    problem = ("length_required", "Нужен заголовок Content-Length", 411)
                elif length is not None and (not length.isdigit() or int(length) > limit):
                    problem = ("payload_too_large", f"Тело больше {limit} байт", 413)
                else:
                    auth = headers.get("authorization", "")
                    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
                    core: ClassroomCore = self.state["core"]
                    if core.student_by_token(token) is None:
                        problem = ("unauthorized", "Нужен действующий токен студента", 401)
        else:  # teacher console and everything else: this computer only
            if not is_loopback_ip(client):
                problem = ("forbidden_not_loopback", "Панель преподавателя доступна только на компьютере преподавателя", 403)
            elif not host_header_ok(headers.get("host")):
                problem = ("forbidden_host", "Неверный заголовок Host", 403)
            elif not origin_ok(headers.get("origin"), self.state["port"], cfg.dev_origin):
                problem = ("forbidden_origin", "Запрос с чужой страницы отклонён", 403)
            elif scope["type"] == "http" and under(path, "/api/teacher") and path != "/api/teacher/login":
                auth: TeacherAuth = self.state["teacher_auth"]
                if auth.check(_cookie(headers.get("cookie"), TEACHER_COOKIE)) is None:
                    problem = ("unauthorized", "Войдите с PIN преподавателя", 401)
                else:
                    length = headers.get("content-length")
                    if "chunked" in headers.get("transfer-encoding", "").lower() or (length is not None and (not length.isdigit() or int(length) > cfg.max_ws_message_bytes)):
                        problem = ("payload_too_large", "Слишком большое тело запроса", 413)
        if problem is None:
            return await self.app(scope, receive, send)
        code, message, status = problem
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403, "reason": code})
            return
        response = api_error(code, message, status)
        await response(scope, receive, send)


def _cookie(header: str | None, name: str) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def create_app(
    config: ServerConfig,
    *,
    extra_features: list[Any] | None = None,
    port_holder: dict[str, int] | None = None,
    clock: Callable[[], Any] | None = None,
) -> FastAPI:
    db = Database(config.data_dir / "classroom.sqlite3")
    db.migrate(CORE_MIGRATIONS)
    hub = TeacherHub(config.teacher_queue)
    core = ClassroomCore(config, db, hub, **({"clock": clock} if clock else {}))
    teacher_auth = TeacherAuth(config.teacher_pin, RateLimiter(config.pin_fail_limit, config.pin_block_s))
    state: dict[str, Any] = {"config": config, "core": core, "teacher_auth": teacher_auth, "port": (port_holder or {}).get("port", config.port)}
    epochs = count(1)

    def teacher_of(request: Request) -> TeacherPrincipal:
        p = teacher_auth.check(request.cookies.get(TEACHER_COOKIE))
        if p is None:
            raise ClassroomError("unauthorized", "Войдите с PIN преподавателя", 401)
        return p

    def student_of(request: Request) -> m.Student | None:
        auth = request.headers.get("authorization", "")
        st = core.student_by_token(auth[7:].strip() if auth.lower().startswith("bearer ") else None)
        return core.student_model(st) if st else None

    def send_raw(student_id: str, message: dict[str, Any]) -> bool:
        st = core.students.get(student_id)
        if st is None or st.conn is None:
            return False
        TypeAdapter(m.ServerToStudentMessage).validate_python({**envelope(), **message})  # never send off-contract
        return st.conn.enqueue({**envelope(), **message})

    ctx = FeatureContext(
        data_dir=config.data_dir,
        db=db,
        publish=lambda event, data: hub.publish({"type": "feature_event", "feature": event.split(".", 1)[0], "event": event, "data": data}),  # event "t03.decision" -> feature "t03"
        submit_command=lambda student_id, kind, payload, issued_by="feature", ttl_ms=None: core.submit_command(student_id, m.CommandKind(kind), payload, issued_by=issued_by, ttl_ms=ttl_ms),
        send_raw_to_student=send_raw,
        students=lambda: [core.student_model(s) for s in core.students.values()],
        student=lambda sid: core.student_model(core.students[sid]) if sid in core.students else None,
        current_session_id=lambda: core.current_session_id,
        teacher=teacher_of,
        student_from_request=student_of,
        student_by_token=lambda token: (lambda st: core.student_model(st) if st else None)(core.student_by_token(token)),
        config=config,
    )
    features = FeatureManager.load(config.features, ctx, extra_features)
    core.features = features
    db.migrate(features.migrations())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind(asyncio.get_running_loop())
        state["port"] = (port_holder or {}).get("port", state["port"])
        ticker = asyncio.create_task(_ticker(core, features, config.tick_s))
        try:
            yield
        finally:
            ticker.cancel()
            for st in list(core.students.values()):
                if st.conn is not None:
                    st.conn.request_close(1001, "server_shutdown")
            features.close()
            db.close()

    app = FastAPI(title="Qorgau Classroom server", version=SERVER_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url="/api/teacher/openapi.json")
    app.state.core = core
    app.state.hub = hub
    app.state.features = features
    app.state.teacher_auth = teacher_auth
    app.state.gate = state

    @app.exception_handler(ClassroomError)
    async def _classroom_error(_: Request, exc: ClassroomError) -> JSONResponse:
        return api_error(exc.code, exc.message_ru, exc.status, **exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        return api_error("invalid_argument", "Запрос не соответствует контракту", 422, location=".".join(str(p) for p in first.get("loc", []))[:200], problem=str(first.get("msg", ""))[:200])

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return api_error("not_found" if exc.status_code == 404 else "http_error", str(exc.detail)[:200], exc.status_code)

    @app.exception_handler(Exception)
    async def _internal(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return api_error("internal", "Внутренняя ошибка сервера (см. журнал сервера)", 500)

    # ---------------------------------------------------------------------------------- feature routes
    taken: set[tuple[str, str]] = set()
    features.mount(app, taken)

    # ----------------------------------------------------------------------------------- teacher routes
    t = APIRouter()

    @t.post("/api/teacher/login")
    async def login(body: m.LoginRequest) -> Response:
        left = teacher_auth.limiter.blocked("loopback")
        if left:
            return api_error("pin_rate_limited", f"Слишком много попыток. Повторите через {int(left) + 1} с", 429, retry_after_s=int(left) + 1)
        cookie, blocked = teacher_auth.login(body.pin)
        if cookie is None:
            return api_error("pin_rejected", "Неверный PIN", 401, **({"blocked_for_s": int(blocked) + 1} if blocked else {}))
        resp = JSONResponse({"ok": True, "teacher_id": "teacher"})
        resp.set_cookie(TEACHER_COOKIE, cookie, httponly=True, samesite="strict", secure=False, path="/")
        return resp

    @t.post("/api/teacher/logout")
    async def logout(request: Request) -> Response:
        teacher_auth.logout(request.cookies.get(TEACHER_COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(TEACHER_COOKIE, path="/")
        return resp

    @t.get("/api/teacher/info", response_model=m.ServerInfo)
    async def info() -> m.ServerInfo:
        return m.ServerInfo(server_version=SERVER_VERSION, server_time=m.utc_now(), session=core.session_model(core.current_session()), features=features.status(), **core.info_counts())

    @t.post("/api/teacher/session", response_model=m.SessionCreated, status_code=201)
    async def create_session(body: m.SessionCreate) -> m.SessionCreated:
        return core.create_session(body)

    @t.get("/api/teacher/session", response_model=m.Session | None)
    async def get_session() -> m.Session | None:
        return core.session_model(core.current_session())

    @t.post("/api/teacher/session/close", response_model=m.Session)
    async def close_session() -> m.Session | None:
        return core.close_session()

    @t.get("/api/teacher/students", response_model=list[m.StudentCard])
    async def students() -> list[m.StudentCard]:
        return core.cards()

    def _student(student_id: str) -> StudentRec:
        st = core.students.get(student_id)
        if st is None:
            raise ClassroomError("student_not_found", "Студент не найден", 404)
        return st

    @t.get("/api/teacher/students/{student_id}", response_model=m.StudentCard)
    async def student(student_id: str) -> m.StudentCard:
        return core.card(_student(student_id))

    @t.get("/api/teacher/students/{student_id}/events", response_model=list[m.ObservationEvent])
    async def events(student_id: str, limit: int = 200) -> list[m.ObservationEvent]:
        return core.events_for(_student(student_id).student_id, max(1, min(limit, 1000)))

    @t.get("/api/teacher/students/{student_id}/preview.jpg")
    async def preview(student_id: str) -> Response:
        st = _student(student_id)
        if st.preview is None:
            return Response(status_code=204)
        return Response(st.preview, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Qorgau-Origin": st.origin.value})

    @t.post("/api/teacher/students/{student_id}/commands", response_model=m.Command, status_code=202)
    async def submit(student_id: str, body: m.CommandCreate, request: Request) -> m.Command:
        return core.submit_command(_student(student_id).student_id, body.kind, body.payload, issued_by=teacher_of(request).teacher_id, ttl_ms=body.ttl_ms)

    @t.get("/api/teacher/students/{student_id}/commands", response_model=list[m.Command])
    async def commands(student_id: str) -> list[m.Command]:
        return core.commands_for(_student(student_id).student_id)

    @t.get("/api/teacher/students/{student_id}/audio", response_model=list[m.AudioSession])
    async def audio(student_id: str) -> list[m.AudioSession]:
        sid = _student(student_id).student_id
        return sorted((a for a in core.audio.values() if a.student_id == sid), key=lambda a: a.requested_at)

    @t.get("/api/teacher/commands/{command_id}", response_model=m.Command)
    async def command(command_id: str) -> m.Command:
        rec = core.commands.get(command_id)
        if rec is None:
            raise ClassroomError("command_not_found", "Команда не найдена", 404)
        return rec.model()

    @t.post("/api/teacher/commands/{command_id}/cancel", response_model=m.Command)
    async def cancel(command_id: str) -> m.Command:
        return core.cancel_command(command_id)

    # v1 §5 paths delegated to T03 — core fallbacks only when no feature registered them
    async def incidents_fallback(student_id: str) -> list[m.Incident]:
        return core.incidents_for(_student(student_id).student_id)

    async def not_installed(request: Request) -> Response:
        return api_error("feature_not_installed", "Модуль истории и клипов (T03) не подключён в этой сборке", 501)

    if ("GET", "/api/teacher/students/{student_id}/incidents") not in taken:
        t.add_api_route("/api/teacher/students/{student_id}/incidents", incidents_fallback, methods=["GET"], response_model=list[m.Incident])
    for method, path in (("GET", "/api/teacher/clips/{incident_id}"), ("POST", "/api/teacher/students/{student_id}/decision"), ("POST", "/api/student/clips/{incident_id}")):
        if (method, path) not in taken:
            t.add_api_route(path, not_installed, methods=[method], include_in_schema=False)

    @t.post("/api/student/ping")
    async def student_ping(request: Request) -> dict[str, Any]:
        st = student_of(request)
        assert st is not None  # the gate already rejected invalid tokens
        return {"student_id": st.student_id, "server_time": m.utc_now().isoformat()}

    for route in t.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                if (method, route.path) in taken:
                    raise RuntimeError(f"core route {method} {route.path} collides with a feature route")
    app.include_router(t)

    # ---------------------------------------------------------------------------------- student socket
    @app.websocket("/ws/student")
    async def ws_student(ws: WebSocket) -> None:
        await ws.accept()
        ip = ws.client.host if ws.client else "?"
        try:
            raw = await asyncio.wait_for(ws.receive(), timeout=config.hello_timeout_s)
        except (asyncio.TimeoutError, WebSocketDisconnect):
            await _close(ws, 4400, "hello_timeout")
            return
        text = raw.get("text")
        if raw.get("type") != "websocket.receive" or not isinstance(text, str) or len(text.encode()) > config.max_ws_message_bytes:
            await _reject(ws, "bad_hello", "Первым сообщением должен быть hello", 4400)
            return
        try:
            hello = m.Hello.model_validate_json(text)
        except ValidationError:
            await _reject(ws, "bad_hello", "Сообщение hello не соответствует протоколу qorgau.class.v1", 4400)
            return
        try:
            st, token, resumed = core.pair(hello, ip)
        except ClassroomError as exc:
            await _reject(ws, exc.code, exc.message_ru, 4429 if exc.status == 429 else 4403)
            return
        conn = StudentConnection(st.student_id, next(epochs), asyncio.get_running_loop())
        exam = core.exam_for(st)
        override = features.student_connected(core.student_model(st), json.loads(text))
        if override:
            try:
                exam = m.ExamPolicy.model_validate({**exam.model_dump(mode="json"), **override})
            except ValidationError:
                log.error("feature exam override rejected (off-contract)")
        welcome = m.Welcome(**envelope(), student_id=st.student_id, resume_token=token, server_time=m.utc_now(), exam=exam, session_id=st.session_id, resumed=resumed)
        try:
            await ws.send_text(welcome.model_dump_json())
        except Exception:
            return
        core.attach(st, conn)
        reader = asyncio.create_task(_student_reader(ws, st, conn))
        writer = asyncio.create_task(_writer(ws, conn))
        pinger = asyncio.create_task(_pinger(conn))
        closer = asyncio.create_task(conn.close_event.wait())
        try:
            await asyncio.wait({reader, writer, pinger, closer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (reader, writer, pinger, closer):
                task.cancel()  # a silent peer never answers: never wait on its close frame
            await _close(ws, conn.close_code if conn.closed else 1000, conn.close_reason or "bye")
            conn.closed = True
            if core.detach(st, conn):
                features.student_disconnected(st.student_id)

    async def _student_reader(ws: WebSocket, st: StudentRec, conn: StudentConnection) -> None:
        while True:
            raw = await ws.receive()
            if raw["type"] == "websocket.disconnect":
                conn.request_close(1000, "client_closed")
                return
            conn.last_pong = time.monotonic()  # any message proves the peer is alive
            core.touch(st)
            text = raw.get("text")
            if not isinstance(text, str) or len(text.encode()) > config.max_ws_message_bytes:
                core.counters["invalid_messages"] += 1
                conn.enqueue(envelope(type="error", code="message_too_large", message_ru="Сообщение больше 256 КБ или не текст"))
                continue
            try:
                data = json.loads(text)
                kind = data.get("type") if isinstance(data, dict) else None
            except ValueError:
                data, kind = None, None
            if kind not in ("hello", "status", "incident", "preview", "ack", "command_progress", "audio_signal", "pong"):
                core.counters["unknown_types"] += 1
                log.info("student %s: ignored message type %r", st.student_id, kind)
                continue
            try:
                msg = STUDENT_MESSAGE.validate_python(data)
            except ValidationError as exc:
                core.counters["invalid_messages"] += 1
                conn.enqueue(envelope(type="error", code="invalid_message", message_ru=f"Сообщение {kind} не соответствует протоколу: {str(exc.errors()[0].get('msg', ''))[:120]}"))
                continue
            try:
                await _dispatch(st, conn, msg)
            except ClassroomError as exc:
                conn.enqueue(envelope(type="error", code=exc.code, message_ru=exc.message_ru[:300]))
            if kind not in ("pong", "preview", "hello"):
                features.student_message(st.student_id, data)

    async def _dispatch(st: StudentRec, conn: StudentConnection, msg: Any) -> None:
        if isinstance(msg, m.Status):
            core.on_status(st, msg)
        elif isinstance(msg, m.IncidentMsg):
            core.on_incident(st, msg)
        elif isinstance(msg, m.Preview):
            core.on_preview(st, msg)
        elif isinstance(msg, m.Ack):
            core.on_ack(st, msg)
        elif isinstance(msg, m.CommandProgress):
            core.on_progress(st, msg)
        elif isinstance(msg, m.AudioSignalIn):
            core.relay_student_signal(st, msg)
        elif isinstance(msg, m.Hello):
            raise ClassroomError("already_joined", "hello уже получен на этом соединении", 409)

    async def _writer(ws: WebSocket, conn: StudentConnection) -> None:
        while True:
            message, on_written = await conn.queue.get()
            await ws.send_text(dumps(message))
            if on_written is not None:
                on_written()

    async def _pinger(conn: StudentConnection) -> None:
        while True:
            await asyncio.sleep(config.ping_interval_s)
            if time.monotonic() - conn.last_pong > config.pong_timeout_s:
                conn.request_close(4408, "heartbeat_timeout")
                return
            conn.enqueue(envelope(type="ping"))

    # ---------------------------------------------------------------------------------- teacher socket
    @app.websocket("/ws/teacher")
    async def ws_teacher(ws: WebSocket) -> None:
        await ws.accept()
        if teacher_auth.check(ws.cookies.get(TEACHER_COOKIE)) is None:
            await _close(ws, 4401, "unauthorized")
            return
        inline = ws.query_params.get("inline_previews") == "1"
        client = hub.add(inline)
        try:
            info_msg = m.ServerInfo(server_version=SERVER_VERSION, server_time=m.utc_now(), session=core.session_model(core.current_session()), features=features.status(), **core.info_counts())
            client.offer({"type": "hello", "info": info_msg.model_dump(mode="json")})
            client.offer({"type": "snapshot", "students": [c.model_dump(mode="json") for c in core.cards()]})

            async def pump() -> None:
                while True:
                    await ws.send_text(dumps(await client.queue.get()))

            async def listen() -> None:
                while True:
                    raw = await ws.receive()
                    if raw["type"] == "websocket.disconnect":
                        return
                    try:
                        sig = m.TeacherAudioSignal.model_validate_json(raw.get("text") or "")
                        core.relay_teacher_signal(sig)
                    except ValidationError:
                        client.offer({"type": "error", "code": "invalid_message", "message_ru": "Ожидалось сообщение audio_signal по контракту"})
                    except ClassroomError as exc:
                        client.offer({"type": "error", "code": exc.code, "message_ru": exc.message_ru[:300]})

            tasks = {asyncio.create_task(pump()), asyncio.create_task(listen())}
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.remove(client)
            await _close(ws, 1000, "bye")

    # ---------------------------------------------------------------------------------- teacher UI
    @app.middleware("http")
    async def _headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    if (config.ui_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(config.ui_dir), html=True), name="teacher-ui")
    else:
        @app.get("/", include_in_schema=False)
        async def no_ui() -> HTMLResponse:
            return HTMLResponse(_NO_UI_PAGE)

    app.add_middleware(Gate, state=state)
    return app


async def _ticker(core: ClassroomCore, features: FeatureManager, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            core.tick()
            features.tick()
        except Exception:
            log.exception("tick failed")


async def _reject(ws: WebSocket, code: str, message_ru: str, close_code: int) -> None:
    try:
        await ws.send_text(dumps(envelope(type="error", code=code, message_ru=message_ru)))
    except Exception:
        pass
    await _close(ws, close_code, code)


async def _close(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await asyncio.wait_for(ws.close(code=code, reason=reason[:120]), timeout=2.0)
    except Exception:
        pass


_NO_UI_PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Qorgau Classroom</title>
<body style="font-family:sans-serif;max-width:40rem;margin:2rem auto">
<h1>Сервер класса Qorgau работает</h1>
<p>Панель преподавателя ещё не собрана. Соберите её: <code>cd proctoring/classroom/teacher-ui && npm ci && npm run build</code>,
затем перезапустите сервер. API: <code>/api/teacher/info</code> (после входа по PIN).</p></body></html>"""
