"""Five-target calibration (center, left, right, up, down) on real samples.

Thread model: start/select/finish/cancel/skip/state are called from API threads, offer() from
the analyzer's consumer thread; everything is guarded by one lock.

Completion is decided by sample count and quality, never by a timer. A target fails
explicitly (message_code) when its samples are unstable, point to the wrong side or are not
distinct from the center, or when no valid sample arrived within a timeout measured on frame
time; the UI then retries that target. The resulting model only stores robust medians of a
few angles/offsets of this session (no images, no landmarks) and is dropped on reset().
"""

from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Callable
from uuid import uuid4

from proctor_contracts.interfaces import InvalidStateError
from proctor_contracts.v1 import (
    CalibrationPhase,
    CalibrationState,
    CalibrationTarget,
    CalibrationTargetState,
    CalibrationTargetStatus,
    Direction,
    ErrorCode,
    utc_now,
)

from .config import AttentionConfig
from .gaze import DirectionModel, EyeRest, eye_rotation, robust_spread
from .geometry import EyeFeatures

TARGET_ORDER = (
    CalibrationTarget.CENTER,
    CalibrationTarget.LEFT,
    CalibrationTarget.RIGHT,
    CalibrationTarget.UP,
    CalibrationTarget.DOWN,
)


@dataclass(frozen=True)
class CalibrationSample:
    t_ms: float
    head_yaw: float
    head_pitch: float
    eyes: EyeFeatures
    quality: float
    bbox: tuple[float, float, float, float]


@dataclass
class _Target:
    state: CalibrationTargetState = CalibrationTargetState.PENDING
    samples: list[CalibrationSample] = field(default_factory=list)
    rejections: Counter = field(default_factory=Counter)
    started_t: float | None = None
    quality: float | None = None
    message_code: str | None = None


def fused(sample: CalibrationSample, rest: EyeRest, cfg: AttentionConfig) -> tuple[float, float] | None:
    rot = eye_rotation(sample.eyes, sample.head_yaw, sample.head_pitch, rest, cfg)
    yaw = sample.head_yaw + (rot.yaw or 0.0)
    pitch = sample.head_pitch + (rot.pitch or 0.0)
    return yaw, pitch


def rest_from_center(samples: list[CalibrationSample], cfg: AttentionConfig) -> EyeRest:
    """This person's iris/blendshape values while looking at the screen center, with the same
    parallax compensation as gaze.eye_rotation, so the center target maps to zero eye rotation."""
    generic = EyeRest.generic(cfg)

    def med(values: list[float | None], default: float) -> float:
        vals = [v for v in values if v is not None]
        return float(median(vals)) if vals else default

    h_r: list[float | None] = []
    h_l: list[float | None] = []
    v_vals: list[float | None] = []
    for s in samples:
        par_h = cfg.iris_parallax * math.sin(math.radians(s.head_yaw))
        par_v = cfg.iris_parallax * math.sin(math.radians(s.head_pitch))
        h_r.append(None if s.eyes.h_right is None else s.eyes.h_right + par_h)
        h_l.append(None if s.eyes.h_left is None else s.eyes.h_left + par_h)
        vs = [v + par_v for v in (s.eyes.v_right, s.eyes.v_left) if v is not None]
        v_vals.append(sum(vs) / len(vs) if vs else None)
    return EyeRest(
        h_right=med(h_r, generic.h_right),
        h_left=med(h_l, generic.h_left),
        v=med(v_vals, generic.v),
        bs_pitch=med([s.eyes.bs_pitch for s in samples], generic.bs_pitch),
    )


# Targets whose failure does not fail the calibration (the generic span is used instead).
OPTIONAL_TARGETS = frozenset({CalibrationTarget.UP})

# expected sign of (target - center) along the axis that the target probes
_AXIS = {
    CalibrationTarget.LEFT: (0, -1.0),  # yaw decreases (subject's left)
    CalibrationTarget.RIGHT: (0, 1.0),
    CalibrationTarget.UP: (1, 1.0),  # pitch increases
    CalibrationTarget.DOWN: (1, -1.0),
}


def _median_point(samples: list[CalibrationSample], rest: EyeRest, cfg: AttentionConfig) -> tuple[float, float]:
    pts = [p for p in (fused(s, rest, cfg) for s in samples) if p is not None]
    return float(median(p[0] for p in pts)), float(median(p[1] for p in pts))


def check_target(
    target: CalibrationTarget, center_pt: tuple[float, float], target_pt: tuple[float, float], cfg: AttentionConfig
) -> str | None:
    """None if the edge target lies on the expected side of the center by the minimum separation."""
    axis, sign = _AXIS[target]
    delta = (target_pt[axis] - center_pt[axis]) * sign
    if delta <= -cfg.calibration_min_separation_deg:
        return "target_wrong_side"
    if delta < cfg.calibration_min_separation_deg:
        return "target_not_distinct"
    return None


