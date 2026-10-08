"""Mutable per-episode accumulator (internal to the fusion engine)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from proctor_contracts.v1 import IncidentEndReason, IncidentRule, ReviewPriority


@dataclass(eq=False)
class Episode:
    """One episode of one rule. ``incident_id is None`` while it is still pending (not yet an incident).

    Times are session milliseconds. ``t_last`` is the last time the condition was observed
    (continuous rules) or the last activity (environment / monitoring)."""

    rule: IncidentRule
    t_start: float
    t_last: float
    incident_id: str | None = None
    count: int = 0  # contributing observations
    refs_head: list[str] = field(default_factory=list)
    refs_tail: deque[str] = field(default_factory=deque)
    conf_max: float | None = None
    q_sum: float = 0.0
    q_n: int = 0
    appearances: int = 1
    max_gap_ms: float = 0.0  # largest gap between consecutive true observations of one appearance
    unknown_n: int = 0  # undetermined observations inside the episode interval
    unknown_pending: int = 0  # undetermined observations after the last true one (not yet inside)
    trigger_frame_id: int | None = None
    counters: dict[str, Any] = field(default_factory=dict)
    related: set[str] = field(default_factory=set)
    # emission bookkeeping
    update_seq: int = -1
    last_emit_t: float = 0.0
    last_priority: ReviewPriority | None = None
    closed: bool = False
    t_end: float | None = None
    end_reason: IncidentEndReason | None = None
    frozen_ctx: dict[str, float] | None = None  # correlation facts fixed at close
    # continuous-rule internals
    prev_true_t: float | None = None
    prev_dir: str | None = None
    clearing_since: float | None = None

    @property
    def is_open(self) -> bool:
        return self.incident_id is not None and not self.closed

    def interval(self) -> tuple[float, float]:
        return self.t_start, self.t_end if self.t_end is not None else self.t_last

    def add_ref(self, observation_id: str, head: int, tail: int) -> None:
        self.count += 1
        if len(self.refs_head) < head:
            self.refs_head.append(observation_id)
            return
        if self.refs_tail.maxlen != tail:
            self.refs_tail = deque(self.refs_tail, maxlen=tail)
        self.refs_tail.append(observation_id)

    def observation_ids(self) -> list[str]:
        return [*self.refs_head, *self.refs_tail]

    def add_quality(self, quality: float | None) -> None:
        if quality is not None:
            self.q_sum += quality
            self.q_n += 1

    def add_confidence(self, confidence: float | None) -> None:
        if confidence is not None:
            self.conf_max = confidence if self.conf_max is None else max(self.conf_max, confidence)

    @property
    def mean_quality(self) -> float | None:
        return round(self.q_sum / self.q_n, 4) if self.q_n else None

    def bump(self, key: str, by: float = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + by

    def raise_max(self, key: str, value: float) -> None:
        if value > self.counters.get(key, float("-inf")):
            self.counters[key] = value
