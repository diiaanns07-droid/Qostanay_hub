"""Reference/dev signaling server for qorgau.class.audio.v1 — NOT the class server (C1).

Implements only what audio needs from qorgau.class.v1: teacher login (one-time PIN → HttpOnly cookie), loopback-only
teacher endpoints, `/ws/teacher`, `/ws/student` (hello/welcome/resume_token/ping), `status.mic_active`, and serves the
teacher audio page and the labelled TEST PEER. C1 should mount `AudioHub` instead of running this file.

    PYTHONPATH=proctoring/class-audio/server python -m qorgau_class_audio.devserver \
        --host 127.0.0.1 --port 8765 [--tls-cert cert.pem --tls-key key.pem] [--pin-file PATH]

`--host 0.0.0.0` exposes /ws/student and /test-peer/ to the LAN; teacher routes stay loopback-only regardless.
Without TLS, a browser on ANOTHER computer is not a secure context: getUserMedia is unavailable there (see LAN.md).
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

from .hub import AudioHub
from .protocol import MAX_MESSAGE_BYTES, PROTOCOL, STUDENT_AUDIO_TYPES, TEACHER_TYPES, envelope, utc_now

log = logging.getLogger("qorgau.class.audio.dev")
WEB = Path(__file__).resolve().parents[2] / "web"
LOOPBACK = {"127.0.0.1", "::1"}
COOKIE = "qorgau_teacher"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "media-src 'self' blob: mediastream:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


class WsLink:
    """Link for AudioHub over a Starlette WebSocket; serialises sends."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.lock = asyncio.Lock()
        self.closed = False

    async def send(self, msg: dict[str, Any]) -> None:
        if self.closed:
            raise ConnectionError("closed")
        async with self.lock:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False))

    async def close(self, code: int, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self.ws.close(code=code, reason=reason)
        except Exception:
            pass


class Student:
    def __init__(self, student_id: str, resume_token: str, label: str, computer: str) -> None:
        self.student_id = student_id
        self.resume_token = resume_token
        self.label = label
        self.computer = computer
        self.link: WsLink | None = None
        self.status: dict[str, Any] = {}
        self.last_seen = 0.0

    def card(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "student_label": self.label,
            "computer_name": self.computer,
            "connected": self.link is not None and not self.link.closed,
            "mic_active": bool(self.status.get("mic_active", False)),
            "last_status_at": self.status.get("_at"),
        }


class State:
    def __init__(self, pin: str, secure_cookie: bool) -> None:
        self.pin = pin
        self.secure_cookie = secure_cookie
        self.join_code = f"{secrets.randbelow(10**6):06d}"
        self.tokens: dict[str, str] = {}  # cookie token -> teacher_id
        self.teacher_links: dict[str, set[WsLink]] = {}
        self.students: dict[str, Student] = {}
        self.by_resume: dict[str, str] = {}
        self.failures: dict[str, tuple[int, float]] = {}  # ip -> (count, locked_until)
        self.hub = AudioHub()

    def throttled(self, ip: str) -> bool:
        n, until = self.failures.get(ip, (0, 0.0))
        return time.monotonic() < until

    def fail(self, ip: str) -> None:
        n, _ = self.failures.get(ip, (0, 0.0))
        n += 1
        self.failures[ip] = (0, time.monotonic() + 30) if n >= 5 else (n, 0.0)

    async def broadcast_teachers(self, msg: dict[str, Any]) -> None:
        for links in self.teacher_links.values():
            for link in list(links):
                try:
                    await link.send(msg)
                except Exception:
                    pass


def create_app(pin: str, *, secure_cookie: bool = False) -> FastAPI:
    st = State(pin, secure_cookie)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await st.hub.shutdown()  # server stop → every student gets audio_stop (fail closed)

    app = FastAPI(
        title="qorgau class audio — DEV signaling server", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.audio = st

    def client_ip(conn: Request | WebSocket) -> str:
        return conn.client.host if conn.client else ""

    def teacher_of(conn: Request | WebSocket) -> str | None:
        tok = conn.cookies.get(COOKIE)
        return st.tokens.get(tok) if tok else None

    def same_origin(conn: Request | WebSocket) -> bool:
        origin = conn.headers.get("origin")
        host = conn.headers.get("host", "")
        if origin is None:
            return isinstance(conn, Request)  # plain navigations/fetch same-origin GET may omit Origin
        return origin in (f"http://{host}", f"https://{host}")

    def static(rel: str) -> Response:
        p = (WEB / rel).resolve()
        if not str(p).startswith(str(WEB)) or not p.is_file():
            return Response(status_code=404)
        return FileResponse(p, media_type=MIME.get(p.suffix, "application/octet-stream"), headers=security_headers())

    def security_headers() -> dict[str, str]:
        return {
            "Content-Security-Policy": CSP,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "microphone=(self), camera=()",
            "Cache-Control": "no-store",
        }

    def loopback_only(request: Request) -> Response | None:
        if client_ip(request) not in LOOPBACK:
            return JSONResponse({"error": "teacher_loopback_only", "message_ru": "Пульт преподавателя доступен только на его компьютере."}, status_code=403)
        return None

    # ------------------------------------------------------------------ teacher pages + API (loopback only)
    @app.get("/")
    async def teacher_page(request: Request) -> Response:
        return loopback_only(request) or static("teacher/index.html")

    @app.get("/teacher/{path:path}")
    async def teacher_static(path: str, request: Request) -> Response:
        return loopback_only(request) or static(f"teacher/{path}")

    @app.get("/shared/{path:path}")
    async def shared_static(path: str) -> Response:
        return static(f"shared/{path}")

    @app.get("/test-peer/{path:path}")
    async def peer_static(path: str) -> Response:
        return static(f"test-peer/{path or 'index.html'}")

    @app.post("/api/teacher/login")
    async def login(request: Request) -> Response:
        denied = loopback_only(request)
        if denied:
            return denied
        if not same_origin(request):
            return JSONResponse({"error": "bad_origin"}, status_code=403)
        ip = client_ip(request)
        if st.throttled(ip):
            return JSONResponse({"error": "rate_limited", "message_ru": "Слишком много попыток, подождите 30 с."}, status_code=429)
        try:
            body = await request.json()
        except Exception:
            body = {}
        pin_in = str(body.get("pin", ""))
        if not hmac.compare_digest(pin_in.encode(), st.pin.encode()):
            st.fail(ip)
            return JSONResponse({"error": "wrong_pin", "message_ru": "Неверный PIN."}, status_code=401)
        token = secrets.token_hex(32)
        st.tokens[token] = "teacher-1"  # MVP: one teacher account
        r = JSONResponse({"ok": True})
        r.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=st.secure_cookie, path="/")
        return r

    @app.post("/api/teacher/logout")
    async def logout(request: Request) -> Response:
        denied = loopback_only(request)
        if denied:
            return denied
        tok = request.cookies.get(COOKIE)
        teacher_id = st.tokens.pop(tok, None) if tok else None
        if teacher_id:
            # Authorization lost: audio is closed BEFORE the sockets go away.
            await st.hub.teacher_auth_lost(teacher_id)
            if not any(t == teacher_id for t in st.tokens.values()):
                for link in list(st.teacher_links.get(teacher_id, ())):
                    await link.close(4401, "logged out")
        r = JSONResponse({"ok": True})
        r.delete_cookie(COOKIE, path="/")
        return r

    @app.get("/api/teacher/session")
    async def session_info(request: Request) -> Response:
        denied = loopback_only(request)
        if denied:
            return denied
        if not teacher_of(request):
            return JSONResponse({"error": "not_authorized"}, status_code=401)
        return JSONResponse({"join_code": st.join_code, "protocol": PROTOCOL})

    @app.get("/api/teacher/students")
    async def students(request: Request) -> Response:
        denied = loopback_only(request)
        if denied:
            return denied
        if not teacher_of(request):
            return JSONResponse({"error": "not_authorized"}, status_code=401)
        return JSONResponse([s.card() for s in st.students.values()])

    @app.websocket("/ws/teacher")
    async def ws_teacher(ws: WebSocket) -> None:
        if client_ip(ws) not in LOOPBACK or not same_origin(ws):
            await ws.close(code=4403)
            return
        teacher_id = teacher_of(ws)
        if not teacher_id:
            await ws.close(code=4401)
            return
        await ws.accept()
        link = WsLink(ws)
        st.teacher_links.setdefault(teacher_id, set()).add(link)
        st.hub.teacher_online(teacher_id, link)
        try:
            await link.send(envelope("teacher_hello", students=[s.card() for s in st.students.values()]))
            while True:
                text = await ws.receive_text()
                if len(text.encode()) > MAX_MESSAGE_BYTES:
                    continue
                # Authorization is re-checked per message: a revoked cookie stops everything.
                if teacher_of(ws) != teacher_id:
                    await st.hub.teacher_auth_lost(teacher_id)
                    await link.close(4401, "authorization lost")
                    break
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") in TEACHER_TYPES:
                    await st.hub.on_teacher_message(link, msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            link.closed = True
            st.teacher_links.get(teacher_id, set()).discard(link)
            await st.hub.teacher_offline(link)

    # ------------------------------------------------------------------ student side (LAN)
    @app.websocket("/ws/student")
    async def ws_student(ws: WebSocket) -> None:
        ip = client_ip(ws)
        await ws.accept()
        link = WsLink(ws)
        student: Student | None = None
        try:
            if st.throttled(ip):
                await link.send(envelope("error", code="join_rejected", message_ru="Слишком много попыток, подождите 30 с."))
                await link.close(4429, "throttled")
                return
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
            if not isinstance(hello, dict) or hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL:
                await link.close(4400, "hello expected")
                return
            token = hello.get("resume_token")
            if isinstance(token, str) and token in st.by_resume:
                student = st.students[st.by_resume[token]]
                old = student.link
                if old and not old.closed:
                    await old.close(4409, "replaced by a newer connection")
                    await st.hub.student_offline(student.student_id, old)
            elif hmac.compare_digest(str(hello.get("join_code", "")).encode(), st.join_code.encode()):
                sid = f"st-{secrets.token_hex(4)}"
                student = Student(
                    sid, secrets.token_hex(32), str(hello.get("student_label", ""))[:64], str(hello.get("computer_name", ""))[:64]
                )
                st.students[sid] = student
                st.by_resume[student.resume_token] = sid
            else:
                st.fail(ip)
                await link.send(envelope("error", code="join_rejected", message_ru="Неверный код подключения."))
                await link.close(4401, "join rejected")
                return
            student.link = link
            student.last_seen = time.monotonic()
            st.hub.student_online(student.student_id, link)
            await link.send(
                envelope(
                    "welcome",
                    student_id=student.student_id,
                    resume_token=student.resume_token,
                    server_time=utc_now(),
                    exam={"exam_id": "audio-dev", "title": "Проверка аудиосвязи (DEV)", "mode": "app", "allowed_urls": [], "allowed_apps": [], "instructions_ru": ""},
                )
            )
            await st.broadcast_teachers(envelope("student_update", student=student.card()))
            pinger = asyncio.create_task(_ping_loop(link, student))
            try:
                while True:
                    text = await ws.receive_text()
                    if len(text.encode()) > MAX_MESSAGE_BYTES:
                        continue
                    try:
                        msg = json.loads(text)
                    except ValueError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    student.last_seen = time.monotonic()
                    t = msg.get("type")
                    if t == "status":
                        student.status = {**msg, "_at": utc_now()}
                        await st.broadcast_teachers(envelope("student_update", student=student.card()))
                    elif t == "ack" and st.hub.owns_command(msg.get("command_id")):
                        await st.hub.on_student_ack(student.student_id, msg)
                    elif t in STUDENT_AUDIO_TYPES:
                        await st.hub.on_student_message(student.student_id, msg)
            finally:
                pinger.cancel()
        except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError, ValueError):
            pass
        finally:
            link.closed = True
            if student is not None and student.link is link:
                student.link = None
                student.status["mic_active"] = False
                await st.hub.student_offline(student.student_id, link)
                await st.broadcast_teachers(envelope("student_update", student=student.card()))

    async def _ping_loop(link: WsLink, student: Student) -> None:
        while not link.closed:
            await asyncio.sleep(5)
            if time.monotonic() - student.last_seen > 15:
                await link.close(4408, "no pong")  # v1: no pong 15 s → offline
                return
            try:
                await link.send(envelope("ping"))
            except Exception:
                return

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--tls-cert")
    ap.add_argument("--tls-key")
    ap.add_argument("--pin-file", help="write the one-time teacher PIN and join code here (0600) instead of only printing")
    args = ap.parse_args()
    logging.basicConfig(level=os.environ.get("QORGAU_AUDIO_LOG", "INFO"))
    pin = f"{secrets.randbelow(10**6):06d}"
    tls = bool(args.tls_cert and args.tls_key)
    app = create_app(pin, secure_cookie=tls)
    st: State = app.state.audio
    scheme = "https" if tls else "http"
    print(f"[qorgau class audio DEV] teacher PIN: {pin}   join code: {st.join_code}", flush=True)
    print(f"[qorgau class audio DEV] teacher: {scheme}://127.0.0.1:{args.port}/   test peer: {scheme}://<this-ip>:{args.port}/test-peer/", flush=True)
    if args.pin_file:
        fd = os.open(args.pin_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"pin": pin, "join_code": st.join_code}, f)
    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ssl_certfile=args.tls_cert if tls else None,
        ssl_keyfile=args.tls_key if tls else None,
        log_level="warning",
        ws_max_size=MAX_MESSAGE_BYTES,
    )


if __name__ == "__main__":
    main()
