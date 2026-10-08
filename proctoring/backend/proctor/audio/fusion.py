"""Small contract adapter around A05's unchanged pure background_speech rule."""
from collections import deque

from proctor_contracts.v1 import (Explanation, ExplanationFact, Incident, IncidentCategory,
    IncidentChange, IncidentChangeType, IncidentEndReason, IncidentRule, IncidentState,
    ObservationStatus, ReviewPriority, SignalState)
from proctor.fusion.audio_rules import AUX_RULE_VERSION, AuxConfig, background_speech


class AudioFusion:
    def __init__(self, session_id, source_mode, wall):
        self.session_id, self.source_mode, self.wall = session_id, source_mode, wall
        self.cfg = AuxConfig()
        self.samples = deque()
        self.recent_starts = deque()
        self.active = None
        self.refs = deque(maxlen=200)
        self.recent_refs = deque()
        self.count = self.serial = 0
        self.last_seen = None
        self.closed_through = -1.0

    def feed(self, obs):
        t = float(obs.t_session_ms)
        self.last_seen = t
        valid = obs.status == ObservationStatus.OK or (
            obs.status == ObservationStatus.DEGRADED and obs.reasons == ["energy_fallback"])
        if not valid or obs.voice_like not in (SignalState.PRESENT, SignalState.ABSENT):
            return self.close(IncidentEndReason.SOURCE_LOST)
        if self.samples and self.samples[-1][0] == t:
            self.samples.pop()
        self.samples.append((t, obs.voice_like == SignalState.PRESENT))
        while self.samples and self.samples[0][0] < t - 2 * self.cfg.speech_window_ms:
            self.samples.popleft()
        self.recent_refs.append((t, obs.observation_id))
        while self.recent_refs and self.recent_refs[0][0] < t - 2 * self.cfg.speech_window_ms:
            self.recent_refs.popleft()
        if self.active is not None:
            self.refs.append(obs.observation_id)
            self.count += 1
        # Sentinel caps the last held sample at observed time, avoiding future evidence.
        episodes = background_speech([*list(self.samples)[:-1], (t, None)], self.cfg)
        candidate = episodes[-1] if episodes else None
        if candidate is not None and candidate.t_end_ms <= self.closed_through:
            candidate = None
        out = []
        if candidate is not None and (self.active is None or candidate.t_end_ms > self.active.t_start_ms):
            if self.active is not None and candidate.t_start_ms > self.active.t_end_ms:
                out += self._close_active(IncidentEndReason.CONDITION_CLEARED)
            if self.active is None:
                # Don't reopen an already emitted candidate retained in the rolling buffer.
                if self.recent_starts and candidate.t_start_ms <= self.recent_starts[-1]:
                    return out
                self.serial += 1
                self.refs = deque((ref for at, ref in self.recent_refs if at >= candidate.t_start_ms), maxlen=200)
                self.count = len(self.refs)
                self.recent_starts.append(candidate.t_start_ms)
                while self.recent_starts[0] < candidate.t_start_ms - self.cfg.speech_repeat_window_ms:
                    self.recent_starts.popleft()
                self.active = Incident(
                    incident_id=f"audio-{self.serial}-{obs.observation_id[-32:]}",
                    session_id=self.session_id, source_mode=self.source_mode,
                    rule_id=IncidentRule.BACKGROUND_SPEECH, category=IncidentCategory.AUDIO,
                    state=IncidentState.OPEN,
                    priority=ReviewPriority.MEDIUM if len(self.recent_starts) >= 3 else ReviewPriority.LOW,
                    t_start_ms=candidate.t_start_ms, t_end_ms=candidate.t_end_ms,
                    wall_start=self.wall(candidate.t_start_ms), duration_ms=0,
                    explanation=Explanation(summary_ru="Возможная речь или разговор рядом"),
                    rule_version=AUX_RULE_VERSION, config_version="a14-audio-1", update_seq=0)
                out.append(self._emit(IncidentChangeType.OPENED, candidate.t_end_ms))
            elif candidate.t_end_ms > self.active.t_end_ms:
                out.append(self._emit(IncidentChangeType.UPDATED, candidate.t_end_ms))
        if self.active is not None and t - self.active.t_end_ms > self.cfg.speech_window_ms:
            out += self._close_active(IncidentEndReason.CONDITION_CLEARED)
        return out

    def expire(self, t):
        if self.last_seen is not None and t - self.last_seen > self.cfg.sample_hold_ms:
            self.last_seen = None
            return self.close(IncidentEndReason.SOURCE_LOST)
        return []

    def close(self, reason):
        out = self._close_active(reason)
        self.samples.clear()
        self.refs.clear()
        self.recent_refs.clear()
        self.count = 0
        self.last_seen = None
        return out

    def _close_active(self, reason):
        if self.active is None:
            return []
        result = self._emit(IncidentChangeType.CLOSED, self.active.t_end_ms, reason)
        self.closed_through = self.active.t_end_ms
        self.active = None
        return [result]

    def _emit(self, change, end, reason=None):
        duration = end - self.active.t_start_ms
        closed = change == IncidentChangeType.CLOSED
        self.active = self.active.model_copy(update=dict(
            t_end_ms=end, duration_ms=duration, update_seq=self.active.update_seq + 1,
            state=IncidentState.CLOSED if closed else IncidentState.OPEN,
            wall_end=self.wall(end) if closed else None, end_reason=reason,
            observation_ids=list(self.refs), observation_count=self.count,
            explanation=Explanation(summary_ru="Возможная речь или разговор рядом",
                facts=[ExplanationFact(key="duration_ms", value=duration, unit="ms", label_ru="Длительность"),
                       ExplanationFact(key="episodes_in_window", value=len(self.recent_starts), unit="count",
                                       label_ru="Эпизодов за 5 минут")],
                caveats_ru=["Детектор не определяет говорящего или содержание речи; шёпот может не обнаруживаться."])))
        # Internal end tracks observed support; wire contract leaves open end unset.
        wire = self.active if closed else self.active.model_copy(update={"t_end_ms": None})
        return IncidentChange(change=change, incident=wire)