def fit_model(
    samples: dict[CalibrationTarget, list[CalibrationSample]], cfg: AttentionConfig, calibration_id: str
) -> tuple[DirectionModel | None, dict[CalibrationTarget, str]]:
    """Fit the per-session direction model. Returns (model or None, problems per target)."""
    missing = [t for t in TARGET_ORDER if t not in OPTIONAL_TARGETS and not samples.get(t)]
    if missing:
        return None, {t: "no_samples" for t in missing}
    center_samples = samples[CalibrationTarget.CENTER]
    rest = rest_from_center(center_samples, cfg)
    med_pt = {t: _median_point(samples[t], rest, cfg) for t in TARGET_ORDER if samples.get(t)}
    c = med_pt[CalibrationTarget.CENTER]
    problems = {t: code for t in _AXIS if t in med_pt and (code := check_target(t, c, med_pt[t], cfg)) is not None}
    for t in OPTIONAL_TARGETS:
        # A laptop webcam sits just above the screen, so looking at the top edge barely moves the
        # eyes/head relative to the center; "up" is not a case requirement. Use the generic span.
        if t not in med_pt or problems.pop(t, None) is not None:
            med_pt[t] = (c[0], c[1] + (cfg.default_edge_up_deg - cfg.default_center_pitch_deg))
    if problems:
        return None, problems
    boxes = [s.bbox for s in center_samples]
    anchor = tuple(float(median(b[i] for b in boxes)) for i in range(4))
    model = DirectionModel(
        center_yaw=c[0],
        center_pitch=c[1],
        left=med_pt[CalibrationTarget.LEFT][0],
        right=med_pt[CalibrationTarget.RIGHT][0],
        up=med_pt[CalibrationTarget.UP][1],
        down=med_pt[CalibrationTarget.DOWN][1],
        head_center_yaw=float(median(s.head_yaw for s in center_samples)),
        head_center_pitch=float(median(s.head_pitch for s in center_samples)),
        rest=rest,
        calibrated=True,
        calibration_id=calibration_id,
        anchor_bbox=anchor,  # type: ignore[arg-type]
    )
    return model, {}


