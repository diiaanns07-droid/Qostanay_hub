"""A08 HTTP routes (CONTRACTS.md §3), mounted by A01 under /v1 behind the bearer/Host/Origin/body checks.

The router adds no authentication of its own and no bypass. Handlers are plain `def`, so FastAPI
runs them in its worker threads (SQLite and file I/O never block the event loop). Path ids are
validated against the contract Id pattern; errors are ProctorError -> ApiError (A01 handler).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import Response

from proctor_contracts.interfaces import BackendContext, InvalidStateError
from proctor_contracts.v1 import (
    AnswerRecord,
    AnswerUpsert,
    ErrorCode,
    HumanReview,
    HumanReviewCreate,
    Incident,
    IncidentDetail,
    SessionInfo,
    SessionState,
)

from . import report
from .store import SqliteEvidenceStore
from .answers import load_exam, validate_answer
from .review_zones import SessionOverviewRow, SessionSummary

ID_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"
SessionId = Annotated[str, Path(pattern=ID_PATTERN)]
ItemId = Annotated[str, Path(pattern=ID_PATTERN)]

NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def build_router(store: SqliteEvidenceStore, context: BackendContext) -> APIRouter:
    router = APIRouter()
    exam = load_exam(store.settings)

    def live_info(session_id: str) -> SessionInfo | None:
        """Most recent SessionInfo from the lifecycle owner (A01), if it still knows the session."""
        try:
            return context.get_session(session_id)
        except Exception:
            return None

    @router.get("/sessions", response_model=list[SessionInfo])
    def list_sessions() -> list[SessionInfo]:
        return store.list_sessions()

    @router.get("/sessions/overview", response_model=list[SessionOverviewRow])
    def overview() -> list[SessionOverviewRow]:
        return store.overview()

    @router.get("/sessions/{session_id}/incidents", response_model=list[Incident])
    def list_incidents(session_id: SessionId) -> list[Incident]:
        return store.list_incidents(session_id)

    @router.get("/sessions/{session_id}/incidents/{incident_id}", response_model=IncidentDetail)
    def get_incident(session_id: SessionId, incident_id: ItemId) -> IncidentDetail:
        return store.get_incident_detail(session_id, incident_id)

    @router.post("/sessions/{session_id}/incidents/{incident_id}/reviews", response_model=HumanReview)
    def add_review(session_id: SessionId, incident_id: ItemId, body: HumanReviewCreate) -> HumanReview:
        return store.add_review(session_id, incident_id, body)

    @router.get("/sessions/{session_id}/evidence/{evidence_id}")
    def get_evidence(session_id: SessionId, evidence_id: ItemId) -> Response:
        item, data = store.evidence_media(session_id, evidence_id)
        return Response(
            content=data,
            media_type=item.media_type,
            headers={
                **NO_STORE,
                "Content-Disposition": f'inline; filename="{item.evidence_id}.jpg"',
                "X-Qorgau-Evidence-Sha256": item.sha256,
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    @router.put("/sessions/{session_id}/answers/{question_id}", response_model=AnswerRecord)
    def save_answer(session_id: SessionId, question_id: ItemId, body: AnswerUpsert) -> AnswerRecord:
        info = live_info(session_id)
        if info is not None and info.state != SessionState.RUNNING:
            raise InvalidStateError(
                ErrorCode.INVALID_STATE,
                f"answers are accepted only while running (session is {info.state.value})",
                state=info.state.value,
            )
        # Require persisted metadata too: an A01 runtime must not resurrect a deleted row.
        stored = store.require_session(session_id)
        validate_answer(exam, stored.exam_id, question_id, body)
        return store.save_answer(session_id, question_id, body)

    @router.get("/sessions/{session_id}/answers", response_model=list[AnswerRecord])
    def list_answers(session_id: SessionId) -> list[AnswerRecord]:
        return store.list_answers(session_id)

    @router.get("/sessions/{session_id}/summary", response_model=SessionSummary)
    def summary(session_id: SessionId) -> SessionSummary:
        return store.summary(session_id, live_info(session_id))

    @router.get("/sessions/{session_id}/report.html")
    def report_html(session_id: SessionId) -> Response:
        snap = store.export_snapshot(session_id, live_info(session_id))
        body = report.render_html(snap).encode("utf-8")
        return Response(
            content=body,
            media_type="text/html; charset=utf-8",
            headers={
                **NO_STORE,
                "Content-Security-Policy": report.CSP,
                "Content-Disposition": f'inline; filename="{report.safe_file_stem(session_id)}.html"',
                "Referrer-Policy": "no-referrer",
            },
        )

    @router.get("/sessions/{session_id}/report.json")
    def report_json(session_id: SessionId) -> Response:
        snap = store.export_snapshot(session_id, live_info(session_id))
        payload = report.build_json(snap)
        return Response(
            content=report.dumps_json(payload),
            media_type="application/json",
            headers={**NO_STORE, "Content-Disposition": f'attachment; filename="{report.safe_file_stem(session_id)}.json"'},
        )

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: SessionId) -> dict[str, bool]:
        if context.active_session_id() == session_id:
            raise InvalidStateError(ErrorCode.SESSION_ACTIVE, "finish or abort the session before deleting it")
        store.delete_session(session_id)
        forget = getattr(context, "forget_session", None)
        if callable(forget):
            forget(session_id)  # available on A01 candidate de72905; optional on BOOTSTRAP
        return {"deleted": True}

    return router
