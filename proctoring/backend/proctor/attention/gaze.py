"""Approximate gaze: head pose + eye-in-head rotation, temporal filters, direction classes.

This is NOT eye tracking. Fused gaze = head yaw/pitch (ray frame, see geometry.py) + a rough
eye-in-head rotation estimated from iris offsets and MediaPipe blendshapes. Directions are
classified against screen edges measured during calibration (or generic edges when the
calibration was skipped, flagged "uncalibrated").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from statistics import median

from proctor_contracts.v1 import Direction

from .config import AttentionConfig
from .geometry import EyeFeatures

SIDE_DIRECTIONS = (Direction.LEFT, Direction.RIGHT, Direction.UP, Direction.DOWN)


@dataclass(frozen=True)
class EyeRest:
    """Iris/blendshape values of this person looking at the screen center (or generic)."""

    h_right: float
    h_left: float
    v: float
    bs_pitch: float

    @classmethod
    def generic(cls, cfg: AttentionConfig) -> "EyeRest":
        return cls(cfg.rest_h_right, cfg.rest_h_left, cfg.rest_v, cfg.rest_bs_pitch)


@dataclass(frozen=True)
class EyeRotation:
    yaw: float | None  # degrees, + = subject's right (relative to the head)
    pitch: float | None  # degrees, + = up
    source: str  # "iris", "iris+bs", "bs", "none"
    reasons: tuple[str, ...] = ()


def eye_rotation(eyes: EyeFeatures, head_yaw: float, head_pitch: float, rest: EyeRest, cfg: AttentionConfig) -> EyeRotation:
    """Eye-in-head rotation from one frame. The eye farther from the camera is ignored when the
    head is turned a lot (it is foreshortened); closed eyes give no cue.

    Parallax: the iris lies ~9 mm in front of the eye corners, so turning only the head moves
    it in the image towards the turn: h_measured ~ h_eye - p*sin(yaw) (+yaw moves it to the image
    left). p = cfg.iris_parallax compensates that (see config for the evidence)."""
    reasons: list[str] = []
    if eyes.closed_right and eyes.closed_left:
        return EyeRotation(None, None, "none", ("eyes_closed",))
    use_r = not eyes.closed_right and eyes.h_right is not None
    use_l = not eyes.closed_left and eyes.h_left is not None
    if head_yaw > cfg.max_eye_yaw_for_both_eyes_deg:  # turned to their right: right eye is far
        use_r = False
        reasons.append("far_eye_ignored")
    elif head_yaw < -cfg.max_eye_yaw_for_both_eyes_deg:
        use_l = False
        reasons.append("far_eye_ignored")
    for side in ("right", "left"):
        width = eyes.width_right_px if side == "right" else eyes.width_left_px
        opening = eyes.open_right if side == "right" else eyes.open_left
        if width < cfg.min_eye_width_px or (opening is not None and opening < cfg.min_iris_openness):
            if side == "right":
                use_r = False
            else:
                use_l = False
    par_h = cfg.iris_parallax * math.sin(math.radians(head_yaw))
    par_v = cfg.iris_parallax * math.sin(math.radians(head_pitch))
    hs, vs = [], []
    if use_r:
        hs.append(eyes.h_right + par_h - rest.h_right)  # type: ignore[operator]
        vs.append(eyes.v_right + par_v - rest.v)  # type: ignore[operator]
    if use_l:
        hs.append(eyes.h_left + par_h - rest.h_left)  # type: ignore[operator]
        vs.append(eyes.v_left + par_v - rest.v)  # type: ignore[operator]
    yaw = pitch = None
    sources = []
    if hs:
        # h > 0: iris towards the image right = the subject's LEFT -> negative subject-centric yaw
        yaw = -cfg.eye_yaw_gain_deg * (sum(hs) / len(hs))
        sources.append("iris")
    elif eyes.bs_yaw is not None:
        yaw = cfg.bs_yaw_gain_deg * eyes.bs_yaw
        sources.append("bs")
    if eyes.bs_pitch is not None:
        pitch = cfg.bs_pitch_gain_deg * (eyes.bs_pitch - rest.bs_pitch)
        if "bs" not in sources:
            sources.append("bs")
    elif vs:
        pitch = -cfg.eye_pitch_gain_deg * (sum(vs) / len(vs))
    if yaw is None and pitch is None:
        reasons.append("iris_unreliable")
        return EyeRotation(None, None, "none", tuple(reasons))
    return EyeRotation(yaw, pitch, "+".join(sources), tuple(reasons))


@dataclass(frozen=True)
class DirectionModel:
    """Screen geometry in fused-gaze degrees + the head-pose center. Generic or calibrated."""

    center_yaw: float
    center_pitch: float
    left: float  # fused yaw when looking at the left screen edge (negative side)
    right: float
    up: float
    down: float
    head_center_yaw: float
    head_center_pitch: float
    rest: EyeRest
    calibrated: bool
    calibration_id: str | None = None
    anchor_bbox: tuple[float, float, float, float] | None = None  # student's face at the center target

    @classmethod
    def generic(cls, cfg: AttentionConfig) -> "DirectionModel":
        return cls(
            center_yaw=cfg.default_center_yaw_deg,
            center_pitch=cfg.default_center_pitch_deg,
            left=cfg.default_edge_left_deg,
            right=cfg.default_edge_right_deg,
            up=cfg.default_edge_up_deg,
            down=cfg.default_edge_down_deg,
            head_center_yaw=cfg.default_center_yaw_deg,
            head_center_pitch=cfg.default_center_pitch_deg,
            rest=EyeRest.generic(cfg),
            calibrated=False,
        )

    def spans(self) -> dict[Direction, float]:
        return {
            Direction.LEFT: self.center_yaw - self.left,
            Direction.RIGHT: self.right - self.center_yaw,
            Direction.UP: self.up - self.center_pitch,
            Direction.DOWN: self.center_pitch - self.down,
        }

    def with_rest(self, rest: EyeRest) -> "DirectionModel":
        return replace(self, rest=rest)


def gaze_levels(model: DirectionModel, yaw: float, pitch: float, cfg: AttentionConfig) -> dict[Direction, float]:
    """Position along each direction normalized so that 1.0 = the entry threshold
    (screen edge + margin). Values < 1 are inside, i.e. "center"."""
    spans = model.spans()
    offsets = {
        Direction.LEFT: model.center_yaw - yaw,
        Direction.RIGHT: yaw - model.center_yaw,
        Direction.UP: pitch - model.center_pitch,
        Direction.DOWN: model.center_pitch - pitch,
    }
    levels = {}
    for d in SIDE_DIRECTIONS:
        span = max(spans[d], 1e-3)
        threshold = span + max(cfg.edge_margin * span, cfg.edge_margin_min_deg)
        levels[d] = max(0.0, offsets[d]) / threshold
    return levels


def head_levels(model: DirectionModel, yaw: float, pitch: float, cfg: AttentionConfig) -> dict[Direction, float]:
    return {
        Direction.LEFT: max(0.0, model.head_center_yaw - yaw) / cfg.head_yaw_threshold_deg,
        Direction.RIGHT: max(0.0, yaw - model.head_center_yaw) / cfg.head_yaw_threshold_deg,
        Direction.UP: max(0.0, pitch - model.head_center_pitch) / cfg.head_up_threshold_deg,
        Direction.DOWN: max(0.0, model.head_center_pitch - pitch) / cfg.head_down_threshold_deg,
    }


def pick_direction(levels: dict[Direction, float], current: Direction, exit_level: float) -> Direction:
    """Hysteresis: enter a side direction above 1.0, stay in it while above exit_level (< 1)."""
    above = {d: v for d, v in levels.items() if v >= 1.0}
    if current in levels and levels[current] >= exit_level:
        best = max(above, key=above.__getitem__) if above else current
        return best if best != current and above[best] > levels[current] else current
    if above:
        return max(above, key=above.__getitem__)
    return Direction.CENTER


class Debouncer:
    """A new direction must persist debounce_ms (frame time) before it is reported. Unknown
    input is reported as unknown immediately; a short unknown gap keeps the previous state."""

    def __init__(self, debounce_ms: float, reset_gap_ms: float):
        self.debounce_ms = debounce_ms
        self.reset_gap_ms = reset_gap_ms
        self.reset()

    def reset(self) -> None:
        self.stable: Direction | None = None
        self.candidate: Direction | None = None
        self.since = 0.0
        self.last_valid_t: float | None = None

    def update(self, raw: Direction, t_ms: float) -> Direction:
        if raw == Direction.UNKNOWN:
            return Direction.UNKNOWN
        if self.last_valid_t is not None and t_ms - self.last_valid_t > self.reset_gap_ms:
            self.reset()
        self.last_valid_t = t_ms
        if self.stable is None or raw == self.stable:
            self.stable = raw
            self.candidate = None
            return raw
        if self.candidate != raw:
            self.candidate, self.since = raw, t_ms
        if t_ms - self.since >= self.debounce_ms:
            self.stable, self.candidate = raw, None
        return self.stable

    @property
    def current(self) -> Direction:
        return self.stable or Direction.CENTER


class OneEuro:
    """One-Euro low-pass filter (Casiez et al. 2012) on frame time (ms)."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self) -> None:
        self.x: float | None = None
        self.dx = 0.0
        self.t: float | None = None

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t_ms: float) -> float:
        if self.x is None or self.t is None:
            self.x, self.t, self.dx = x, t_ms, 0.0
            return x
        dt = (t_ms - self.t) / 1000.0
        if dt <= 0:
            return self.x
        a_d = self._alpha(dt, self.d_cutoff)
        self.dx = a_d * (x - self.x) / dt + (1 - a_d) * self.dx
        a = self._alpha(dt, self.min_cutoff + self.beta * abs(self.dx))
        self.x = a * x + (1 - a) * self.x
        self.t = t_ms
        return self.x


def robust_spread(values: list[float]) -> float:
    """1.4826 * median absolute deviation (a std estimate robust to blinks/outliers)."""
    if len(values) < 2:
        return 0.0
    m = median(values)
    return 1.4826 * median(abs(v - m) for v in values)
