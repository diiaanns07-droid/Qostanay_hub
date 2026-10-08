"""Production C1 adapter for the T03 review store (no dev server or synthetic data).

C1's accepted, persistent event log is the source of truth. The WebSocket hook is only
a wakeup and a source of optional snapshot bytes, never a second sequence validator.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, Response

from classroom.contracts.models import Command
from classroom.server.features import FeatureContext

from .config import MAX_CLIP_BYTES, ReviewConfig
from .router import NO_STORE, create_review_router
from .store import ReviewError, ReviewStore

ASSETS = {"register.js": "text/javascript", "review-module.js": "text/javascript", "review.css": "text/css"}


class ClassroomReviewFeature:
    name = "history"
    owner = "T03"

    def __init__(self, ctx: FeatureContext):
        self.ctx = ctx
        self._sync_lock = threading.RLock()
        self._cursor = 0
        self.store = ReviewStore(ReviewConfig.from_env(
            data_dir=ctx.feature_dir("history"),
            max_clip_bytes=min(MAX_CLIP_BYTES, ctx.config.max_clip_bytes),
        ))
        self.store.open()
        try:
            # Backfill old classes, including events accepted before this feature was installed.
            for row in ctx.db.query("SELECT session_id,created_at FROM sessions"):
                self.store.open_class_session(row["session_id"], datetime.fromisoformat(row["created_at"]))
            self._sync_events()
            for row in ctx.db.query("SELECT json FROM commands ORDER BY rowid"):
                self.on_command_update(Command.model_validate_json(row["json"]))
            self.store.expire_requests()
        except BaseException:
            self.store.close()
            raise
        self.store.on_change(lambda event: ctx.publish("history.changed", event))
        self.router = create_review_router(self.store, teacher_guard=self._teacher,
                                           student_resolver=self._student, status_provider=self._status)

        @self.router.get("/api/teacher/history/assets/{name}")
        def asset(name: str, request: Request) -> Response:
            self._teacher(request)
            if name not in ASSETS:
                return Response(status_code=404, headers=NO_STORE)
            return FileResponse(Path(__file__).with_name("ui") / name,
                                media_type=ASSETS[name], headers=NO_STORE)

    def _teacher(self, request: Request) -> str:
        return self.ctx.teacher(request).teacher_id

    def _student(self, token: str) -> str | None:
        student = self.ctx.student_by_token(token)
        return student.student_id if student is not None else None

    def _status(self, student_id: str) -> dict[str, Any] | None:
        student = self.ctx.student(student_id)
        rows = self.ctx.db.query("SELECT json FROM device_status WHERE student_id=?", (student_id,))
        if not student or not rows:
            return None
        status = json.loads(rows[0]["json"])
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(status["received_at"])).total_seconds()
        if student.connection != "online" or age > self.ctx.config.status_stale_s or status.get("camera") != "ok":
            return {"zone": "grey", "monitoring": "degraded"}
        return status

    def _sync_events(self, live: dict[str, Any] | None = None) -> None:
        with self._sync_lock:
            while True:
                rows = self.ctx.db.query("SELECT rowid AS cursor,json FROM events WHERE rowid>? ORDER BY rowid LIMIT 500",
                                         (self._cursor,))
                if not rows:
                    break
                for row in rows:
                    event = json.loads(row["json"])
                    if event.get("kind") == "incident":
                        payload = dict(event["payload"])
                        # C1 deliberately excludes JPEG bytes from its event log. Only attach bytes
                        # to the exact accepted envelope; a conflicting/redelivered message cannot
                        # replace earlier evidence. Old missing bytes are never fabricated.
                        if live and payload.get("msg_id") == live.get("msg_id") and live.get("snapshot_jpeg_b64"):
                            payload["snapshot_jpeg_b64"] = live["snapshot_jpeg_b64"]
                        self.store.ingest_incident(event["student_id"], payload,
                                                   class_session_id=event["session_id"], canonical_event=event)
                    self._cursor = row["cursor"]

    def on_student_message(self, student_id: str, message: dict[str, Any]) -> None:
        if message.get("type") == "incident":
            self._sync_events(message)

    def on_command_update(self, command: Command) -> None:
        if command.kind != "request_clip":
            return
        self._sync_events()
        incident_id = command.payload["incident_id"]
        try:
            # C1 publishes QUEUED before placing a command in the student's send queue.
            # SENT/ACK/restart replay are idempotent and cannot reset the request timeout.
            self.store.note_clip_requested(command.student_id, incident_id, command.command_id,
                                           requested_at=command.issued_at)
        except ReviewError as exc:
            if exc.code in ("clip_already_available", "incident_not_found"):
                return
            raise
        if command.ack is not None and not command.ack.ok:
            self.store.note_command_ack(command.command_id, False, command.ack.error_ru)
        elif command.status in ("expired", "cancelled"):
            reason = "Срок команды истёк. Можно запросить снова." if command.status == "expired" else "Запрос клипа отменён."
            self.store.note_command_ack(command.command_id, False, reason, reason_code=f"command_{command.status}")

    def incidents_unreviewed(self, student_id: str) -> int:
        # Count current C1 episodes too: its first card notification precedes the feature hook.
        canonical = {r["incident_id"] for r in self.ctx.db.query("SELECT incident_id FROM incidents WHERE student_id=?", (student_id,))}
        reviewed = {i["incident_id"] for i in self.store.list_incidents(student_id) if i["decision"] is not None}
        return len(canonical - reviewed)

    def tick(self) -> None:
        self._sync_events()
        self.store.expire_requests()

    def close(self) -> None:
        self.store.close()


def create_classroom_feature(ctx: FeatureContext) -> ClassroomReviewFeature:
    return ClassroomReviewFeature(ctx)


create = create_classroom_feature  # the spec name in classroom/coordination/INTERFACES.md §3
