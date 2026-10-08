"""T03 HTTP routes of qorgau.class.v1 §5 (episodes, clips, decisions), mounted by the class server (C1).

    router = create_review_router(store, teacher_guard=..., student_resolver=..., status_provider=...)
    app.include_router(router)

Authorization is injected, never assumed:
  * teacher_guard(request) -> operator label; raises/returns 401/403 itself (C1: loopback + teacher cookie);
  * student_resolver(resume_token) -> student_id | None (C1: tokens issued in `welcome`);
  * status_provider(student_id) -> {"zone", "monitoring"} | None (optional, C1's last `status`).
Without a guard/resolver every request is refused (secure default).

Errors: {"error": {"code": "...", "message_ru": "..."}}.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .config import CLIP_MEDIA_TYPES
from .media import BROWSER_CODECS, RangeNotSatisfiable, base_media_type, iter_file, parse_range
from .store import ID_RE, ReviewError, ReviewStore

TeacherGuard = Callable[[Request], str]
StudentResolver = Callable[[str], "str | None"]
StatusProvider = Callable[[str], "dict[str, Any] | None"]

NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class AuthError(Exception):
    def __init__(self, status: int, code: str, message_ru: str):
        super().__init__(message_ru)
        self.status, self.code, self.message_ru = status, code, message_ru


def error(status: int, code: str, message_ru: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message_ru": message_ru}}, status_code=status, headers={**NO_STORE, **(headers or {})})


def _deny_teacher(request: Request) -> str:
    raise AuthError(401, "teacher_auth_not_configured", "Вход преподавателя не настроен на сервере")


def _deny_student(token: str) -> None:
    return None


def create_review_router(
    store: ReviewStore,
    *,
    teacher_guard: TeacherGuard | None = None,
    student_resolver: StudentResolver | None = None,
    status_provider: StatusProvider | None = None,
) -> APIRouter:
    guard = teacher_guard or _deny_teacher
    resolve = student_resolver or _deny_student
    router = APIRouter()

    def teacher(request: Request) -> str:
        return guard(request)

    def handle(fn: Callable[[], Response]) -> Response:
        try:
            return fn()
        except AuthError as exc:
            return error(exc.status, exc.code, exc.message_ru)
        except ReviewError as exc:
            return error(exc.status, exc.code, exc.message_ru)

    # ------------------------------------------------------------------ student: clip upload
    @router.post("/api/student/clips/{incident_id}")
    async def upload_clip(incident_id: str, request: Request) -> Response:
        if not ID_RE.fullmatch(incident_id):
            return error(422, "invalid_id", "Некорректный incident_id")
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        student_id = await run_in_threadpool(resolve, token) if token else None
        if not student_id:
            return error(401, "student_auth_failed", "Нужен заголовок Authorization: Bearer <resume_token>", {"WWW-Authenticate": "Bearer"})
        media_type = base_media_type(request.headers.get("content-type"))
        if media_type not in CLIP_MEDIA_TYPES:
            return error(415, "unsupported_media_type", "Content-Type: video/mp4 или video/x-msvideo")
        limit = store.config.max_clip_bytes
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                return error(400, "bad_content_length", "Некорректный Content-Length")
            if int(length) > limit:
                return error(413, "clip_too_large", f"Клип больше {limit // (1024 * 1024)} МБ")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit:
                return error(413, "clip_too_large", f"Клип больше {limit // (1024 * 1024)} МБ")
        source = request.headers.get("x-qorgau-clip-source", "unspecified").strip().lower()
        try:
            result = await run_in_threadpool(store.store_clip, student_id, incident_id, media_type, bytes(body), source)
        except ReviewError as exc:
            return error(exc.status, exc.code, exc.message_ru)
        status = 201 if result["status"] == "stored" else 200
        return JSONResponse(result, status_code=status, headers=NO_STORE)

    # ------------------------------------------------------------------ teacher: episodes
    @router.get("/api/teacher/students/{student_id}/incidents")
    def list_incidents(student_id: str, request: Request) -> Response:
        def run() -> Response:
            teacher(request)
            if not ID_RE.fullmatch(student_id):
                raise ReviewError(422, "invalid_id", "Некорректный student_id")
            status = None
            if status_provider is not None:
                try:
                    status = status_provider(student_id)
                except Exception:
                    status = None
            incidents = store.list_incidents(student_id)
            review = store.review_summary(
                student_id,
                reported_zone=(status or {}).get("zone"),
                monitoring=(status or {}).get("monitoring"),
            )
            return JSONResponse({"student_id": student_id, "incidents": incidents, "review": review}, headers=NO_STORE)

        return handle(run)

    @router.get("/api/teacher/students/{student_id}/incidents/{incident_id}/snapshot")
    def snapshot(student_id: str, incident_id: str, request: Request) -> Response:
        def run() -> Response:
            teacher(request)
            path = store.snapshot_file(student_id, incident_id)
            return FileResponse(path, media_type="image/jpeg", headers=NO_STORE)

        return handle(run)

    @router.post("/api/teacher/students/{student_id}/decision")
    async def decision(student_id: str, request: Request) -> Response:
        try:
            operator = await run_in_threadpool(teacher, request)
        except AuthError as exc:
            return error(exc.status, exc.code, exc.message_ru)
        if not ID_RE.fullmatch(student_id):
            return error(422, "invalid_id", "Некорректный student_id")
        try:
            body = await request.json()
        except Exception:
            return error(400, "invalid_json", "Тело запроса — не JSON")
        if not isinstance(body, dict) or set(body) - {"incident_id", "decision", "note_ru"}:
            return error(422, "invalid_body", "Поля: incident_id, decision, note_ru")
        incident_id, value, note = body.get("incident_id"), body.get("decision"), body.get("note_ru", "")
        if not isinstance(incident_id, str) or not isinstance(value, str) or not isinstance(note, str):
            return error(422, "invalid_body", "Поля: incident_id, decision, note_ru (строки)")
        try:
            view = await run_in_threadpool(store.record_decision, student_id, incident_id, value, note, operator)
        except ReviewError as exc:
            return error(exc.status, exc.code, exc.message_ru)
        return JSONResponse(view, headers=NO_STORE)

    # ------------------------------------------------------------------ teacher: clip playback with seeking
    @router.api_route("/api/teacher/clips/{incident_id}", methods=["GET", "HEAD"])
    def get_clip(incident_id: str, request: Request, student_id: str | None = None) -> Response:
        def run() -> Response:
            teacher(request)
            if student_id is not None and not ID_RE.fullmatch(student_id):
                raise ReviewError(422, "invalid_id", "Некорректный student_id")
            meta, path = store.clip_file(incident_id, student_id)
            size = meta["size_bytes"]
            ext = "mp4" if meta["container"] == "mp4" else "avi"
            name = SAFE_NAME.sub("_", f"clip-{meta['student_id']}-{incident_id}")[:120]
            # MP4 H.264/VP9/AV1 plays in <video>; anything else (A02 MJPG .avi today) is a download for a video player
            playable = meta["container"] == "mp4" and meta.get("codec") in BROWSER_CODECS
            headers = {
                **NO_STORE,
                "Accept-Ranges": "bytes",
                "ETag": f'"{meta["sha256"]}"',
                "Content-Disposition": f'{"inline" if playable else "attachment"}; filename="{name}.{ext}"',
                "X-Qorgau-Clip-Source": meta["source"],
                "Content-Security-Policy": "default-src 'none'",
            }
            try:
                rng = parse_range(request.headers.get("range"), size)
            except RangeNotSatisfiable:
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            if_range = request.headers.get("if-range")
            if rng is not None and if_range is not None and if_range.strip() != headers["ETag"]:
                rng = None  # the client's cached copy is a different file: send all of it
            if rng is None:
                start, end, status = 0, size - 1, 200
            else:
                (start, end), status = rng, 206
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1 if size else 0)
            if request.method == "HEAD":
                return Response(status_code=status, headers=headers, media_type=meta["media_type"])
            return StreamingResponse(iter_file(path, start, end), status_code=status, headers=headers, media_type=meta["media_type"])

        return handle(run)

    return router
