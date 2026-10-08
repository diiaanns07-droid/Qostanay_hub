"""DEV HARNESS for T03 — NOT the class server (C1). It implements only the qorgau.class.v1 pieces needed to
exercise episodes, clips and decisions end to end:

  teacher (loopback only + PIN cookie): POST /api/teacher/login, POST /api/teacher/session,
      GET /api/teacher/students, POST /api/teacher/students/{id}/commands (request_clip only), WS /ws/teacher
  student: WS /ws/student (hello/welcome, status, incident, ack, pong)
  T03 router (real): /api/teacher/students/{id}/incidents, /api/teacher/clips/{incident_id},
      /api/teacher/students/{id}/decision, /api/student/clips/{incident_id}
  UI: /  (DEV host page) and /review/* (the T03 module)

    cd proctoring && python -m classreview.devserver [--host 127.0.0.1] [--port 8765] [--data-dir DIR]

The teacher PIN is printed once to the console. Default bind is loopback; --host 0.0.0.0 exposes the student
endpoints to the LAN (teacher endpoints still require a loopback client).
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROCTORING = Path(__file__).resolve().parents[1]
for _p in (PROCTORING / "backend", PROCTORING / "contracts" / "python"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))  # A05 zone rule (proctor.fusion.zones), optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402

from . import UI_DIR  # noqa: E402
from .config import ReviewConfig  # noqa: E402
from .router import AuthError, create_review_router, error  # noqa: E402
from .store import ReviewError, ReviewStore  # noqa: E402

log = logging.getLogger("classreview.dev")
PROTOCOL = "qorgau.class.v1"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
COOKIE = "qorgau_teacher"
UI_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self';"
    " connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
UI_FILES = {"dev.html": "text/html; charset=utf-8", "dev-host.js": "text/javascript", "review-module.js": "text/javascript",
            "register.js": "text/javascript", "review.css": "text/css"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def envelope(msg_type: str, **fields: Any) -> str:
    return json.dumps({"type": msg_type, "v": 1, "msg_id": str(uuid.uuid4()), "sent_at": now_iso(), **fields}, ensure_ascii=False)


class DevClassServer:
    def __init__(self, store: ReviewStore, *, pin: str | None = None, trusted_hosts: set[str] | None = None):
        self.store = store
        self.pin = pin or f"{secrets.randbelow(10**6):06d}"
        self.cookie_secret = secrets.token_hex(32)
        self.trusted = LOOPBACK | (trusted_hosts or set())
        self.class_session_id: str | None = None
        self.join_code: str | None = None
        self.students: dict[str, dict[str, Any]] = {}  # student_id -> card
        self.tokens: dict[str, str] = {}  # resume_token -> student_id
        self.sockets: dict[str, WebSocket] = {}
        self.teachers: set[WebSocket] = set()
        self.commands: dict[str, dict[str, Any]] = {}
        self.failures: dict[str, list[float]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        store.on_change(self._on_store_change)

    # --------------------------------------------------------------- auth
    def teacher_guard(self, request: Request) -> str:
        host = request.client.host if request.client else ""
        if host not in self.trusted:
            raise AuthError(403, "teacher_loopback_only", "Панель преподавателя доступна только на компьютере преподавателя")
        cookie = request.cookies.get(COOKIE, "")
        if not hmac.compare_digest(cookie.encode(), self.cookie_secret.encode()):
            raise AuthError(401, "teacher_auth_required", "Нужен вход преподавателя (PIN из консоли сервера)")
        return "teacher"

    def resolve_student(self, token: str) -> str | None:
        for known, sid in self.tokens.items():
            if hmac.compare_digest(known.encode(), token.encode()):
                return sid
        return None

    def status_of(self, student_id: str) -> dict[str, Any] | None:
        card = self.students.get(student_id)
        return {"zone": card.get("zone"), "monitoring": card.get("monitoring")} if card else None

    # --------------------------------------------------------------- push to /ws/teacher
    def _on_store_change(self, event: dict[str, Any]) -> None:
        if self.loop is None:
            return
        payload = envelope("incident", student_id=event["student_id"], incident=event["incident"])
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._broadcast(payload)))

    async def _broadcast(self, payload: str) -> None:
        for ws in list(self.teachers):
            try:
                await ws.send_text(payload)
            except Exception:
                self.teachers.discard(ws)

    # --------------------------------------------------------------- app
    def build(self) -> FastAPI:
        server = self

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            server.loop = asyncio.get_running_loop()
            yield

        app = FastAPI(title="Qorgau Class — T03 DEV harness", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
        app.include_router(
            create_review_router(self.store, teacher_guard=self.teacher_guard, student_resolver=self.resolve_student,
                                 status_provider=self.status_of)
        )

        def guarded(request: Request) -> Response | None:
            try:
                self.teacher_guard(request)
            except AuthError as exc:
                return error(exc.status, exc.code, exc.message_ru)
            return None

        @app.post("/api/teacher/login")
        async def login(request: Request) -> Response:
            host = request.client.host if request.client else ""
            if host not in server.trusted:
                return error(403, "teacher_loopback_only", "Вход преподавателя только на компьютере преподавателя")
            try:
                body = await request.json()
            except Exception:
                return error(400, "invalid_json", "Тело запроса — не JSON")
            pin = str(body.get("pin", "")) if isinstance(body, dict) else ""
            if not hmac.compare_digest(pin.encode(), server.pin.encode()):
                return error(401, "wrong_pin", "Неверный PIN")
            resp = JSONResponse({"ok": True})
            resp.set_cookie(COOKIE, server.cookie_secret, httponly=True, samesite="strict", path="/")
            return resp

        @app.post("/api/teacher/session")
        async def create_session(request: Request) -> Response:
            if (denied := guarded(request)) is not None:
                return denied
            server.class_session_id = f"cls-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
            server.join_code = f"{secrets.randbelow(10**6):06d}"
            server.store.open_class_session(server.class_session_id)
            return JSONResponse({"join_code": server.join_code, "class_session_id": server.class_session_id})

        @app.get("/api/teacher/students")
        def students(request: Request) -> Response:
            if (denied := guarded(request)) is not None:
                return denied
            out = []
            known = {row["student_id"] for row in server.store.list_students()}
            cards = {**{sid: {"student_label": "", "computer_name": "", "restored": True} for sid in known}, **server.students}
            for sid, card in cards.items():
                eps = server.store.list_incidents(sid)
                public = {k: v for k, v in card.items() if not k.startswith("_")}  # never expose resume tokens
                out.append({**public, "student_id": sid, "connected": sid in server.sockets,
                            "incidents_total": len(eps), "incidents_unreviewed": sum(1 for e in eps if e["decision"] is None)})
            return JSONResponse(out, headers={"Cache-Control": "no-store"})

        @app.post("/api/teacher/students/{student_id}/commands")
        async def command(student_id: str, request: Request) -> Response:
            if (denied := guarded(request)) is not None:
                return denied
            try:
                body = await request.json()
            except Exception:
                return error(400, "invalid_json", "Тело запроса — не JSON")
            if not isinstance(body, dict) or body.get("kind") != "request_clip":
                return error(501, "dev_harness_command", "DEV-стенд T03 поддерживает только request_clip")
            payload = body.get("payload")
            incident_id = payload.get("incident_id") if isinstance(payload, dict) else None
            if not isinstance(incident_id, str):
                return error(422, "invalid_body", "payload.incident_id обязателен")
            ws = server.sockets.get(student_id)
            if ws is None:
                return error(409, "student_offline", "Студент не подключён")
            command_id = str(uuid.uuid4())
            try:
                server.store.note_clip_requested(student_id, str(incident_id), command_id)
            except ReviewError as exc:
                return error(exc.status, exc.code, exc.message_ru)
            server.commands[command_id] = {"student_id": student_id, "kind": "request_clip", "sent": time.monotonic()}
            await ws.send_text(envelope("command", command_id=command_id, kind="request_clip", payload={"incident_id": incident_id}))
            return JSONResponse({"command_id": command_id})

        @app.websocket("/ws/teacher")
        async def ws_teacher(ws: WebSocket) -> None:
            host = ws.client.host if ws.client else ""
            if host not in server.trusted or not hmac.compare_digest(ws.cookies.get(COOKIE, "").encode(), server.cookie_secret.encode()):
                await ws.close(code=4401)
                return
            await ws.accept()
            server.teachers.add(ws)
            try:
                while True:
                    await ws.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                server.teachers.discard(ws)

        @app.websocket("/ws/student")
        async def ws_student(ws: WebSocket) -> None:
            await ws.accept()
            ip = ws.client.host if ws.client else "?"
            recent = [t for t in server.failures.get(ip, []) if time.monotonic() - t < 30]
            if len(recent) >= 5:
                await ws.send_text(envelope("error", code="join_paused", message_ru="Слишком много попыток, подождите 30 с"))
                await ws.close(code=1008)
                return
            try:
                hello = json.loads(await ws.receive_text())
            except Exception:
                await ws.close(code=1003)
                return
            sid = None
            if hello.get("type") == "hello" and hello.get("protocol") == PROTOCOL:
                if hello.get("resume_token"):
                    sid = server.resolve_student(str(hello["resume_token"]))
                elif server.join_code and hmac.compare_digest(str(hello.get("join_code", "")).encode(), server.join_code.encode()):
                    sid = f"stu-{secrets.token_hex(4)}"
                    token = secrets.token_hex(32)
                    server.tokens[token] = sid
                    server.students[sid] = {"student_label": str(hello.get("student_label", ""))[:64],
                                            "computer_name": str(hello.get("computer_name", ""))[:64], "_token": token}
            if sid is None or server.class_session_id is None:
                server.failures.setdefault(ip, []).append(time.monotonic())
                await ws.send_text(envelope("error", code="join_rejected", message_ru="Неверный код подключения"))
                await ws.close(code=1008)
                return
            card = server.students[sid]
            await ws.send_text(envelope("welcome", student_id=sid, resume_token=card["_token"], server_time=now_iso(),
                                        exam={"exam_id": "dev", "title": "DEV", "mode": "app", "allowed_urls": [],
                                              "allowed_apps": [], "instructions_ru": "DEV-стенд T03"}))
            server.sockets[sid] = ws
            try:
                while True:
                    raw = await ws.receive_text()
                    if len(raw) > 256 * 1024:
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    kind = msg.get("type")
                    if kind == "status":
                        for key in ("exam_state", "camera", "monitoring", "zone", "zone_reasons_ru", "locked", "mic_active"):
                            if key in msg:
                                card[key] = msg[key]
                        card["last_status_at"] = now_iso()
                    elif kind == "incident":
                        try:
                            await asyncio.to_thread(server.store.ingest_incident, sid, msg, class_session_id=server.class_session_id)
                        except ReviewError as exc:
                            await ws.send_text(envelope("error", code=exc.code, message_ru=exc.message_ru))
                    elif kind == "ack":
                        cmd_id = str(msg.get("command_id", ""))
                        if server.commands.pop(cmd_id, None) is not None:
                            await asyncio.to_thread(server.store.note_command_ack, cmd_id, bool(msg.get("ok")), msg.get("error_ru"))
                    # pong / audio_signal / preview / unknown: ignored by the DEV harness
            except WebSocketDisconnect:
                pass
            finally:
                if server.sockets.get(sid) is ws:
                    server.sockets.pop(sid, None)

        @app.get("/")
        def index() -> Response:
            return FileResponse(UI_DIR / "dev.html", media_type=UI_FILES["dev.html"],
                                headers={"Content-Security-Policy": UI_CSP, "Cache-Control": "no-store"})

        @app.get("/review/{name}")
        def ui_file(name: str) -> Response:
            if name not in UI_FILES:
                return error(404, "not_found", "Нет такого файла")
            return FileResponse(UI_DIR / name, media_type=UI_FILES[name],
                                headers={"Content-Security-Policy": UI_CSP, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

        return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Qorgau Class T03 DEV harness (not the C1 class server)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--pin", default=None, help="fixed teacher PIN (tests/demo); default: random, printed once")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = ReviewConfig.from_env(**({"data_dir": args.data_dir} if args.data_dir else {}))
    store = ReviewStore(cfg)
    store.open()
    server = DevClassServer(store, pin=args.pin)
    print(f"Qorgau Class T03 DEV harness — НЕ сервер класса C1. Данные: {cfg.data_dir}", flush=True)
    print(f"PIN преподавателя: {server.pin}   Панель: http://127.0.0.1:{args.port}/", flush=True)
    import uvicorn

    try:
        uvicorn.run(server.build(), host=args.host, port=args.port, log_level="warning")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
