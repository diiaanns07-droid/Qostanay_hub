"""Direction boundaries, hysteresis, debounce, filters (no images)."""

from __future__ import annotations

import pytest

from proctor.attention.config import AttentionConfig
from proctor.attention.gaze import (
    Debouncer,
    DirectionModel,
    EyeRest,
    OneEuro,
    gaze_levels,
    head_levels,
    pick_direction,
    robust_spread,
)
from proctor_contracts.v1 import Direction

CFG = AttentionConfig()


def calibrated(**kw) -> DirectionModel:
    base = dict(center_yaw=2.0, center_pitch=-10.0, left=-12.0, right=18.0, up=-2.0, down=-24.0,
                head_center_yaw=1.0, head_center_pitch=-9.0, rest=EyeRest.generic(CFG), calibrated=True,
                calibration_id="cal-test")
    base.update(kw)
    return DirectionModel(**base)


def threshold(span: float) -> float:
    return span + max(CFG.edge_margin * span, CFG.edge_margin_min_deg)


@pytest.mark.parametrize(
    "direction,axis_point",
    [
        (Direction.RIGHT, lambda m, d: (m.center_yaw + d, m.center_pitch)),
        (Direction.LEFT, lambda m, d: (m.center_yaw - d, m.center_pitch)),
        (Direction.UP, lambda m, d: (m.center_yaw, m.center_pitch + d)),
        (Direction.DOWN, lambda m, d: (m.center_yaw, m.center_pitch - d)),
    ],
)
def test_boundaries_each_direction(direction, axis_point):
    m = calibrated()
    thr = threshold(m.spans()[direction])
    inside = gaze_levels(m, *axis_point(m, thr - 0.05), CFG)
    outside = gaze_levels(m, *axis_point(m, thr + 0.05), CFG)
    assert pick_direction(inside, Direction.CENTER, 1 - CFG.hysteresis) == Direction.CENTER
    assert pick_direction(outside, Direction.CENTER, 1 - CFG.hysteresis) == direction
    at_edge = gaze_levels(m, *axis_point(m, m.spans()[direction]), CFG)  # exactly the screen edge
    assert pick_direction(at_edge, Direction.CENTER, 1 - CFG.hysteresis) == Direction.CENTER


def test_asymmetric_spans_are_respected():
    m = calibrated()  # left span 14, right span 16
    assert threshold(14.0) != threshold(16.0)
    lv = gaze_levels(m, m.center_yaw - threshold(14.0) - 0.1, m.center_pitch, CFG)
    rv = gaze_levels(m, m.center_yaw + threshold(14.0) + 0.1, m.center_pitch, CFG)
    assert lv[Direction.LEFT] > 1.0 and rv[Direction.RIGHT] < 1.0


def test_minimum_margin_in_degrees_for_tiny_screens():
    m = calibrated(left=0.0, right=4.0)  # 2 deg spans: margin is at least edge_margin_min_deg
    lv = gaze_levels(m, m.center_yaw + 2.0 + CFG.edge_margin_min_deg - 0.1, m.center_pitch, CFG)
    assert lv[Direction.RIGHT] < 1.0


def test_hysteresis_enter_stay_leave():
    m = calibrated()
    thr = threshold(m.spans()[Direction.DOWN])
    exit_level = 1 - CFG.hysteresis

    def at(depth):
        return gaze_levels(m, m.center_yaw, m.center_pitch - depth, CFG)

    assert pick_direction(at(thr * 0.95), Direction.CENTER, exit_level) == Direction.CENTER
    assert pick_direction(at(thr * 1.02), Direction.CENTER, exit_level) == Direction.DOWN
    assert pick_direction(at(thr * 0.95), Direction.DOWN, exit_level) == Direction.DOWN  # stays
    assert pick_direction(at(thr * (exit_level - 0.02)), Direction.DOWN, exit_level) == Direction.CENTER


def test_switch_to_stronger_direction():
    m = calibrated()
    levels = gaze_levels(m, m.center_yaw + 40.0, m.center_pitch - threshold(14.0) * 0.9, CFG)
    assert pick_direction(levels, Direction.DOWN, 1 - CFG.hysteresis) == Direction.RIGHT


def test_head_levels_relative_to_calibrated_head_center():
    m = calibrated()
    lv = head_levels(m, m.head_center_yaw + CFG.head_yaw_threshold_deg + 0.5, m.head_center_pitch, CFG)
    assert lv[Direction.RIGHT] > 1.0
    lv = head_levels(m, m.head_center_yaw, m.head_center_pitch - CFG.head_down_threshold_deg + 0.5, CFG)
    assert lv[Direction.DOWN] < 1.0


def test_debouncer_suppresses_single_frame_flicker():
    d = Debouncer(debounce_ms=150.0, reset_gap_ms=600.0)
    assert d.update(Direction.CENTER, 0) == Direction.CENTER
    assert d.update(Direction.DOWN, 66) == Direction.CENTER  # one frame only
    assert d.update(Direction.CENTER, 133) == Direction.CENTER
    for t in (200, 266):
        assert d.update(Direction.DOWN, t) == Direction.CENTER
    assert d.update(Direction.DOWN, 350) == Direction.DOWN  # persisted >= 150 ms


def test_debouncer_unknown_passes_through_and_short_gap_keeps_state():
    d = Debouncer(debounce_ms=150.0, reset_gap_ms=600.0)
    d.update(Direction.LEFT, 0)
    assert d.update(Direction.UNKNOWN, 66) == Direction.UNKNOWN  # never converted
    assert d.update(Direction.LEFT, 133) == Direction.LEFT  # no re-debounce after a short gap
    assert d.update(Direction.UNKNOWN, 200) == Direction.UNKNOWN
    assert d.update(Direction.CENTER, 1000) == Direction.CENTER  # long gap: fresh start


def test_one_euro_converges_and_tracks_steps():
    f = OneEuro(CFG.filter_min_cutoff_hz, CFG.filter_beta, CFG.filter_d_cutoff_hz)
    out = [f(0.0, t * 66.7) for t in range(5)]
    assert out[-1] == 0.0
    vals = [f(30.0, (5 + t) * 66.7) for t in range(12)]
    assert 0.0 < vals[0] < 30.0 and vals[-1] > 28.5
    assert f(99.0, 11 * 66.7 + 5 * 66.7 - 1000) == pytest.approx(f.x)  # non-increasing time: unchanged


def test_robust_spread_ignores_outliers():
    assert robust_spread([1.0, 1.1, 0.9, 1.0, 30.0]) < 0.5
    assert robust_spread([5.0]) == 0.0
