"""A05 incident engine: noisy observations -> a small number of explainable incidents.

Pure and deterministic: no camera, UI, DB, clock or file access. Called only from the session's fusion
thread (A01). Same observation sequence + config => same incidents, whatever the replay speed or the
``advance()`` cadence: every incident time is taken from observation data, never from when a tick ran.

Policies (normative for this module; summary in handoffs/A05/STATUS.md):

* Time. Ordering and durations use ``t_session_ms`` only. The watermark ``W`` is the largest session
  time seen (accepted observations and ``advance(t)``). ``wall_*`` of incidents = anchor + t, where the
  anchor is ``wall_time - t_session_ms`` of the first accepted observation: a wall-clock jump can
  neither reorder episodes nor make ``wall_end < wall_start``.
* Late / out-of-order. Each stream (phone, attention, environment, health.<component>) must be
  non-decreasing in ``t_session_ms``; an older observation than the last accepted one of its stream is
  dropped and counted (``stats()['late']``): state machines never rewind. Equal timestamps are fine.
  Cross-stream disorder is harmless: each rule is fed by one stream and correlation uses intervals.
* Stale. A phone/attention observation older than ``W - TTL(source)`` is not used as evidence.
  A source silent (or only undetermined) for longer than its TTL stops taking part in rules and
  correlation: its open episodes close with ``source_lost`` (never ``condition_cleared``), and a
  ``monitoring_degraded`` incident records the coverage gap.
* Unknown. ``unknown`` / ``error`` status, quality below ``min_quality``, ``face_count = None``,
  ``Direction.unknown``, ``SignalState.unknown|insufficient_evidence`` are neither violation nor
  all-clear: they never open, extend or clear an episode.
* Duplicates (same ``observation_id``), other sessions and other ``source_mode`` are ignored and counted.
* Pause closes everything with ``session_paused``; observations during pause are ignored; resume
  restarts freshness. ``finish`` closes everything (the last episode is never lost) and is idempotent.
* Replay (``source_mode=replay``) is purely data-driven: ``advance()`` is a no-op and the recording is
  the clock, so wall-clock ticks cannot create gaps that are not in the recording.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from collections import Counter, deque
from datetime import datetime, timedelta
from typing import Any

from proctor_contracts.v1 import (
    AudioObservation,
    AttentionObservation,
    Direction,
    EnvironmentAction,
    EnvironmentObservation,
    HealthObservation,
    HealthStatus,
    Incident,
    IncidentCategory,
    IncidentChange,
    IncidentChangeType,
    IncidentEndReason,
    IncidentRule,
    IncidentState,
    Observation,
    ObservationStatus,
    PhoneObservation,
    PhoneSignalName,
    SignalState,
    SourceMode,
    utc_now,
)

from . import explain
from .config import RULE_VERSION, FusionConfig
from .episode import Episode

R = IncidentRule
OPENED, UPDATED, CLOSED = IncidentChangeType.OPENED, IncidentChangeType.UPDATED, IncidentChangeType.CLOSED

SOURCES = ("phone", "attention")
INTERVAL_RULES: dict[IncidentRule, str] = {
    R.PHONE_VISIBLE: "phone",
    R.PHONE_RAISED: "phone",
    R.POSSIBLE_SCREEN_CAPTURE: "phone",
    R.GAZE_PROLONGED_DOWN: "attention",
    R.GAZE_PROLONGED_SIDE: "attention",
    R.FACE_MISSING: "attention",
    R.MULTIPLE_FACES: "attention",
    R.FOREIGN_OBJECT_VISIBLE: "phone",  # A03 detections (book)
    R.SECOND_SCREEN_VISIBLE: "phone",  # A03 detections (laptop, tv)
}
CATEGORY: dict[IncidentRule, IncidentCategory] = {
    R.PHONE_VISIBLE: IncidentCategory.PHONE,
    R.PHONE_RAISED: IncidentCategory.PHONE,
    R.POSSIBLE_SCREEN_CAPTURE: IncidentCategory.PHONE,
    R.GAZE_PROLONGED_DOWN: IncidentCategory.ATTENTION,
    R.GAZE_PROLONGED_SIDE: IncidentCategory.ATTENTION,
    R.FACE_MISSING: IncidentCategory.PRESENCE,
    R.MULTIPLE_FACES: IncidentCategory.PRESENCE,
    R.ENVIRONMENT_BLOCKED_ACTION: IncidentCategory.ENVIRONMENT,
    R.ENVIRONMENT_ESCAPE: IncidentCategory.ENVIRONMENT,
    R.MONITORING_DEGRADED: IncidentCategory.TECHNICAL,
    R.FOREIGN_OBJECT_VISIBLE: IncidentCategory.OBJECTS,
    R.SECOND_SCREEN_VISIBLE: IncidentCategory.OBJECTS,
}
PHONE_CLASS = "cell phone"


def _iou(a: Any, b: Any) -> float:
    x1, y1 = max(a.x_min, b.x_min), max(a.y_min, b.y_min)
    x2, y2 = min(a.x_max, b.x_max), min(a.y_max, b.y_max)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a.x_max - a.x_min) * (a.y_max - a.y_min) + (b.x_max - b.x_min) * (b.y_max - b.y_min) - inter
    return inter / union if union > 0 else 0.0
PHONE_FAMILY = frozenset({R.PHONE_VISIBLE, R.PHONE_RAISED, R.POSSIBLE_SCREEN_CAPTURE})
A = EnvironmentAction
BLOCKED_ACTIONS = frozenset(
    {
        A.SHORTCUT_ALT_TAB,
        A.SHORTCUT_CTRL_C,
        A.SHORTCUT_CTRL_V,
        A.SHORTCUT_CTRL_X,
        A.SHORTCUT_CTRL_TAB,
        A.SHORTCUT_WIN,
        A.SHORTCUT_PRINT_SCREEN,
        A.SHORTCUT_ALT_F4,
        A.NEW_WINDOW_BLOCKED,
        A.NAVIGATION_BLOCKED,
        A.DEVTOOLS_BLOCKED,
        A.CLIPBOARD_BLOCKED,
        A.DISPLAY_CHANGED,
    }
)
BAD_HEALTH = frozenset({HealthStatus.DEGRADED, HealthStatus.UNAVAILABLE, HealthStatus.ERROR, HealthStatus.STOPPED})
USABLE = frozenset({ObservationStatus.OK, ObservationStatus.DEGRADED})
ENV_UPDATE_MILESTONES = frozenset({2, 3, 5, 10, 20, 50, 100, 200, 500, 1000})
DEDUP_MAX = 200_000
MAX_ID_SESSION_PART = 64


def _state_value(state: SignalState | None) -> bool | None:
    if state == SignalState.PRESENT:
        return True
    if state == SignalState.ABSENT:
        return False
    return None  # unknown / insufficient_evidence / missing


def _combine(states: list[SignalState]) -> SignalState | None:
    """Several signals of one name (one per track): any present wins, then absent, else unknown."""
    for wanted in (SignalState.PRESENT, SignalState.ABSENT, SignalState.INSUFFICIENT_EVIDENCE, SignalState.UNKNOWN):
        if wanted in states:
            return wanted
    return None


class _Series:
    """Raw samples (t, True/False/None) of one condition; a sample stands until the next one or
    for at most ``hold_ms`` (dropped frames are not counted as time with the condition)."""

    def __init__(self, hold_ms: float):
        self.hold = hold_ms
        self.ts: list[float] = []
        self.vs: list[bool | None] = []
        self.cum: list[float] = []  # cum[i] = true coverage of samples 0..i-1 (final once sample i exists)

    def add(self, t: float, value: bool | None) -> None:
        if self.ts:
            self.cum.append(self.cum[-1] + self._cover(len(self.ts) - 1, t))
        else:
            self.cum.append(0.0)
        self.ts.append(t)
        self.vs.append(value)

    def _cover(self, k: int, next_t: float | None) -> float:
        if self.vs[k] is not True:
            return 0.0
        t = self.ts[k]
        end = t + self.hold if next_t is None else min(t + self.hold, next_t)
        return max(0.0, end - t)

    def _part(self, k: int, a: float, b: float) -> float:
        if self.vs[k] is not True:
            return 0.0
        t = self.ts[k]
        end = t + self.hold
        if k + 1 < len(self.ts):
            end = min(end, self.ts[k + 1])
        return max(0.0, min(b, end) - max(a, t))

    def true_ms(self, a: float, b: float) -> float:
        """Time in [a, b] covered by true samples; O(log n) via prefix sums."""
        if b <= a or not self.ts:
            return 0.0
        i0 = bisect_right(self.ts, a) - 1  # sample covering a (may be -1)
        j = bisect_right(self.ts, b) - 1  # last sample starting at or before b
        if j < 0:
            return 0.0
        if i0 >= 0 and i0 == j:
            return self._part(j, a, b)
        total = self._part(i0, a, b) if i0 >= 0 else 0.0
        lo = i0 + 1  # samples lo..j-1 lie fully inside [a, b]
        if j > lo:
            total += self.cum[j] - self.cum[lo]
        return total + self._part(j, a, b)

    def prune(self, before: float) -> None:
        i = bisect_right(self.ts, before) - 1  # keep the sample covering `before`
        if i > 2048:
            del self.ts[:i]
            del self.vs[:i]
            del self.cum[:i]


class _IntervalRule:
    """Hysteresis state machine of one continuous rule (pending -> open -> closed)."""

    def __init__(self, engine: "FusionEngine", rule: IncidentRule, source: str):
        self.e = engine
        self.rule = rule
        self.source = source
        self.p = engine.cfg.interval(rule.value)
        self.ttl = engine.cfg.source_ttl_ms[source]
        self.ep: Episode | None = None  # pending (incident_id None) or open
        self.last_known_t: float | None = None  # last determinate (true/false) observation

    def feed(self, t: float, value: bool | None, obs: Observation, info: dict[str, Any]) -> list[IncidentChange]:
        ep, p = self.ep, self.p
        if value is None:
            if ep is not None and ep.is_open:
                ep.unknown_pending += 1  # counted only if the condition is seen again (inside the interval)
            return []
        self.last_known_t = t
        if value:
            if ep is not None and ep.is_open:
                merged = ep.clearing_since is not None
                if merged:  # re-appearance inside the merge gap: same episode
                    ep.appearances += 1
                    ep.clearing_since = None
                self._absorb(ep, t, obs, info, new_appearance=merged)
                return self.e._maybe_update(ep, t, force=merged)
            if ep is None or t - ep.t_last > p.pending_gap_ms:
                ep = self.ep = Episode(rule=self.rule, t_start=t, t_last=t)
            self._absorb(ep, t, obs, info)
            if ep.t_last - ep.t_start >= p.min_duration_ms and ep.count >= p.min_count:
                return self.e._open(ep, obs.frame_id, t)
            return []
        if ep is None:
            return []
        if not ep.is_open:
            if t - ep.t_last > p.pending_gap_ms:
                self.ep = None  # a brief glance never becomes a prolonged episode
            return []
        if ep.clearing_since is None:
            ep.clearing_since = t
        if t - ep.t_last >= p.merge_gap_ms:
            self.ep = None
            return self.e._close(ep, ep.t_last, IncidentEndReason.CONDITION_CLEARED)
        return []

    def _absorb(self, ep: Episode, t: float, obs: Observation, info: dict[str, Any], new_appearance: bool = False) -> None:
        cfg = self.e.cfg
        if ep.prev_true_t is not None and not new_appearance:
            gap = t - ep.prev_true_t
            ep.max_gap_ms = max(ep.max_gap_ms, gap)
            if ep.prev_dir is not None:
                ep.bump(f"{ep.prev_dir}_ms", min(gap, cfg.sample_hold_ms))
        ep.unknown_n += ep.unknown_pending
        ep.unknown_pending = 0
        ep.prev_true_t = t
        ep.prev_dir = info.get("direction")
        ep.t_last = t
        ep.add_ref(obs.observation_id, cfg.ref_head, cfg.ref_tail)
        ep.add_confidence(info.get("confidence"))
        ep.add_quality(obs.quality)
        for key, value in info.get("max", {}).items():
            ep.raise_max(key, value)
        for key in info.get("bump", ()):
            ep.bump(key)

    def expire(self, w: float) -> list[IncidentChange]:
        ep = self.ep
        if ep is None or self.last_known_t is None or w - self.last_known_t <= self.ttl:
            return []
        self.ep = None
        if ep.is_open:
            return self.e._close(ep, ep.t_last, IncidentEndReason.SOURCE_LOST)
        return []

    def close_all(self, reason: IncidentEndReason) -> list[IncidentChange]:
        ep, self.ep = self.ep, None
        if ep is not None and ep.is_open:
            return self.e._close(ep, ep.t_last, reason)
        return []

    def reset(self) -> None:
        self.ep = None
        self.last_known_t = None


class FusionEngine:
    """``proctor_contracts.interfaces.IncidentEngine`` implementation (A05)."""

    def __init__(self, session_id: str, source_mode: SourceMode, config: FusionConfig | None = None):
        self.session_id = session_id
        self.source_mode = SourceMode(source_mode)
        self.cfg = config or FusionConfig()
        self.rule_version = RULE_VERSION
        self.config_version = self.cfg.version
        self._sid = (
            session_id
            if len(session_id) <= MAX_ID_SESSION_PART
            else "h" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        )
        self._w: float | None = None  # watermark
        self._ref_t: float | None = None  # freshness reference before a source delivers anything
        self._anchor: datetime | None = None
        self._paused = False
        self._finished = False
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._last_t: dict[str, float] = {}
        self._last_seen: dict[str, float] = {}
        self._indet_since: dict[str, float | None] = {}
        self._health_bad: dict[str, str] = {}
        self._series = {"phone_visible": _Series(self.cfg.sample_hold_ms), "gaze_down": _Series(self.cfg.sample_hold_ms)}
        self._obj_t0: float | None = None  # first phone observation: start of the object scene baseline
        self._obj_baseline: dict[str, list[Any]] = {}  # class_name -> boxes seen during the baseline window
        self._face_seen_t: float | None = None  # last attention observation with a face (objects need the student)
        self._rules = {rule: _IntervalRule(self, rule, src) for rule, src in INTERVAL_RULES.items()}
        self._burst: Episode | None = None
        self._escape: Episode | None = None
        self._lost_since: float | None = None
        self._mon: Episode | None = None
        self._ids: dict[IncidentRule, int] = {}
        self._eps: dict[IncidentRule, list[Episode]] = {rule: [] for rule in IncidentRule}
        self._partners: dict[IncidentRule, list[IncidentRule]] = {}
        for a, b in self.cfg.link_pairs:
            self._partners.setdefault(R(a), []).append(R(b))
            self._partners.setdefault(R(b), []).append(R(a))
        for rule in self._partners:
            self._partners[rule] = sorted(set(self._partners[rule]), key=lambda r: r.value)
        self._stats: Counter[str] = Counter()
        from proctor.audio.fusion import AudioFusion
        self._audio = AudioFusion(session_id, self.source_mode, self._wall)

    # ------------------------------------------------------------------ IncidentEngine
    def consume(self, observation: Observation) -> list[IncidentChange]:
        obs = observation
        if self._finished:
            self._stats["after_finish"] += 1
            return []
        if obs.session_id != self.session_id:
            self._stats["foreign_session"] += 1
            return []
        if obs.source_mode != self.source_mode:
            self._stats["mode_mismatch"] += 1
            return []
        if obs.observation_id in self._seen:
            self._stats["duplicate"] += 1
            return []
        if self._paused:
            self._stats["while_paused"] += 1
            return []
        self._remember(obs.observation_id)
        t = float(obs.t_session_ms)
        stream = obs.kind if not isinstance(obs, HealthObservation) else f"health.{obs.health.component.value}"
        last = self._last_t.get(stream)
        if last is not None and t < last:
            self._stats["late"] += 1
            return []
        self._last_t[stream] = t
        if self._anchor is None:
            self._anchor = obs.wall_time - timedelta(milliseconds=t)
        if self._ref_t is None:
            self._ref_t = t
        if obs.kind in SOURCES and self._w is not None and self._w - t > self.cfg.source_ttl_ms[obs.kind]:
            self._stats["stale_on_arrival"] += 1
            return []
        self._stats[f"accepted_{obs.kind}"] += 1
        self._w = t if self._w is None else max(self._w, t)
        out = self._expire(self._w)
        out += self._identity_hook(obs, t)  # A05 1.3: identity (A13), kept separate from the kind dispatch below
        if isinstance(obs, PhoneObservation):
            out += self._on_phone(obs, t)
        elif isinstance(obs, AttentionObservation):
            out += self._on_attention(obs, t)
        elif isinstance(obs, EnvironmentObservation):
            out += self._on_environment(obs, t)
        elif isinstance(obs, HealthObservation):
            out += self._on_health(obs, t)
        elif isinstance(obs, AudioObservation):
            if self._w - t <= self._audio.cfg.sample_hold_ms:
                out += self._audio.feed(obs)
            else:
                self._stats["stale_on_arrival"] += 1
        return out

    def advance(self, t_session_ms: float) -> list[IncidentChange]:
        if self._finished or self._paused:
            return []
        if self.source_mode == SourceMode.REPLAY:
            return []  # the recording is the clock: expiry already ran when the data advanced
        t = float(t_session_ms)
        if self._ref_t is None:
            self._ref_t = t
        if self._w is None or t > self._w:
            self._w = t
        return self._expire(self._w)

    def set_paused(self, paused: bool, t_session_ms: float) -> list[IncidentChange]:
        if self._finished or paused == self._paused:
            return []
        t = float(t_session_ms)
        if paused:
            out = self._close_everything(t, IncidentEndReason.SESSION_PAUSED)
            self._paused = True
            self._last_seen.clear()
            self._indet_since.clear()
            for tracker in self._rules.values():
                tracker.reset()
            self._stats["pauses"] += 1
            return out
        self._paused = False
        if self.source_mode == SourceMode.REPLAY:
            self._ref_t = None  # freshness restarts with the next recorded observation
            return []
        self._w = t if self._w is None else max(self._w, t)
        self._ref_t = self._w
        out: list[IncidentChange] = []
        for component, code in sorted(self._health_bad.items()):  # still broken after resume
            out += self._mon_activate(f"health.{component}", self._w, self._w, code)
        return out

    def finish(self, t_session_ms: float, reason: IncidentEndReason) -> list[IncidentChange]:
        if self._finished:
            return []
        out = [] if self._paused else self._close_everything(float(t_session_ms), IncidentEndReason(reason))
        self._finished = True
        return out

    def config_snapshot(self) -> dict[str, Any]:
        return {
            "engine": "proctor.fusion",
            "rule_version": self.rule_version,
            "config_version": self.config_version,
            "note": "initial thresholds are hypotheses for tuning on recorded sessions, not validated norms",
            "priority_meaning": "priority of human review from transparent rules; not a probability of cheating",
            "config": self.cfg.to_dict(),
        }

    # ------------------------------------------------------------------ extras (not in the Protocol)
    def stats(self) -> dict[str, int]:
        """Counters of ignored input (late, duplicate, foreign_session, mode_mismatch, ...)."""
        return dict(self._stats)

    @property
    def finished(self) -> bool:
        return self._finished

    # ------------------------------------------------------------------ input handlers
    def _remember(self, observation_id: str) -> None:
        self._seen.add(observation_id)
        self._seen_order.append(observation_id)
        if len(self._seen_order) > DEDUP_MAX:  # older re-deliveries are rejected as late anyway
            self._seen.discard(self._seen_order.popleft())

    def _usable(self, obs: PhoneObservation | AttentionObservation) -> bool:
        return obs.status in USABLE and (obs.quality is None or obs.quality >= self.cfg.min_quality)

    @staticmethod
    def _undetermined_code(obs: PhoneObservation | AttentionObservation) -> str:
        if obs.quality_flags:
            return obs.quality_flags[0]
        reasons = getattr(obs, "reasons", None)
        if reasons:
            return reasons[0]
        return f"status_{obs.status.value}"

    def _on_phone(self, obs: PhoneObservation, t: float) -> list[IncidentChange]:
        out = self._source_seen("phone", t)
        usable = self._usable(obs)
        by_name: dict[PhoneSignalName, list] = {}
        for sig in obs.signals:
            by_name.setdefault(sig.name, []).append(sig)

        def value(name: PhoneSignalName) -> tuple[bool | None, float | None, SignalState | None]:
            sigs = by_name.get(name, [])
            state = _combine([s.state for s in sigs])
            confs = [s.confidence for s in sigs if s.state == SignalState.PRESENT and s.confidence is not None]
            return (_state_value(state) if usable else None), (max(confs) if confs else None), state

        visible, vis_conf, _ = value(PhoneSignalName.PHONE_VISIBLE)
        raised, raised_conf, _ = value(PhoneSignalName.PHONE_RAISED)
        capture, capture_conf, capture_state = value(PhoneSignalName.POSSIBLE_SCREEN_CAPTURE)
        phones = [d for d in obs.detections if d.class_name == PHONE_CLASS]
        det_conf = max((d.confidence for d in phones), default=None)
        out += self._determinacy("phone", t, visible is not None, self._undetermined_code(obs))
        self._series["phone_visible"].add(t, visible)
        vis_info: dict[str, Any] = {
            "confidence": vis_conf if vis_conf is not None else det_conf,
            "max": {"max_phones": len(phones)},
        }
        if capture_state == SignalState.INSUFFICIENT_EVIDENCE:
            vis_info["bump"] = ("capture_unobservable",)
        out += self._rules[R.PHONE_VISIBLE].feed(t, visible, obs, vis_info)
        raised, raised_info = self._raised_evidence(obs, t, usable, raised, raised_conf)
        capture, capture_info = self._capture_evidence(obs, t, usable, capture, capture_conf)
        out += self._rules[R.PHONE_RAISED].feed(t, raised, obs, raised_info)
        out += self._rules[R.POSSIBLE_SCREEN_CAPTURE].feed(t, capture, obs, capture_info)
        out += self._on_objects(obs, t, usable)
        return out

    def _on_objects(self, obs: PhoneObservation, t: float, usable: bool) -> list[IncidentChange]:
        """Review-only objects from A03 detections: book -> foreign_object_visible, laptop/tv -> second_screen_visible.
        Objects present during the scene baseline (first object_baseline_ms) are the room and never count."""
        cfg = self.cfg
        if self._obj_t0 is None:
            self._obj_t0 = t
        in_baseline = t - self._obj_t0 < cfg.object_baseline_ms
        student_here = self._face_seen_t is not None and t - self._face_seen_t <= cfg.object_face_window_ms
        found: dict[IncidentRule, list[float]] = {R.FOREIGN_OBJECT_VISIBLE: [], R.SECOND_SCREEN_VISIBLE: []}
        for d in obs.detections if usable else []:
            if d.confidence < cfg.object_min_confidence:
                continue
            rule = (R.FOREIGN_OBJECT_VISIBLE if d.class_name in cfg.foreign_object_classes
                    else R.SECOND_SCREEN_VISIBLE if d.class_name in cfg.second_screen_classes else None)
            if rule is None:
                continue
            if in_baseline:
                self._obj_baseline.setdefault(d.class_name, []).append(d.bbox)
                continue
            if any(_iou(d.bbox, b) >= cfg.object_baseline_iou for b in self._obj_baseline.get(d.class_name, ())):
                continue  # part of the room seen at the start
            found[rule].append(d.confidence)
        out: list[IncidentChange] = []
        for rule, confs in found.items():
            # student away: the room behind is visible -> no evidence either way (never opens, never clears)
            value = None if not usable or (not in_baseline and not student_here) else (False if in_baseline else bool(confs))
            out += self._rules[rule].feed(t, value, obs, {"confidence": max(confs) if confs else None})
        return out

    def _on_attention(self, obs: AttentionObservation, t: float) -> list[IncidentChange]:
        out = self._source_seen("attention", t)
        usable = self._usable(obs)
        face_count = obs.face_count if usable else None
        if face_count:
            self._face_seen_t = t
        self._remember_face(obs, t, usable)
        out += self._determinacy("attention", t, face_count is not None, self._undetermined_code(obs))

        direction: Direction | None = None
        via_head = False
        if face_count and obs.primary_face_present is not False:
            gaze_dir = obs.gaze.direction if obs.gaze is not None else Direction.UNKNOWN
            head_dir = obs.head_direction
            away = (Direction.LEFT, Direction.RIGHT, Direction.DOWN)
            if gaze_dir in away:
                direction = gaze_dir
            elif head_dir in away:
                # head clearly turned while the eye estimate says centre/up: the head pose wins
                # (real A04 output on a camera clip: yaw -50 deg, gaze flipping left/up every 0.5 s)
                direction, via_head = head_dir, True
            elif gaze_dir != Direction.UNKNOWN:
                direction = gaze_dir
            elif head_dir != Direction.UNKNOWN:
                direction, via_head = head_dir, True
        down = None if direction is None else direction == Direction.DOWN
        side = None if direction is None else direction in (Direction.LEFT, Direction.RIGHT)
        if face_count is None:
            missing = None
        elif face_count == 0:
            missing = True
        elif obs.primary_face_present is False:
            missing = None  # someone is in frame but not the primary face: undetermined
        else:
            missing = False
        multiple = None if face_count is None else face_count >= 2

        gaze_conf = obs.gaze.confidence if obs.gaze is not None else None
        bump = []
        if obs.gaze is None or not obs.gaze.calibrated:
            bump.append("uncalibrated")
        if via_head:
            bump.append("head_fallback")
        gaze_info = {"confidence": gaze_conf, "bump": tuple(bump)}
        side_info = {**gaze_info, "direction": direction.value if side else None}
        second = sorted((f.confidence for f in obs.faces if f.confidence is not None), reverse=True)
        multi_info = {
            "confidence": second[1] if len(second) >= 2 else None,
            "max": {"max_faces": face_count or 0},
        }
        self._series["gaze_down"].add(t, down)
        out += self._rules[R.GAZE_PROLONGED_DOWN].feed(t, down, obs, gaze_info)
        out += self._rules[R.GAZE_PROLONGED_SIDE].feed(t, side, obs, side_info)
        out += self._rules[R.FACE_MISSING].feed(t, missing, obs, {})
        out += self._rules[R.MULTIPLE_FACES].feed(t, multiple, obs, multi_info)
        return out

    def _on_health(self, obs: HealthObservation, t: float) -> list[IncidentChange]:
        health = obs.health
        component = health.component.value
        key = f"health.{component}"
        if health.status in BAD_HEALTH:
            self._health_bad[component] = health.code
            return self._mon_activate(key, t, t, health.code, obs.observation_id)
        if health.status == HealthStatus.OK:
            self._health_bad.pop(component, None)
            return self._mon_deactivate(key, t, obs.observation_id)
        return []  # starting: neither degraded nor recovered

    def _on_environment(self, obs: EnvironmentObservation, t: float) -> list[IncidentChange]:
        action = obs.action
        if action in BLOCKED_ACTIONS:
            return self._env_blocked(obs, t)
        if action == A.FOCUS_LOST:
            return self._escape_event(obs, t, lost=True)
        if action == A.FOREIGN_WINDOW_FOREGROUND:
            return self._escape_event(obs, t, lost=False)
        if action == A.FOCUS_REGAINED:
            return self._escape_regained(obs, t)
        if action == A.ENFORCEMENT_ERROR:
            return self._mon_activate("environment.enforcement_error", t, t, "enforcement_error", obs.observation_id, point=True)
        if action == A.EXAM_MODE_RELEASED:
            return self._mon_activate("environment.exam_mode_released", t, t, "exam_mode_released", obs.observation_id)
        if action == A.EXAM_MODE_ENGAGED:
            return self._mon_deactivate("environment.exam_mode_released", t, obs.observation_id)
        self._stats["environment_ignored"] += 1
        return []

    # ------------------------------------------------------------------ environment rules
    def _env_blocked(self, obs: EnvironmentObservation, t: float) -> list[IncidentChange]:
        out: list[IncidentChange] = []
        ep = self._burst
        if ep is not None and t - ep.t_last > self.cfg.env_burst_gap_ms:  # normally done by _expire
            self._burst = None
            out += self._close(ep, ep.t_last, IncidentEndReason.CONDITION_CLEARED)
            ep = None
        opening = ep is None
        if ep is None:
            ep = self._burst = Episode(rule=R.ENVIRONMENT_BLOCKED_ACTION, t_start=t, t_last=t)
        key_a, key_e = f"action.{obs.action.value}", f"enforcement.{obs.enforcement.value}"
        new_kind = key_a not in ep.counters or key_e not in ep.counters
        ep.bump(key_a)
        ep.bump(key_e)
        ep.t_last = t
        ep.add_ref(obs.observation_id, self.cfg.ref_head, self.cfg.ref_tail)
        if opening:
            return out + self._open(ep, None, t)
        return out + self._maybe_update(ep, t, force=new_kind or ep.count in ENV_UPDATE_MILESTONES)

    def _escape_event(self, obs: EnvironmentObservation, t: float, *, lost: bool) -> list[IncidentChange]:
        out: list[IncidentChange] = []
        ep = self._escape
        if ep is not None and self._lost_since is None and t - ep.t_last > self.cfg.env_escape_merge_gap_ms:
            self._escape = None  # normally done by _expire
            out += self._close(ep, ep.t_last, IncidentEndReason.CONDITION_CLEARED)
            ep = None
        opening = ep is None
        if ep is None:
            ep = self._escape = Episode(rule=R.ENVIRONMENT_ESCAPE, t_start=t, t_last=t)
            ep.appearances = 0
        ep.add_ref(obs.observation_id, self.cfg.ref_head, self.cfg.ref_tail)
        ep.t_last = max(ep.t_last, t)
        force = False
        if lost:
            if self._lost_since is not None:
                self._stats["focus_lost_repeated"] += 1
            else:
                self._lost_since = t
                ep.appearances += 1
                ep.bump("focus_lost_count")
                force = True
        else:
            ep.bump("foreign_window_count")
            name = obs.detail.process_name
            procs: set[str] = ep.counters.setdefault("processes", set())
            if name and name not in procs:
                procs.add(name)
                force = True
        if opening:
            return out + self._open(ep, None, t)
        return out + self._maybe_update(ep, t, force=force)

    def _escape_regained(self, obs: EnvironmentObservation, t: float) -> list[IncidentChange]:
        ep = self._escape
        if ep is None or self._lost_since is None:
            self._stats["focus_regained_unpaired"] += 1
            return []
        ep.bump("focus_lost_ms", t - self._lost_since)
        self._lost_since = None
        ep.t_last = max(ep.t_last, t)
        ep.add_ref(obs.observation_id, self.cfg.ref_head, self.cfg.ref_tail)
        return self._maybe_update(ep, t, force=True)

    # ------------------------------------------------------------------ monitoring (coverage gaps)
    def _mon_any_active(self) -> bool:
        return self._mon is not None and any(c["active"] for c in self._mon.counters["causes"].values())

    def _mon_activate(
        self, key: str, t_act: float, gap_start: float, code: str, observation_id: str | None = None, point: bool = False
    ) -> list[IncidentChange]:
        out: list[IncidentChange] = []
        ep = self._mon
        if ep is not None:
            cause = ep.counters["causes"].get(key)
            if cause is not None and cause["active"]:
                return []
            if not self._mon_any_active() and t_act - ep.t_last > self.cfg.monitoring_merge_gap_ms:
                self._mon = None
                out += self._close(ep, ep.t_last, IncidentEndReason.CONDITION_CLEARED)
                ep = None
        opening = ep is None
        if ep is None:
            # the incident covers the data gap itself (e.g. from the last phone observation), while
            # merge decisions use the activation time t_act, which is data-defined in every schedule
            ep = self._mon = Episode(rule=R.MONITORING_DEGRADED, t_start=min(gap_start, t_act), t_last=t_act)
            ep.counters["causes"] = {}
        cause = ep.counters["causes"].setdefault(key, {"active": False, "start": gap_start, "code": code, "total_ms": 0.0})
        cause.update(active=not point, start=gap_start, code=code)
        ep.t_last = max(ep.t_last, t_act)
        if observation_id is not None:
            ep.add_ref(observation_id, self.cfg.ref_head, self.cfg.ref_tail)
        if opening:
            return out + self._open(ep, None, t_act)
        return out + [self._emit(ep, UPDATED)]

    def _mon_deactivate(self, key: str, t: float, observation_id: str | None = None) -> list[IncidentChange]:
        ep = self._mon
        if ep is None:
            return []
        cause = ep.counters["causes"].get(key)
        if cause is None or not cause["active"]:
            return []
        cause["active"] = False
        cause["total_ms"] += max(0.0, t - cause["start"])
        ep.t_last = max(ep.t_last, t)
        if observation_id is not None:
            ep.add_ref(observation_id, self.cfg.ref_head, self.cfg.ref_tail)
        return [self._emit(ep, UPDATED)]

    def _source_seen(self, source: str, t: float) -> list[IncidentChange]:
        self._last_seen[source] = t
        return self._mon_deactivate(f"stale.{source}", t)

    def _determinacy(self, source: str, t: float, determinate: bool, code: str) -> list[IncidentChange]:
        if determinate:
            self._indet_since[source] = None
            return self._mon_deactivate(f"undetermined.{source}", t)
        since = self._indet_since.get(source)
        if since is None:
            self._indet_since[source] = since = t
        if t - since >= self.cfg.undetermined_ms:
            return self._mon_activate(f"undetermined.{source}", t, since, code)
        return []

    # ------------------------------------------------------------------ time-driven expiry
    # ------------------------------------------------------------------ phone raised / capture geometry (A05 1.4)
    def _remember_face(self, obs: AttentionObservation, t: float, usable: bool) -> None:
        face = next((f for f in obs.faces if f.is_primary), None) if usable and obs.face_count else None
        if face is not None:
            self._last_face = (t, face.bbox)

    def _raised_phone(self, obs: PhoneObservation, t: float) -> Any | None:
        """The highest phone box that is at face level (or in the upper part of the frame without a face)."""
        cfg = self.cfg
        face = getattr(self, "_last_face", None)
        fresh = face is not None and t - face[0] <= cfg.phone_face_max_age_ms
        line = (face[1].y_min + cfg.phone_raise_face_frac * (face[1].y_max - face[1].y_min)) if fresh else cfg.phone_raise_fallback_top
        cands = [d for d in obs.detections if d.class_name == PHONE_CLASS and d.confidence >= cfg.phone_raise_min_confidence and d.bbox.y_min <= line]
        return min(cands, key=lambda d: d.bbox.y_min) if cands else None

    def _raised_evidence(self, obs: PhoneObservation, t: float, usable: bool, a03: bool | None, a03_conf: float | None) -> tuple[bool | None, dict[str, Any]]:
        """A03's track heuristic OR the face-relative geometry; unusable frame -> no evidence."""
        if not usable:
            return None, {"confidence": a03_conf}
        det = self._raised_phone(obs, t)
        hist = self.__dict__.setdefault("_raise_hist", [])
        if det is not None:
            b = det.bbox
            hist.append((t, (b.x_min + b.x_max) / 2.0, (b.y_min + b.y_max) / 2.0))
        else:
            hist.clear()
        bump = ("face_level",) if det is not None else ()
        conf = max([c for c in (a03_conf, det.confidence if det is not None else None) if c is not None], default=None)
        return bool(a03) or det is not None, {"confidence": conf, "bump": bump}  # usable frame: clear yes/no

    def _capture_evidence(self, obs: PhoneObservation, t: float, usable: bool, a03: bool | None, a03_conf: float | None) -> tuple[bool | None, dict[str, Any]]:
        """Raised, central and almost still for >= phone_capture_steady_ms (uses the history kept by _raised_evidence)."""
        cfg = self.cfg
        if not usable:
            return None, {"confidence": a03_conf}
        hist = [h for h in self.__dict__.get("_raise_hist", []) if t - h[0] <= cfg.phone_capture_steady_ms + 400.0]
        self._raise_hist = hist
        steady = False
        if hist and hist[-1][0] - hist[0][0] >= cfg.phone_capture_steady_ms:
            gaps_ok = all(b[0] - a[0] <= cfg.phone_raised.pending_gap_ms for a, b in zip(hist, hist[1:]))
            mx = sum(h[1] for h in hist) / len(hist)
            my = sum(h[2] for h in hist) / len(hist)
            still = max(max(abs(h[1] - mx), abs(h[2] - my)) for h in hist) <= cfg.phone_capture_max_motion
            central = cfg.phone_capture_x_min <= mx <= cfg.phone_capture_x_max
            steady = gaps_ok and still and central
        return bool(a03) or steady, {"confidence": a03_conf, "bump": ("steady_at_face",) if steady else ()}

    # ------------------------------------------------------------------ identity (contracts 1.1, A13)
    def _identity_rule(self) -> "_IntervalRule":
        """Registered lazily so the engine's generic expiry/pause/finish handle it like any interval rule."""
        rule = self._rules.get(R.IDENTITY_MISMATCH)
        if rule is None:
            rule = _IntervalRule(self, R.IDENTITY_MISMATCH, "attention")  # source only selects a TTL default...
            rule.ttl = self.cfg.identity_ttl_ms  # ...replaced by the identity stream's own TTL
            self._rules[R.IDENTITY_MISMATCH] = rule
        return rule

    def _identity_hook(self, obs: Observation, t: float) -> list[IncidentChange]:
        """IdentityObservation: same_person = absent -> mismatch, present -> cleared, unknown / not enrolled /
        unusable -> no evidence (never opens, never clears)."""
        if getattr(obs, "kind", None) != "identity":
            return []
        state = obs.same_person
        if not obs.enrolled or obs.status not in USABLE or state not in (SignalState.PRESENT, SignalState.ABSENT):
            value = None
        else:
            value = state == SignalState.ABSENT
        return self._identity_rule().feed(t, value, obs, {"confidence": None})

    def _expire(self, w: float) -> list[IncidentChange]:
        cfg = self.cfg
        out: list[IncidentChange] = self._audio.expire(w)
        for tracker in self._rules.values():
            out += tracker.expire(w)
        for source in SOURCES:
            last = self._last_seen.get(source)
            ref, ttl = (last, cfg.source_ttl_ms[source]) if last is not None else (
                self._ref_t,
                max(cfg.source_ttl_ms[source], cfg.startup_grace_ms),
            )
            if ref is not None and w - ref > ttl:
                code = "no_observations" if last is not None else "no_observations_since_start"
                out += self._mon_activate(f"stale.{source}", ref + ttl, ref, code)
        if self._burst is not None and w - self._burst.t_last > cfg.env_burst_gap_ms:
            ep, self._burst = self._burst, None
            out += self._close(ep, ep.t_last, IncidentEndReason.CONDITION_CLEARED)
        if self._escape is not None and self._lost_since is None and w - self._escape.t_last > cfg.env_escape_merge_gap_ms:
            ep, self._escape = self._escape, None
            out += self._close(ep, ep.t_last, IncidentEndReason.CONDITION_CLEARED)
        if self._mon is not None and not self._mon_any_active() and w - self._mon.t_last > cfg.monitoring_merge_gap_ms:
            ep, self._mon = self._mon, None
            out += self._close(ep, ep.t_last, IncidentEndReason.CONDITION_CLEARED)
        self._prune(w)
        return out

    def _prune(self, w: float) -> None:
        horizon = w - self.cfg.recent_retention_ms
        for rule, eps in self._eps.items():
            if eps and eps[0].closed and eps[0].t_end is not None and eps[0].t_end < horizon:
                self._eps[rule] = [e for e in eps if not (e.closed and e.t_end is not None and e.t_end < horizon)]
        open_starts = [t.ep.t_start for t in self._rules.values() if t.ep is not None]
        keep_from = min([horizon, *open_starts])
        for series in self._series.values():
            series.prune(keep_from)

    def _close_everything(self, t: float, reason: IncidentEndReason) -> list[IncidentChange]:
        t_fin = t if self._w is None else max(t, self._w)
        out: list[IncidentChange] = self._audio.close(reason)
        for tracker in self._rules.values():
            out += tracker.close_all(reason)
        if self._burst is not None:
            ep, self._burst = self._burst, None
            out += self._close(ep, ep.t_last, reason)
        if self._escape is not None:
            ep, self._escape = self._escape, None
            end = ep.t_last
            if self._lost_since is not None:  # still without focus: lasted until now
                ep.bump("focus_lost_ms", max(0.0, t_fin - self._lost_since))
                end = max(end, t_fin)
            out += self._close(ep, end, reason)
        self._lost_since = None
        if self._mon is not None:
            ep, self._mon = self._mon, None
            end = ep.t_last
            for cause in ep.counters["causes"].values():
                if cause["active"]:  # gap lasted until now
                    cause["active"] = False
                    cause["total_ms"] += max(0.0, t_fin - cause["start"])
                    end = max(end, t_fin)
            out += self._close(ep, end, reason)
        return out

    # ------------------------------------------------------------------ incident lifecycle
    def _new_id(self, rule: IncidentRule) -> str:
        n = self._ids[rule] = self._ids.get(rule, 0) + 1
        return f"inc.{self._sid}.{rule.value}.{n}"

    def _open(self, ep: Episode, frame_id: int | None, t: float) -> list[IncidentChange]:
        ep.incident_id = self._new_id(ep.rule)
        ep.trigger_frame_id = frame_id
        ep.last_emit_t = t
        self._eps[ep.rule].append(ep)
        partners = self._link(ep)
        return [self._emit(ep, OPENED), *(self._emit(o, UPDATED) for o in partners)]

    def _maybe_update(self, ep: Episode, t: float, force: bool = False) -> list[IncidentChange]:
        """Data-driven re-emission of an open episode (never driven by advance ticks)."""
        if force or t - ep.last_emit_t >= self.cfg.update_every_ms:
            ep.last_emit_t = t
            return [self._emit(ep, UPDATED)]
        built = self._build(ep)
        if built.priority != ep.last_priority:
            ep.last_emit_t = t
            return [self._emit(ep, UPDATED, built)]
        return []

    def _close(self, ep: Episode, t_end: float, reason: IncidentEndReason) -> list[IncidentChange]:
        ep.closed = True
        ep.t_end = max(t_end, ep.t_start)
        ep.end_reason = reason
        ep.frozen_ctx = self._context(ep)
        partners = self._link(ep)
        return [self._emit(ep, CLOSED), *(self._emit(o, UPDATED) for o in partners)]

    def _link(self, ep: Episode) -> list[Episode]:
        """Cross-reference overlapping incidents of linked rules (one phone episode = one group)."""
        changed: list[Episode] = []
        a0, a1 = ep.interval()
        for rule in self._partners.get(ep.rule, ()):
            for other in self._eps[rule]:
                if other is ep or other.incident_id is None or other.incident_id in ep.related:
                    continue
                b0, b1 = other.interval()
                if max(a0, b0) - min(a1, b1) <= self.cfg.link_max_gap_ms:
                    ep.related.add(other.incident_id)
                    other.related.add(ep.incident_id)  # type: ignore[arg-type]
                    changed.append(other)
        return changed

    def _context(self, ep: Episode) -> dict[str, float]:
        if ep.frozen_ctx is not None:
            return ep.frozen_ctx
        a, b = ep.interval()
        if ep.rule in PHONE_FAMILY:
            return {"gaze_down_overlap_ms": self._series["gaze_down"].true_ms(a, b)}
        if ep.rule == R.GAZE_PROLONGED_DOWN:
            return {"phone_visible_overlap_ms": self._series["phone_visible"].true_ms(a, b)}
        return {}

    def _build(self, ep: Episode) -> explain.Built:
        a, b = ep.interval()
        return explain.build(ep, b - a, self._context(ep), self.cfg, self.source_mode)

    def _wall(self, t: float) -> datetime:
        if self._anchor is None:  # no observation yet (display only)
            self._anchor = utc_now() - timedelta(milliseconds=self._w or 0.0)
        return self._anchor + timedelta(milliseconds=t)

    def _emit(self, ep: Episode, change: IncidentChangeType, built: explain.Built | None = None) -> IncidentChange:
        built = built or self._build(ep)
        ep.update_seq += 1
        ep.last_priority = built.priority
        t_start, t_end = ep.interval()
        incident = Incident(
            incident_id=ep.incident_id,
            session_id=self.session_id,
            rule_id=ep.rule,
            category=CATEGORY[ep.rule],
            state=IncidentState.CLOSED if ep.closed else IncidentState.OPEN,
            priority=built.priority,
            t_start_ms=t_start,
            t_end_ms=t_end if ep.closed else None,
            wall_start=self._wall(t_start),
            wall_end=self._wall(t_end) if ep.closed else None,
            duration_ms=round(max(0.0, t_end - t_start), 3),
            source_mode=self.source_mode,
            max_confidence=ep.conf_max,
            mean_quality=ep.mean_quality,
            explanation=built.explanation,
            observation_ids=ep.observation_ids()[:200],
            observation_count=ep.count,
            trigger_frame_id=ep.trigger_frame_id,
            related_incident_ids=sorted(ep.related)[:32],
            rule_version=self.rule_version,
            config_version=self.config_version,
            end_reason=ep.end_reason,
            update_seq=ep.update_seq,
        )
        return IncidentChange(change=change, incident=incident)


# A05 1.3 (contracts 1.1): category of the identity rule, registered here to keep the shared tables untouched
CATEGORY.setdefault(R.IDENTITY_MISMATCH, IncidentCategory.IDENTITY)
