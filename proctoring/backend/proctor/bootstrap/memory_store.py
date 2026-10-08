"""BOOTSTRAP-ONLY in-memory EvidenceStore (owner: A01). Synthetic sessions only.

Implements a subset of the A08 routes so the API shape can be exercised before proctor.evidence
lands. Nothing is persisted; report/export/evidence routes answer 501 NOT_IMPLEMENTED.
"""

from __future__ import annotations

import threading

from fastapi import APIRouter

from proctor_contracts.interfaces import (
    BackendContext,
    FramePacket,
    InvalidStateError,
    NotFoundError,
    ProctorError,
)
from proctor_contracts.v1 import (
    AnswerRecord,
    AnswerUpsert,
    Component,
    ErrorCode,
    EvidenceItem,
    Health,
    HealthStatus,
    HumanReview,
    HumanReviewCreate,
    Incident,
    IncidentChange,
    IncidentDetail,
    Observation,
    ReviewStatus,
    SessionInfo,
    SessionState,
    SessionSummary,
    utc_now,
)


class NotImplementedProctorError(ProctorError):
    http_status = 501


class MemoryEvidenceStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionInfo] = {}
        self._incidents: dict[str, dict[str, Incident]] = {}
        self._reviews: dict[str, list[HumanReview]] = {}
        self._answers: dict[str, dict[str, AnswerRecord]] = {}
        self._obs_count: dict[str, int] = {}

    def open(self) -> Health:
        return self.health()

    def close(self) -> None:
        pass

    def upsert_session(self, info: SessionInfo) -> None:
        with self._lock:
            self._sessions[info.session_id] = info

    def record_observation(self, observation: Observation) -> None:
        with self._lock:
            self._obs_count[observation.session_id] = self._obs_count.get(observation.session_id, 0) + 1

    def record_incident_change(self, change: IncidentChange) -> None:
        inc = change.incident
        with self._lock:
            bucket = self._incidents.setdefault(inc.session_id, {})
            current = bucket.get(inc.incident_id)
            if current is not None and current.update_seq >= inc.update_seq:
                return  # idempotent re-delivery
            review_status = current.review_status if current is not None else ReviewStatus.PENDING
            bucket[inc.incident_id] = inc.model_copy(update={"review_status": review_status})

    def capture_snapshot(self, session_id: str, incident_id: str, frame: FramePacket) -> EvidenceItem | None:
        return None  # media retention is A08

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            for table in (self._sessions, self._incidents, self._answers, self._obs_count):
                table.pop(session_id, None)
            for key in [k for k in self._reviews if k.startswith(f"{session_id}/")]:
                self._reviews.pop(key, None)

    def health(self) -> Health:
        return Health(
            component=Component.EVIDENCE,
            status=HealthStatus.DEGRADED,
            code="bootstrap_memory_store",
            message="In-memory bootstrap store (synthetic only, nothing persisted)",
        )

    # --- A08 routes subset ---
    def create_router(self, context: BackendContext) -> APIRouter:
        router = APIRouter()
        store = self

        def known(session_id: str) -> SessionInfo:
            info = context.get_session(session_id) or store._sessions.get(session_id)
            if info is None:
                raise NotFoundError(ErrorCode.SESSION_NOT_FOUND, f"session {session_id} not found")
            return info

        @router.get("/sessions", response_model=list[SessionInfo])
        def list_sessions() -> list[SessionInfo]:
            with store._lock:
                return sorted(store._sessions.values(), key=lambda s: s.created_at, reverse=True)

        @router.get("/sessions/{session_id}/incidents", response_model=list[Incident])
        def list_incidents(session_id: str) -> list[Incident]:
            known(session_id)
            with store._lock:
                return sorted(store._incidents.get(session_id, {}).values(), key=lambda i: i.t_start_ms)

        @router.get("/sessions/{session_id}/incidents/{incident_id}", response_model=IncidentDetail)
        def get_incident(session_id: str, incident_id: str) -> IncidentDetail:
            known(session_id)
            with store._lock:
                inc = store._incidents.get(session_id, {}).get(incident_id)
                if inc is None:
                    raise NotFoundError(ErrorCode.NOT_FOUND, f"incident {incident_id} not found")
                return IncidentDetail(incident=inc, reviews=list(store._reviews.get(f"{session_id}/{incident_id}", [])))

        @router.post("/sessions/{session_id}/incidents/{incident_id}/reviews", response_model=HumanReview)
        def add_review(session_id: str, incident_id: str, body: HumanReviewCreate) -> HumanReview:
            known(session_id)
            with store._lock:
                inc = store._incidents.get(session_id, {}).get(incident_id)
                if inc is None:
                    raise NotFoundError(ErrorCode.NOT_FOUND, f"incident {incident_id} not found")
                history = store._reviews.setdefault(f"{session_id}/{incident_id}", [])
                review = HumanReview(
                    review_id=f"rev-{incident_id}-{len(history) + 1}",
                    session_id=session_id,
                    incident_id=incident_id,
                    decision=body.decision,
                    comment=body.comment,
                    operator=body.operator,
                    created_at=utc_now(),
                    supersedes_review_id=history[-1].review_id if history else None,
                )
                history.append(review)
                store._incidents[session_id][incident_id] = inc.model_copy(
                    update={"review_status": ReviewStatus(body.decision.value)}
                )
                return review

        @router.put("/sessions/{session_id}/answers/{question_id}", response_model=AnswerRecord)
        def save_answer(session_id: str, question_id: str, body: AnswerUpsert) -> AnswerRecord:
            info = known(session_id)
            if info.state != SessionState.RUNNING:
                raise InvalidStateError(ErrorCode.INVALID_STATE, "answers are accepted only while running")
            with store._lock:
                answers = store._answers.setdefault(session_id, {})
                current = answers.get(question_id)
                if current is not None and current.client_seq >= body.client_seq:
                    return current
                record = AnswerRecord(
                    session_id=session_id,
                    question_id=question_id,
                    value=body.value,
                    client_seq=body.client_seq,
                    saved_at=utc_now(),
                )
                answers[question_id] = record
                return record

        @router.get("/sessions/{session_id}/answers", response_model=list[AnswerRecord])
        def list_answers(session_id: str) -> list[AnswerRecord]:
            known(session_id)
            with store._lock:
                return list(store._answers.get(session_id, {}).values())

        @router.get("/sessions/{session_id}/summary", response_model=SessionSummary)
        def summary(session_id: str) -> SessionSummary:
            info = known(session_id)
            with store._lock:
                incidents = list(store._incidents.get(session_id, {}).values())
            by_rule: dict[str, int] = {}
            for inc in incidents:
                by_rule[inc.rule_id.value] = by_rule.get(inc.rule_id.value, 0) + 1
            return SessionSummary(
                session=info,
                observed_ms=0.0,
                paused_ms=info.paused_total_ms,
                incidents_total=len(incidents),
                incidents_by_rule=by_rule,
                limitations_ru=["BOOTSTRAP: синтетические данные, хранение в памяти, покрытие не рассчитывается"],
            )

        def not_impl(what: str) -> None:
            raise NotImplementedProctorError(ErrorCode.NOT_IMPLEMENTED, f"{what} is provided by proctor.evidence (A08)")

        @router.get("/sessions/{session_id}/evidence/{evidence_id}")
        def evidence(session_id: str, evidence_id: str) -> None:
            not_impl("evidence")

        @router.get("/sessions/{session_id}/report.html")
        def report_html(session_id: str) -> None:
            not_impl("HTML report")

        @router.get("/sessions/{session_id}/report.json")
        def report_json(session_id: str) -> None:
            not_impl("JSON export")

        @router.delete("/sessions/{session_id}")
        def delete(session_id: str) -> dict[str, bool]:
            if context.active_session_id() == session_id:
                raise InvalidStateError(ErrorCode.SESSION_ACTIVE, "finish or abort the session before deleting it")
            known(session_id)
            store.delete_session(session_id)
            return {"deleted": True}

        return router
