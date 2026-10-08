"""identity_mismatch episodes for A05's FusionEngine (owner: A13; wired into fusion/engine.py like A14 audio).

Input: IdentityObservation (~1 per second, proctor.identity analyzer).
* An episode is a run of consecutive ``same_person == absent`` observations (enrolled, status ok, with similarity).
  It opens once the run spans >= 3 s of session time (first .. last absent), i.e. ~4 analyses in a row.
* ``unknown`` (no face, several faces, small face, model missing, not enrolled), ``present`` or a gap in the stream
  (> 2.5 s without identity observations) end the run. ``unknown`` is never an episode: face_missing /
  multiple_faces (A04) and monitoring_degraded cover those.
* Incident: rule identity_mismatch, category identity, priority HIGH (priority of human review, not a verdict).
  «Лицо не совпадает с лицом в начале экзамена — мм:сс, N с», mm:ss from the start of the exam (first observation
  the engine received after RUNNING).
Only similarity numbers are used; there are no face features anywhere in fusion.
"""

from __future__ import annotations

import statistics
from typing import Callable

from proctor_contracts.v1 import (
    Explanation,
    ExplanationFact,
    IdentityObservation,
    Incident,
    IncidentCategory,
    IncidentChange,
    IncidentChangeType,
    IncidentEndReason,
    IncidentRule,
    IncidentState,
    ObservationStatus,
    ReviewPriority,
    SignalState,
)

from .config import IdentityConfig

IDENTITY_RULE_VERSION = "a13-identity-rule-1.0.0"
MIN_ABSENT_MS = 3000.0
MAX_GAP_MS = 2500.0  # identity analyses ~1/s; a longer silence is not evidence that the face still differs
SUMMARY = "Лицо не совпадает с лицом в начале экзамена"
CAVEATS = [
    "Сравнение только с лицом, запомненным в первые секунды экзамена; это не установление личности.",
    "Поворот головы, освещение, маска или очки могут снижать сходство; решение принимает преподаватель.",
]


def _mmss(t_ms: float) -> str:
    total = max(0, int(round(t_ms / 1000.0)))
    return f"{total // 60:02d}:{total % 60:02d}"


class IdentityFusion:
    def __init__(self, session_id: str, source_mode, wall: Callable[[float], object], origin: Callable[[], float | None]):
        self.session_id, self.source_mode, self.wall, self.origin = session_id, source_mode, wall, origin
        self.threshold = IdentityConfig().match_threshold
        self.exam_t0: float | None = None
        self.run_start: float | None = None
        self.run_last: float | None = None
        self.run_ids: list[str] = []
        self.run_sims: list[float] = []
        self.active: Incident | None = None
        self.serial = 0

    def feed(self, obs: IdentityObservation) -> list[IncidentChange]:
        t = float(obs.t_session_ms)
        if self.exam_t0 is None:
            origin = self.origin()
            self.exam_t0 = t if origin is None else min(origin, t)
        absent = (
            obs.same_person == SignalState.ABSENT
            and obs.enrolled
            and obs.status == ObservationStatus.OK
            and obs.similarity is not None
        )
        if not absent:
            return self.close(IncidentEndReason.CONDITION_CLEARED)
        out: list[IncidentChange] = []
        if self.run_last is not None and t - self.run_last > MAX_GAP_MS:
            out += self.close(IncidentEndReason.SOURCE_LOST)
        if self.run_start is None:
            self.run_start = t
        self.run_last = t
        self.run_ids = [*self.run_ids, obs.observation_id][-200:]
        self.run_sims.append(float(obs.similarity))
        if self.run_last - self.run_start < MIN_ABSENT_MS:
            return out
        if self.active is None:
            self.serial += 1
            self.active = Incident(
                incident_id=f"identity-{self.serial}-{obs.observation_id[-32:]}",
                session_id=self.session_id,
                source_mode=self.source_mode,
                rule_id=IncidentRule.IDENTITY_MISMATCH,
                category=IncidentCategory.IDENTITY,
                state=IncidentState.OPEN,
                priority=ReviewPriority.HIGH,
                t_start_ms=self.run_start,
                t_end_ms=self.run_last,
                wall_start=self.wall(self.run_start),
                duration_ms=0,
                explanation=Explanation(summary_ru=SUMMARY),
                rule_version=IDENTITY_RULE_VERSION,
                config_version="a13-identity-1",
                update_seq=0,
            )
            out.append(self._emit(IncidentChangeType.OPENED))
        else:
            out.append(self._emit(IncidentChangeType.UPDATED))
        return out

    def expire(self, w: float) -> list[IncidentChange]:
        if self.run_last is not None and w - self.run_last > MAX_GAP_MS:
            return self.close(IncidentEndReason.SOURCE_LOST)
        return []

    def close(self, reason: IncidentEndReason) -> list[IncidentChange]:
        out = [self._emit(IncidentChangeType.CLOSED, reason)] if self.active is not None else []
        self.active = None
        self.run_start = self.run_last = None
        self.run_ids, self.run_sims = [], []
        return out

    def _emit(self, change: IncidentChangeType, reason: IncidentEndReason | None = None) -> IncidentChange:
        assert self.active is not None and self.run_start is not None and self.run_last is not None
        end = self.run_last
        duration = end - self.run_start
        closed = change == IncidentChangeType.CLOSED
        start_rel = self.run_start - (self.exam_t0 if self.exam_t0 is not None else 0.0)
        sims = self.run_sims
        self.active = self.active.model_copy(update=dict(
            t_end_ms=end,
            duration_ms=duration,
            update_seq=self.active.update_seq + 1,
            state=IncidentState.CLOSED if closed else IncidentState.OPEN,
            wall_end=self.wall(end) if closed else None,
            end_reason=reason,
            observation_ids=list(self.run_ids),
            observation_count=len(sims),
            explanation=Explanation(
                summary_ru=f"{SUMMARY} — {_mmss(start_rel)}, {int(round(duration / 1000.0))} с",
                facts=[
                    ExplanationFact(key="start_from_exam_ms", value=round(start_rel, 1), unit="ms", label_ru="Начало от старта экзамена"),
                    ExplanationFact(key="duration_ms", value=round(duration, 1), unit="ms", label_ru="Длительность"),
                    ExplanationFact(key="similarity_min", value=round(min(sims), 4), label_ru="Сходство с началом, минимум"),
                    ExplanationFact(key="similarity_median", value=round(statistics.median(sims), 4), label_ru="Сходство с началом, медиана"),
                    ExplanationFact(key="threshold", value=self.threshold, label_ru="Порог OpenCV SFace (косинус)"),
                    ExplanationFact(key="observations", value=len(sims), unit="count", label_ru="Сверок подряд"),
                ],
                caveats_ru=list(CAVEATS),
            ),
        ))
        wire = self.active if closed else self.active.model_copy(update={"t_end_ms": None})
        return IncidentChange(change=change, incident=wire)
