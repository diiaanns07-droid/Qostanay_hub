"""BOOTSTRAP-ONLY incident engine (owner: A01). Synthetic sessions only.

Deliberately minimal: proves the observation -> incident -> stream/store path. It handles only
phone_visible (continuous PRESENT >= 1 s opens, ABSENT >= 0.5 s closes) and environment events
(one closed incident per event). The real rules engine is proctor.fusion (A05).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from proctor_contracts.v1 import (
    EnvironmentObservation,
    Explanation,
    ExplanationFact,
    Incident,
    IncidentCategory,
    IncidentChange,
    IncidentChangeType,
    IncidentEndReason,
    IncidentRule,
    IncidentState,
    Observation,
    PhoneObservation,
    PhoneSignalName,
    ReviewPriority,
    SignalState,
    SourceMode,
)

OPEN_AFTER_MS = 1000.0
CLOSE_AFTER_MS = 500.0
CAVEAT = "СИНТЕТИКА bootstrap: сценарные данные, не результат CV."


class BootstrapIncidentEngine:
    rule_version = "bootstrap-0"
    config_version = "bootstrap-0"

    def __init__(self, session_id: str, source_mode: SourceMode):
        if source_mode != SourceMode.SYNTHETIC:
            raise ValueError("BootstrapIncidentEngine is synthetic-only")
        self.session_id = session_id
        self._seq = 0
        self._present_since: float | None = None
        self._absent_since: float | None = None
        self._first_obs: PhoneObservation | None = None
        self._obs_ids: list[str] = []
        self._open: Incident | None = None
        self._finished = False
        self._paused = False

    # --- IncidentEngine ---
    def consume(self, observation: Observation) -> list[IncidentChange]:
        if self._finished or self._paused or observation.session_id != self.session_id:
            return []
        if isinstance(observation, PhoneObservation):
            return self._phone(observation)
        if isinstance(observation, EnvironmentObservation):
            return self._environment(observation)
        return []

    def advance(self, t_session_ms: float) -> list[IncidentChange]:
        return []

    def set_paused(self, paused: bool, t_session_ms: float) -> list[IncidentChange]:
        self._paused = paused
        if paused:
            return self._close(t_session_ms, IncidentEndReason.SESSION_PAUSED)
        return []

    def finish(self, t_session_ms: float, reason: IncidentEndReason) -> list[IncidentChange]:
        changes = self._close(t_session_ms, reason)
        self._finished = True
        return changes

    def config_snapshot(self) -> dict[str, Any]:
        return {"engine": "bootstrap", "open_after_ms": OPEN_AFTER_MS, "close_after_ms": CLOSE_AFTER_MS}

    # --- internals ---
    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self.session_id}-{self._seq}"

    def _phone(self, obs: PhoneObservation) -> list[IncidentChange]:
        signal = next((s for s in obs.signals if s.name == PhoneSignalName.PHONE_VISIBLE), None)
        if signal is None or signal.state in (SignalState.UNKNOWN, SignalState.INSUFFICIENT_EVIDENCE):
            return []  # unknown is neither "phone" nor "no phone"
        t = obs.t_session_ms
        if signal.state == SignalState.PRESENT:
            self._absent_since = None
            if self._present_since is None:
                self._present_since, self._first_obs, self._obs_ids = t, obs, []
            self._obs_ids = (self._obs_ids + [obs.observation_id])[-50:]
            if self._open is None and t - self._present_since >= OPEN_AFTER_MS:
                first = self._first_obs or obs
                self._open = Incident(
                    incident_id=self._next_id("inc"),
                    session_id=self.session_id,
                    rule_id=IncidentRule.PHONE_VISIBLE,
                    category=IncidentCategory.PHONE,
                    state=IncidentState.OPEN,
                    priority=ReviewPriority.LOW,
                    t_start_ms=self._present_since,
                    wall_start=first.wall_time,
                    duration_ms=t - self._present_since,
                    source_mode=obs.source_mode,
                    explanation=self._explain(t - self._present_since),
                    observation_ids=list(self._obs_ids),
                    observation_count=len(self._obs_ids),
                    trigger_frame_id=obs.frame_id,
                    rule_version=self.rule_version,
                    config_version=self.config_version,
                    update_seq=0,
                )
                return [IncidentChange(change=IncidentChangeType.OPENED, incident=self._open)]
            return []
        # ABSENT
        if self._present_since is None:
            return []
        if self._absent_since is None:
            self._absent_since = t
        if t - self._absent_since >= CLOSE_AFTER_MS:
            end = self._absent_since
            self._present_since = None
            self._absent_since = None
            return self._close(end, IncidentEndReason.CONDITION_CLEARED)
        return []

    def _close(self, t_end: float, reason: IncidentEndReason) -> list[IncidentChange]:
        self._present_since = None
        self._absent_since = None
        if self._open is None:
            return []
        inc = self._open
        self._open = None
        duration = max(0.0, t_end - inc.t_start_ms)
        closed = inc.model_copy(
            update={
                "state": IncidentState.CLOSED,
                "t_end_ms": max(t_end, inc.t_start_ms),
                "wall_end": inc.wall_start + timedelta(milliseconds=duration),
                "duration_ms": duration,
                "explanation": self._explain(duration),
                "end_reason": reason,
                "update_seq": inc.update_seq + 1,
            }
        )
        return [IncidentChange(change=IncidentChangeType.CLOSED, incident=closed)]

    @staticmethod
    def _explain(duration_ms: float) -> Explanation:
        seconds = f"{duration_ms / 1000.0:.1f}".replace(".", ",")
        return Explanation(
            summary_ru=f"СИНТЕТИКА: сценарный «телефон» виден {seconds} с; требуется проверка.",
            facts=[ExplanationFact(key="phone_visible_ms", value=round(duration_ms, 1), unit="ms", label_ru="Телефон виден")],
            caveats_ru=[CAVEAT],
        )

    def _environment(self, obs: EnvironmentObservation) -> list[IncidentChange]:
        rule = (
            IncidentRule.ENVIRONMENT_ESCAPE
            if obs.action.value in {"focus_lost", "foreign_window_foreground"}
            else IncidentRule.ENVIRONMENT_BLOCKED_ACTION
        )
        if obs.action.value in {"focus_regained", "exam_mode_engaged", "exam_mode_released"}:
            return []
        inc = Incident(
            incident_id=self._next_id("inc"),
            session_id=self.session_id,
            rule_id=rule,
            category=IncidentCategory.ENVIRONMENT,
            state=IncidentState.CLOSED,
            priority=ReviewPriority.LOW,
            t_start_ms=obs.t_session_ms,
            t_end_ms=obs.t_session_ms,
            wall_start=obs.wall_time,
            wall_end=obs.wall_time,
            duration_ms=0.0,
            source_mode=obs.source_mode,
            explanation=Explanation(
                summary_ru=f"Действие среды: {obs.action.value}, результат: {obs.enforcement.value}.",
                facts=[ExplanationFact(key="action", value=obs.action.value, label_ru="Действие")],
                caveats_ru=[CAVEAT],
            ),
            observation_ids=[obs.observation_id],
            observation_count=1,
            rule_version=self.rule_version,
            config_version=self.config_version,
            end_reason=IncidentEndReason.CONDITION_CLEARED,
            update_seq=0,
        )
        return [IncidentChange(change=IncidentChangeType.CLOSED, incident=inc)]
