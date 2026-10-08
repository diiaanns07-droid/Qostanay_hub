"""Versioned thresholds of the A05 fusion rules.

Every number here is an initial HYPOTHESIS to be tuned on recorded sessions, not a validated norm.
The effective configuration is hashed into ``config_version`` and returned by
``IncidentEngine.config_snapshot()`` so reports always state which thresholds produced an incident.

Time values are milliseconds on the session timeline (``t_session_ms``), never frame counts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

RULE_VERSION = "a05-rules-1.4.0"
CONFIG_NAME = "a05-default-1"


@dataclass(frozen=True)
class IntervalParams:
    """Hysteresis parameters of one continuous rule.

    * open: the condition is observed true from t_first to t_last with
      ``t_last - t_first >= min_duration_ms`` and at least ``min_count`` true observations;
      while pending, a gap without a true observation longer than ``pending_gap_ms`` restarts it
      (a brief glance never accumulates into a prolonged episode);
    * merge/close: once open, a false observation at least ``merge_gap_ms`` after the last true one
      closes the episode (``condition_cleared``, ``t_end`` = last true observation); a re-appearance
      inside the gap continues the same episode (one incident, ``appearances`` + 1);
    * ``long_ms``: an episode LONGER than this raises review priority one level (None = never).
    """

    min_duration_ms: float
    min_count: int
    pending_gap_ms: float
    merge_gap_ms: float
    long_ms: float | None = None


def _priority_base() -> dict[str, str]:
    return {
        "phone_visible": "medium",
        "phone_raised": "high",  # zones spec 2026-10-08: phone raised / aimed at the screen = high
        "possible_screen_capture": "high",
        "gaze_prolonged_down": "low",
        "gaze_prolonged_side": "low",
        "face_missing": "medium",
        "multiple_faces": "high",
        "environment_blocked_action": "low",
        "environment_escape": "medium",
        "monitoring_degraded": "low",
        # contracts 1.1: review-only objects from A03 detections (COCO book / laptop, tv)
        "foreign_object_visible": "medium",
        "second_screen_visible": "medium",
        # contracts 1.1: A13 identity check (same_person = absent)
        "identity_mismatch": "high",
    }


def _source_ttl() -> dict[str, float]:
    # phone analysis is scheduled at ~8 fps, attention at ~15 fps (Settings.*_max_fps)
    return {"phone": 2000.0, "attention": 1500.0}


def _link_pairs() -> tuple[tuple[str, str], ...]:
    # incidents of these rules are cross-referenced (related_incident_ids) when their intervals
    # overlap or are at most link_max_gap_ms apart: one phone episode = one group, not N alerts
    return (
        ("phone_visible", "phone_raised"),
        ("phone_visible", "possible_screen_capture"),
        ("phone_raised", "possible_screen_capture"),
        ("phone_visible", "gaze_prolonged_down"),
        ("phone_raised", "gaze_prolonged_down"),
        ("possible_screen_capture", "gaze_prolonged_down"),
    )


@dataclass(frozen=True)
class FusionConfig:
    name: str = CONFIG_NAME

    # --- continuous rules (phone: A03 signals, attention: A04 observations) ---
    phone_visible: IntervalParams = IntervalParams(1000.0, 3, 500.0, 2000.0, 10000.0)
    # phone raised (ТЗ 2.1): evidence must hold >= 1 s (>= 4 phone observations, gaps <= 0.6 s) — a single A03
    # raise signal used to open an episode, and realtime frame sampling made it appear in some runs only
    phone_raised: IntervalParams = IntervalParams(1000.0, 4, 600.0, 3000.0, None)
    possible_screen_capture: IntervalParams = IntervalParams(500.0, 2, 500.0, 5000.0, None)
    # gaze (zones spec, agreed with the captain): 3-8 s = low, > 8 s = medium; never higher by duration
    gaze_prolonged_down: IntervalParams = IntervalParams(3000.0, 3, 400.0, 1500.0, 8000.0)
    gaze_prolonged_side: IntervalParams = IntervalParams(3000.0, 3, 400.0, 1500.0, 8000.0)
    # face missing longer than the threshold = medium (zones spec); no escalation to high by duration
    face_missing: IntervalParams = IntervalParams(2000.0, 3, 500.0, 2000.0, None)
    multiple_faces: IntervalParams = IntervalParams(1000.0, 3, 500.0, 3000.0, None)
    # review-only objects (A03 detections, score >= object_min_confidence): visible >= 2 s, medium, no escalation
    foreign_object_visible: IntervalParams = IntervalParams(2000.0, 3, 700.0, 2000.0, None)
    second_screen_visible: IntervalParams = IntervalParams(2000.0, 3, 700.0, 2000.0, None)
    # A13 identity: face differs from the one enrolled at exam start for >= 3 s (>= 2 observations; identity runs
    # at ~1-2 Hz, so gaps up to 2 s are tolerated) -> high, no escalation
    identity_mismatch: IntervalParams = IntervalParams(3000.0, 2, 2000.0, 3000.0, None)
    identity_ttl_ms: float = 5000.0  # no identity observation this long -> an open episode closes source_lost

    # --- phone raised / possible screen capture: geometry relative to the A04 face (A05 1.4) ---
    # "raised" = a phone box whose TOP is above the line at phone_raise_face_frac of the primary face height
    # (0 = top of the face, 1 = chin; 0.65 ~ mouth); the face must be fresher than phone_face_max_age_ms.
    # Without a face (the phone may cover it): top in the upper part of the frame (<= phone_raise_fallback_top).
    phone_raise_face_frac: float = 0.65
    phone_raise_fallback_top: float = 0.35
    phone_raise_min_confidence: float = 0.30
    phone_face_max_age_ms: float = 600.0
    # "possible screen capture" = raised AND almost still (centre within phone_capture_max_motion of the window
    # mean) AND in the central band for >= phone_capture_steady_ms. A pattern, never "a photo was taken".
    phone_capture_steady_ms: float = 1500.0
    phone_capture_max_motion: float = 0.05
    phone_capture_x_min: float = 0.20
    phone_capture_x_max: float = 0.80

    # --- objects (A03 detections by class_name) ---
    object_min_confidence: float = 0.5
    foreign_object_classes: tuple[str, ...] = ("book",)
    second_screen_classes: tuple[str, ...] = ("laptop", "tv")
    # Scene baseline: objects already in the frame during the first object_baseline_ms of monitoring are the
    # room (e.g. computer-lab monitors behind the student; measured on real clips: tv/laptop 0.5-0.88 all clip
    # long) and never open an episode; a detection of the same class overlapping a baseline box (IoU >=
    # object_baseline_iou) is ignored. Only objects that APPEAR later count.
    object_baseline_ms: float = 10000.0
    object_baseline_iou: float = 0.3
    # Objects count only while the student is in the frame (a face seen within this time): when the student
    # leaves, the room behind becomes visible (measured: lab monitors -> "second screen" while away).
    object_face_window_ms: float = 1500.0

    # --- freshness / unknown ---
    source_ttl_ms: dict[str, float] = field(default_factory=_source_ttl)
    startup_grace_ms: float = 5000.0  # a source may stay silent this long after start/resume
    undetermined_ms: float = 3000.0  # continuous unknown/error/low-quality output -> monitoring cause
    min_quality: float = 0.2  # observations with quality below this are treated as unknown
    sample_hold_ms: float = 600.0  # one observation stands for at most this long (overlap, gap facts)

    # --- correlation / grouping ---
    corr_min_overlap_ms: float = 1000.0  # phone visible AND gaze "down" at the same time -> high
    link_max_gap_ms: float = 1000.0
    link_pairs: tuple[tuple[str, str], ...] = field(default_factory=_link_pairs)

    # --- environment (A06 events) ---
    env_burst_gap_ms: float = 10000.0  # restricted actions closer than this form one incident
    env_repeat_count: int = 5  # a burst with this many actions raises priority one level
    env_escape_merge_gap_ms: float = 3000.0  # focus lost again within this gap = same incident
    env_escape_long_ms: float = 5000.0

    # --- monitoring (coverage) ---
    monitoring_merge_gap_ms: float = 3000.0
    monitoring_long_ms: float = 30000.0

    # --- priority of HUMAN REVIEW (never a probability of guilt) ---
    priority_base: dict[str, str] = field(default_factory=_priority_base)
    repeat_segments: int = 3  # an episode with this many appearances raises priority one level ...
    # ... only for these rules (gaze/face priority depends on duration only: zones spec)
    repeat_escalation_rules: tuple[str, ...] = ("phone_visible", "phone_raised", "possible_screen_capture", "multiple_faces")

    # --- output shaping ---
    update_every_ms: float = 5000.0  # an open episode is re-emitted at most this often (episode time)
    ref_head: int = 20  # observation_ids keeps the first ref_head ...
    ref_tail: int = 100  # ... and the last ref_tail contributing observation ids
    recent_retention_ms: float = 180000.0  # closed incidents/series kept for linking

    def __post_init__(self) -> None:
        rules = set(_priority_base())
        levels = {"low", "medium", "high"}
        if set(self.priority_base) != rules or not set(self.priority_base.values()) <= levels:
            raise ValueError(f"priority_base must map every rule {sorted(rules)} to one of {sorted(levels)}")
        if set(self.source_ttl_ms) != {"phone", "attention"} or min(self.source_ttl_ms.values()) <= 0:
            raise ValueError("source_ttl_ms needs positive 'phone' and 'attention' values")
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, IntervalParams):
                if value.min_duration_ms < 0 or value.min_count < 1 or value.pending_gap_ms < 0 or value.merge_gap_ms <= 0:
                    raise ValueError(f"invalid {f.name}: {value}")
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                raise ValueError(f"{f.name} must be >= 0")
        if not set(self.repeat_escalation_rules) <= rules:
            raise ValueError(f"invalid repeat_escalation_rules {self.repeat_escalation_rules}")
        for pair in self.link_pairs:
            if len(pair) != 2 or not set(pair) <= rules:
                raise ValueError(f"invalid link pair {pair}")

    # ------------------------------------------------------------------ helpers
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["link_pairs"] = [list(p) for p in self.link_pairs]
        return data

    @property
    def version(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return f"{self.name}+{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]}"

    def interval(self, rule: str) -> IntervalParams:
        value = getattr(self, rule)
        if not isinstance(value, IntervalParams):
            raise KeyError(rule)
        return value

    @classmethod
    def from_dict(cls, overrides: dict[str, Any] | None = None) -> "FusionConfig":
        """Default config with overrides (nested dicts for IntervalParams). Unknown keys are rejected."""
        base = cls()
        if not overrides:
            return base
        known = {f.name: f for f in fields(cls)}
        changes: dict[str, Any] = {}
        for key, value in overrides.items():
            if key not in known:
                raise ValueError(f"unknown fusion config key: {key}")
            current = getattr(base, key)
            if isinstance(current, IntervalParams):
                if not isinstance(value, dict):
                    raise ValueError(f"{key} must be an object")
                unknown = set(value) - {f.name for f in fields(IntervalParams)}
                if unknown:
                    raise ValueError(f"unknown {key} keys: {sorted(unknown)}")
                changes[key] = replace(current, **value)
            elif isinstance(current, dict):
                changes[key] = {**current, **value}
            elif key == "link_pairs":
                changes[key] = tuple(tuple(p) for p in value)
            else:
                changes[key] = type(current)(value) if current is not None else value
        return replace(base, **changes)