class CalibrationController:
    def __init__(self, cfg: AttentionConfig, id_factory: Callable[[], str] | None = None):
        self.cfg = cfg
        self._id_factory = id_factory or (lambda: f"cal-{uuid4().hex[:16]}")
        self._lock = threading.RLock()
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        """Drop everything (samples and model): session end / new session."""
        with self._lock:
            self._phase = CalibrationPhase.NOT_STARTED
            self._id: str | None = None
            self._targets = {t: _Target() for t in TARGET_ORDER}
            self._current: CalibrationTarget | None = None
            self._message: str | None = None
            self._model: DirectionModel | None = None
            self._updated = utc_now()

    def _touch(self, message: str | None = None) -> None:
        if message is not None:
            self._message = message
        self._updated = utc_now()

    def state(self) -> CalibrationState:
        with self._lock:
            return CalibrationState(
                calibration_id=self._id,
                phase=self._phase,
                targets=[
                    CalibrationTargetStatus(
                        target=t,
                        state=d.state,
                        samples=min(len(d.samples), self.cfg.calibration_required_samples),
                        required_samples=self.cfg.calibration_required_samples,
                        quality=None if d.quality is None else round(min(1.0, max(0.0, d.quality)), 3),
                        message_code=d.message_code,
                    )
                    for t, d in self._targets.items()
                ],
                current_target=self._current,
                message_code=self._message,
                updated_at=self._updated,
            )

    def model(self) -> DirectionModel | None:
        with self._lock:
            return self._model if self._phase == CalibrationPhase.COMPLETED else None

    @property
    def collecting(self) -> bool:
        with self._lock:
            return self._phase == CalibrationPhase.COLLECTING and self._current is not None

    # ---------------------------------------------------------------- commands
    def start(self) -> CalibrationState:
        with self._lock:
            self.reset()
            self._phase = CalibrationPhase.COLLECTING
            self._id = self._id_factory()
            self._touch("select_target")
            return self.state()

    def select(self, target: CalibrationTarget) -> CalibrationState:
        with self._lock:
            if self._phase not in (CalibrationPhase.COLLECTING, CalibrationPhase.FAILED):
                raise InvalidStateError(
                    ErrorCode.INVALID_STATE, f"calibration is {self._phase.value}, start it first", phase=self._phase.value
                )
            if self._phase == CalibrationPhase.FAILED:
                self._phase = CalibrationPhase.COLLECTING  # retry after a failed finish
            prev = self._current
            if prev is not None and prev != target and self._targets[prev].state == CalibrationTargetState.COLLECTING:
                self._targets[prev] = _Target(message_code="interrupted")
            self._targets[target] = _Target(state=CalibrationTargetState.COLLECTING, message_code="collecting")
            self._current = target
            self._touch("collecting")
            return self.state()

    def finish(self) -> CalibrationState:
        with self._lock:
            if self._phase == CalibrationPhase.COMPLETED:
                return self.state()  # idempotent
            if self._phase not in (CalibrationPhase.COLLECTING, CalibrationPhase.FAILED):
                raise InvalidStateError(ErrorCode.INVALID_STATE, f"calibration is {self._phase.value}", phase=self._phase.value)
            self._current = None
            not_ok = [t for t, d in self._targets.items()
                      if d.state != CalibrationTargetState.OK and t not in OPTIONAL_TARGETS]
            if not_ok:
                for t in not_ok:
                    d = self._targets[t]
                    if d.state != CalibrationTargetState.FAILED:
                        self._targets[t] = _Target(state=CalibrationTargetState.FAILED, message_code=d.message_code
                                                   if d.message_code not in (None, "collecting") else "not_collected")
                self._phase = CalibrationPhase.FAILED
                self._touch("targets_incomplete")
                return self.state()
            assert self._id is not None
            model, problems = fit_model({t: d.samples for t, d in self._targets.items()}, self.cfg, self._id)
            if model is None:
                for t, code in problems.items():
                    self._fail(t, code)
                self._phase = CalibrationPhase.FAILED
                self._touch("targets_not_distinct")
                return self.state()
            self._model = model
            self._phase = CalibrationPhase.COMPLETED
            self._touch("calibration_ok")
            return self.state()

    def cancel(self) -> CalibrationState:
        with self._lock:
            self.reset()
            self._phase = CalibrationPhase.CANCELLED
            self._touch("cancelled")
            return self.state()

    def skip(self, reason: str) -> CalibrationState:
        with self._lock:
            keep_id = self._id
            self.reset()
            self._id = keep_id
            self._phase = CalibrationPhase.SKIPPED
            self._touch("skipped_by_operator")
            return self.state()

    # ------------------------------------------------------------------ samples
    def offer(self, t_ms: float, sample: CalibrationSample | None, rejection: str | None) -> None:
        """Called for every analyzed frame while collecting: a valid sample or a rejection code."""
        with self._lock:
            if self._phase != CalibrationPhase.COLLECTING or self._current is None:
                return
            target = self._current
            d = self._targets[target]
            if d.state != CalibrationTargetState.COLLECTING:
                return
            if d.started_t is None:
                d.started_t = t_ms
            if t_ms - d.started_t < self.cfg.calibration_settle_ms:
                return  # the student is still moving the eyes to the target
            if sample is not None:
                d.samples.append(sample)
                d.quality = sum(s.quality for s in d.samples) / len(d.samples)
            elif rejection:
                d.rejections[rejection] += 1
            if len(d.samples) >= self.cfg.calibration_required_samples:
                self._evaluate(target)
            elif t_ms - d.started_t > self.cfg.calibration_target_timeout_ms:
                code = d.rejections.most_common(1)[0][0] if d.rejections else "insufficient_samples"
                self._fail(target, code)
            elif d.rejections and not d.samples:
                d.message_code = d.rejections.most_common(1)[0][0]  # live hint for the UI
            else:
                d.message_code = "collecting"
            self._touch()

    def _fail(self, target: CalibrationTarget, code: str) -> None:
        d = self._targets[target]
        self._targets[target] = _Target(state=CalibrationTargetState.FAILED, quality=d.quality, message_code=code)

    def _evaluate(self, target: CalibrationTarget) -> None:
        d = self._targets[target]
        center = self._targets[CalibrationTarget.CENTER]
        if target == CalibrationTarget.CENTER:
            rest = rest_from_center(d.samples, self.cfg)
        elif center.state == CalibrationTargetState.OK:
            rest = rest_from_center(center.samples, self.cfg)
        else:
            rest = EyeRest.generic(self.cfg)
        pts = [p for p in (fused(s, rest, self.cfg) for s in d.samples) if p is not None]
        spread = max(robust_spread([p[0] for p in pts]), robust_spread([p[1] for p in pts]))
        if spread > self.cfg.calibration_max_spread_deg:
            self._fail(target, "unstable_fixation")
            return
        if target != CalibrationTarget.CENTER and center.state == CalibrationTargetState.OK:
            problem = check_target(
                target, _median_point(center.samples, rest, self.cfg), _median_point(d.samples, rest, self.cfg), self.cfg
            )
            if problem is not None:
                self._fail(target, problem)
                return
        q = (d.quality or 0.0) * (1.0 - 0.5 * spread / self.cfg.calibration_max_spread_deg)
        d.state = CalibrationTargetState.OK
        d.quality = q
        d.message_code = None


def direction_of(target: CalibrationTarget) -> Direction:
    return Direction(target.value)
