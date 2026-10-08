"""Phone signal state machines through the real PhoneAnalyzer with an injected FakeDetector (owner: A03).

FAKE DETECTIONS ONLY. Every box in this file is scripted by ``FakeDetector``; no model runs. These tests
check the temporal logic of phone_visible / phone_raised / possible_screen_capture (tracking, latching,
hysteresis, unknown vs absent, contract shape). They say NOTHING about how well YOLO11n finds phones.

A phone in the frame is not evidence that anything was photographed: possible_screen_capture is at most
"a pattern compatible with pointing a phone at the screen" and must never become ``absent`` (the webcam
cannot see which way the phone camera points). Every observation emitted here is validated against the
shared contract and scanned for wording that would claim a photo was taken.

Frames: ``make_frame(i)`` -> t_session_ms = i * FRAME_MS (125 ms, 8 fps), textured (usable) image unless
a scenario swaps in a uniform black frame.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Sequence

import pytest

from proctor.phone.analyzer import PhoneAnalyzer
from proctor.phone.config import PhoneConfig
from proctor.phone.detector import RawDetection
from proctor.phone.tests.helpers import FRAME_MS, SESSION, FakeDetector, box, make_frame, make_image
from proctor_contracts.v1 import (
    HealthStatus,
    ObservationStatus,
    PhoneObservation,
    PhoneSignal,
    PhoneSignalName,
    SignalState,
    SourceMode,
)

CFG = PhoneConfig()  # defaults; PhoneAnalyzer(settings, CFG) ignores QORGAU_PHONE_* env overrides
IMG = make_image()  # textured, usable (quality 1.0, no flags)
BLACK = make_image(value=0)  # uniform black: unusable (low_light + blank_frame)

VISIBLE = PhoneSignalName.PHONE_VISIBLE
RAISED = PhoneSignalName.PHONE_RAISED
CAPTURE = PhoneSignalName.POSSIBLE_SCREEN_CAPTURE
SIGNAL_ORDER = [VISIBLE, RAISED, CAPTURE]

# Wording that would turn a phone detection into a claim about a photo / a verdict.
_PHOTO_CLAIM = re.compile(r"photo|picture|screenshot|snapshot|shot|taken|took|captured|cheat|violation|guilt")

Script = Callable[[int], list[RawDetection]]


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
def frame_index_at(t_ms: float) -> int:
    """First frame index whose t_session_ms >= t_ms."""
    return math.ceil(t_ms / FRAME_MS)


def path_script(cys: Sequence[float | None], cx: float | Sequence[float] = 0.5, w: float = 0.10, h: float = 0.18, conf: float = 0.6) -> Script:
    """One phone per frame following ``cys`` (None = no detection); frames past the end are empty."""

    def script(i: int) -> list[RawDetection]:
        if i >= len(cys) or cys[i] is None:
            return []
        x = cx[i] if isinstance(cx, Sequence) else cx
        return [box(x, cys[i], w=w, h=h, conf=conf)]

    return script


def steady_script(cx: float, cy: float, w: float, h: float, conf: float = 0.6, frames: int | None = None) -> Script:
    return lambda i: [box(cx, cy, w=w, h=h, conf=conf)] if frames is None or i < frames else []


def no_phone(i: int) -> list[RawDetection]:
    return []


def sig(obs: PhoneObservation, name: PhoneSignalName) -> PhoneSignal:
    matches = [s for s in obs.signals if s.name == name]
    assert len(matches) == 1, f"expected exactly one {name} signal, got {obs.signals}"
    return matches[0]


def states(observations: Sequence[PhoneObservation], name: PhoneSignalName) -> list[tuple[str, str]]:
    return [(sig(o, name).state.value, sig(o, name).reason) for o in observations]


def assert_no_photo_claim(signal: PhoneSignal) -> None:
    assert not _PHOTO_CLAIM.search(signal.reason.lower()), f"{signal.name}: reason claims too much: {signal.reason}"
    for key, value in signal.facts.items():
        assert not _PHOTO_CLAIM.search(key.lower()), f"{signal.name}: fact key claims too much: {key}"
        if isinstance(value, str):
            assert not _PHOTO_CLAIM.search(value.lower()), f"{signal.name}: fact {key}={value!r} claims too much"
    # the webcam never observes which way the phone camera points
    assert signal.facts.get("camera_direction_observable") is not True


def check_observation(obs: PhoneObservation) -> None:
    """Invariants for EVERY observation in this file (all scenarios)."""
    assert isinstance(obs, PhoneObservation)
    PhoneObservation.model_validate(obs.model_dump(mode="json"))  # wire contract round trip
    assert obs.status != ObservationStatus.ERROR
    assert obs.producer.model_id == "fake-detector"  # never presented as a real model
    assert [s.name for s in obs.signals] == SIGNAL_ORDER
    for s in obs.signals:
        assert_no_photo_claim(s)
        if s.state == SignalState.PRESENT and s.confidence is not None:
            assert 0.0 <= s.confidence <= 1.0
    capture = sig(obs, CAPTURE)
    assert capture.state != SignalState.ABSENT, "possible_screen_capture must never be 'absent'"
    if capture.state == SignalState.PRESENT:
        assert capture.facts["camera_direction_observable"] is False
        assert capture.track_id is not None and capture.confidence is not None


def run(
    settings,
    script: Script,
    n: int,
    *,
    images: Callable[[int], object] | None = None,
    times: Sequence[float] | None = None,
    config: PhoneConfig = CFG,
) -> list[PhoneObservation]:
    analyzer = PhoneAnalyzer(settings, config, detector=FakeDetector(script=script))
    health = analyzer.load()
    assert health.status == HealthStatus.OK
    assert health.details.get("fake_detector") is True
    analyzer.start_session(SESSION, SourceMode.REPLAY)
    out: list[PhoneObservation] = []
    try:
        for i in range(n):
            image = images(i) if images is not None else IMG
            t = times[i] if times is not None else None
            result = analyzer.process(make_frame(i, t_ms=t, image=image))
            assert len(result) == 1, f"frame {i}: expected one observation, got {result}"
            obs = result[0]
            assert obs.frame_id == i and obs.observation_id == f"phone-{i}"
            check_observation(obs)
            out.append(obs)
    finally:
        analyzer.close()
    return out


# --------------------------------------------------------------------------------------------
# phone_visible
# --------------------------------------------------------------------------------------------
def test_visible_single_low_confidence_detection_is_unknown_unconfirmed(settings):
    obs = run(settings, lambda i: [box(0.5, 0.5, conf=0.3)] if i == 0 else [], 2)
    v0 = sig(obs[0], VISIBLE)
    assert (v0.state, v0.reason) == (SignalState.UNKNOWN, "unconfirmed_detection")
    assert v0.confidence == pytest.approx(0.3)
    assert v0.track_id is None
    assert v0.facts["detections"] == 1 and v0.facts["confirmed_tracks"] == 0
    # the raw detection is still reported, with its (new, unconfirmed) track
    assert len(obs[0].detections) == 1
    assert obs[0].detections[0].track_id == "ph-1"
    assert obs[0].detections[0].track_quality is not None and obs[0].detections[0].track_quality <= 0.25
    # an unconfirmed blip does not coast: next empty usable frame is a plain "no detection"
    v1 = sig(obs[1], VISIBLE)
    assert (v1.state, v1.reason) == (SignalState.ABSENT, "no_detection")


def test_visible_same_phone_in_two_consecutive_frames_is_present_with_track(settings):
    obs = run(settings, steady_script(0.5, 0.5, 0.10, 0.18, conf=0.3, frames=2), 2)
    assert states(obs, VISIBLE)[0] == ("unknown", "unconfirmed_detection")
    v1 = sig(obs[1], VISIBLE)
    assert (v1.state, v1.reason) == (SignalState.PRESENT, "detected_in_frame")
    assert v1.track_id == "ph-1"
    assert v1.track_id == obs[1].detections[0].track_id
    assert v1.confidence == pytest.approx(0.3)  # detector score of the box, not a cheating probability
    assert v1.facts["confirmed_tracks"] == 1
    assert v1.facts["visible_ms"] == pytest.approx(FRAME_MS)


def test_visible_two_far_apart_detections_do_not_confirm_one_track(settings):
    script = {0: [box(0.2, 0.3, conf=0.3)], 1: [box(0.8, 0.75, conf=0.3)]}
    obs = run(settings, lambda i: script.get(i, []), 2)
    v1 = sig(obs[1], VISIBLE)
    assert (v1.state, v1.reason) == (SignalState.UNKNOWN, "unconfirmed_detection")
    assert obs[1].detections[0].track_id == "ph-2"  # a different phone/track, nothing confirmed


@pytest.mark.parametrize(
    "conf, expected",
    [
        (0.80, ("present", "detected_high_confidence")),
        (CFG.high_conf_single, ("present", "detected_high_confidence")),  # boundary is inclusive
        (CFG.high_conf_single - 0.01, ("unknown", "unconfirmed_detection")),
    ],
)
def test_visible_single_detection_high_confidence_threshold(settings, conf, expected):
    obs = run(settings, lambda i: [box(0.5, 0.5, conf=conf)] if i == 0 else [], 1)
    v = sig(obs[0], VISIBLE)
    assert (v.state.value, v.reason) == expected
    assert v.confidence == pytest.approx(round(conf, 3))
    if v.state == SignalState.PRESENT:
        assert v.track_id == "ph-1"
        assert v.facts["confirmed_tracks"] == 0  # present without track confirmation


def test_visible_coasts_after_detections_stop_then_absent(settings):
    last_det = 3  # detections on frames 0..3
    n = 12
    obs = run(settings, lambda i: [box(0.5, 0.5)] if i <= last_det else [], n)
    t_last = last_det * FRAME_MS
    for i in range(1, last_det + 1):
        assert states(obs[i : i + 1], VISIBLE)[0] == ("present", "detected_in_frame")
    for i in range(last_det + 1, n):
        v = sig(obs[i], VISIBLE)
        since = i * FRAME_MS - t_last
        if since <= CFG.coast_visible_ms:
            assert (v.state, v.reason) == (SignalState.PRESENT, "track_coasting"), (i, since)
            assert v.confidence is None  # no detector score in a frame without a detection
            assert v.track_id == "ph-1"
            assert v.facts["ms_since_detection"] == pytest.approx(since)
            assert obs[i].detections == []
        else:
            assert (v.state, v.reason) == (SignalState.ABSENT, "no_detection"), (i, since)
            assert v.track_id is None
    # sanity: the scenario really covered both phases
    assert ("present", "track_coasting") in states(obs, VISIBLE)
    assert states(obs, VISIBLE)[-1] == ("absent", "no_detection")


def test_visible_coasting_boundary_is_inclusive(settings):
    t1 = FRAME_MS
    times = [0.0, t1, t1 + CFG.coast_visible_ms, t1 + CFG.coast_visible_ms + 1.0]
    obs = run(settings, lambda i: [box(0.5, 0.5)] if i < 2 else [], 4, times=times)
    assert states(obs, VISIBLE)[2] == ("present", "track_coasting")
    assert states(obs, VISIBLE)[3] == ("absent", "no_detection")


def test_black_frame_without_detections_is_unknown_not_absent(settings):
    obs = run(settings, no_phone, 3, images=lambda i: BLACK)
    for o in obs:
        assert o.status == ObservationStatus.UNKNOWN
        assert o.quality == 0.0
        assert "blank_frame" in o.quality_flags and "low_light" in o.quality_flags
        for name in SIGNAL_ORDER:
            s = sig(o, name)
            assert (s.state, s.reason) == (SignalState.UNKNOWN, "frame_unusable"), name
            assert s.confidence is None


def test_black_frame_after_usable_no_phone_frames_switches_absent_to_unknown(settings):
    obs = run(settings, no_phone, 4, images=lambda i: BLACK if i >= 2 else IMG)
    assert states(obs, VISIBLE) == [("absent", "no_detection")] * 2 + [("unknown", "frame_unusable")] * 2
    assert [o.status for o in obs] == [ObservationStatus.OK] * 2 + [ObservationStatus.UNKNOWN] * 2


# --------------------------------------------------------------------------------------------
# phone_raised
# --------------------------------------------------------------------------------------------
# fast rise 0.85 -> 0.45 in < 1 s, hold, slow descent through the hysteresis band, then below exit.
RISE = [0.85, 0.80, 0.75, 0.70, 0.64, 0.57, 0.51, 0.45, 0.45, 0.45, 0.45]
DESCENT_IN_ZONE = [0.52, 0.58]
HYSTERESIS = [0.64, 0.66, 0.66]  # raise_zone_y_max < y <= raise_exit_y
BELOW_EXIT = [0.72, 0.76, 0.76]
RAISE_PATH = RISE + DESCENT_IN_ZONE + HYSTERESIS + BELOW_EXIT


def test_raise_constants_match_config():
    assert all(CFG.raise_zone_y_max < y <= CFG.raise_exit_y for y in HYSTERESIS)
    assert all(y > CFG.raise_exit_y for y in BELOW_EXIT)
    assert RISE[0] - min(RISE) >= CFG.raise_min_rise
    first_in_zone = next(i for i, y in enumerate(RISE) if y <= CFG.raise_zone_y_max)
    assert first_in_zone * FRAME_MS <= 1000.0 <= CFG.raise_window_ms


def test_raised_fast_rise_triggers_holds_with_hysteresis_and_releases(settings):
    obs = run(settings, path_script(RAISE_PATH), len(RAISE_PATH))
    trigger = next(i for i, y in enumerate(RAISE_PATH) if y <= CFG.raise_zone_y_max)

    assert states(obs, RAISED)[0] == ("unknown", "track_unconfirmed")
    for i in range(1, trigger):
        assert states(obs[i : i + 1], RAISED)[0] == ("absent", "track_below_raise_zone"), i

    r = sig(obs[trigger], RAISED)
    assert (r.state, r.reason) == (SignalState.PRESENT, "track_rose_into_raise_zone")
    assert r.track_id == "ph-1"
    assert r.confidence is not None
    assert r.facts["rise_ratio"] >= CFG.raise_min_rise
    assert r.facts["rise_ratio"] == pytest.approx(RAISE_PATH[0] - RAISE_PATH[trigger], abs=1e-3)
    assert r.facts["rise_observed"] is True
    assert r.facts["raised_ms"] == 0.0
    assert r.facts["y_center"] <= CFG.raise_zone_y_max

    release = len(RISE) + len(DESCENT_IN_ZONE) + len(HYSTERESIS)
    for i in range(trigger + 1, release):
        r = sig(obs[i], RAISED)
        assert (r.state, r.reason) == (SignalState.PRESENT, "track_held_in_raise_zone"), (i, RAISE_PATH[i])
        assert r.track_id == "ph-1"
        assert r.facts["rise_observed"] is True
        assert r.facts["rise_ratio"] >= CFG.raise_min_rise
        assert r.facts["raised_ms"] == pytest.approx((i - trigger) * FRAME_MS)

    # hysteresis band explicitly: between raise_zone_y_max and raise_exit_y it stays latched
    for i in range(len(RISE) + len(DESCENT_IN_ZONE), release):
        assert CFG.raise_zone_y_max < RAISE_PATH[i] <= CFG.raise_exit_y
        assert sig(obs[i], RAISED).state == SignalState.PRESENT

    for i in range(release, len(RAISE_PATH)):
        r = sig(obs[i], RAISED)
        assert (r.state, r.reason) == (SignalState.ABSENT, "track_below_raise_zone"), (i, RAISE_PATH[i])
        assert r.track_id == "ph-1"
    # phone stayed one confirmed track the whole time
    assert {d.track_id for o in obs for d in o.detections} == {"ph-1"}


def test_raised_hysteresis_band_does_not_trigger_before_latch(settings):
    # rises 0.85 -> 0.64 (>= raise_min_rise) but never reaches raise_zone_y_max: not raised
    path = [0.85, 0.80, 0.75, 0.70, 0.66, 0.64, 0.64, 0.64, 0.64]
    obs = run(settings, path_script(path), len(path))
    assert all(st != "present" for st, _ in states(obs, RAISED))
    last = sig(obs[-1], RAISED)
    assert (last.state, last.reason) == (SignalState.ABSENT, "track_below_raise_zone")
    assert last.facts["rise_ratio"] >= CFG.raise_min_rise  # the rise is reported as a fact, not as a signal


def _slow_drift(seconds: float = 6.0, start: float = 0.80, end: float = 0.50) -> list[float]:
    n = int(seconds * 1000 / FRAME_MS)
    return [start + (end - start) * k / n for k in range(n + 1)]


def test_raised_slow_drift_upward_does_not_trigger(settings):
    drift = _slow_drift()
    per_window = (drift[0] - drift[-1]) * CFG.raise_window_ms / ((len(drift) - 1) * FRAME_MS)
    assert per_window < CFG.raise_min_rise  # rise inside any raise window is too small
    assert (len(drift) - 1) * FRAME_MS > 3 * CFG.raise_window_ms
    path = drift + [drift[-1]] * 4  # plus a short hold (0.5 s)
    obs = run(settings, path_script(path), len(path))
    st = states(obs, RAISED)
    assert all(s != "present" for s, _ in st), [x for x in st if x[0] == "present"]
    # below the zone: absent; inside the zone after a too-slow rise: not counted (unknown, not absent)
    for i, y in enumerate(path[1:], start=1):
        s = sig(obs[i], RAISED)
        if y > CFG.raise_zone_y_max:
            assert (s.state, s.reason) == (SignalState.ABSENT, "track_below_raise_zone"), (i, y)
        else:
            assert (s.state, s.reason) == (SignalState.UNKNOWN, "rise_below_threshold"), (i, y)
            assert s.facts["rise_ratio"] < CFG.raise_min_rise


def test_raised_slow_drift_then_hold_is_not_reported_as_appeared_in_zone(settings):
    drift = _slow_drift()
    path = drift + [drift[-1]] * 32  # hold 4 s after the drift
    obs = run(settings, path_script(path), len(path))
    # The track was observed entering the zone from below, so by the signals.py docstring it never
    # "appeared in the zone"; whatever the state, that reason must not be emitted for this track.
    reasons = [sig(o, RAISED).reason for o in obs]
    assert "appeared_in_raise_zone" not in reasons


def test_raised_phone_appearing_inside_zone_needs_hold(settings):
    n = 12
    obs = run(settings, steady_script(0.5, 0.40, 0.10, 0.18), n)
    trigger = frame_index_at(CFG.raise_hold_ms)
    assert trigger >= 2
    assert states(obs, RAISED)[0] == ("unknown", "track_unconfirmed")
    for i in range(1, trigger):
        r = sig(obs[i], RAISED)
        assert (r.state, r.reason) == (SignalState.UNKNOWN, "insufficient_track_history"), i
        assert r.track_id == "ph-1"
        assert r.facts["hold_ms_required"] == CFG.raise_hold_ms
    r = sig(obs[trigger], RAISED)
    assert (r.state, r.reason) == (SignalState.PRESENT, "appeared_in_raise_zone")
    assert r.facts["rise_observed"] is False
    assert r.facts["track_age_ms"] >= CFG.raise_hold_ms
    for i in range(trigger + 1, n):
        r = sig(obs[i], RAISED)
        assert (r.state, r.reason) == (SignalState.PRESENT, "track_held_in_raise_zone"), i
        assert r.facts["rise_observed"] is False


def test_raised_phone_staying_low_is_absent_below_zone(settings):
    obs = run(settings, steady_script(0.5, 0.85, 0.10, 0.18), 16)
    assert states(obs, RAISED)[0] == ("unknown", "track_unconfirmed")
    for o in obs[1:]:
        r = sig(o, RAISED)
        assert (r.state, r.reason) == (SignalState.ABSENT, "track_below_raise_zone")
        assert r.track_id == "ph-1"
        assert r.facts["y_center"] > CFG.raise_zone_y_max


def test_raised_no_phone_is_absent_no_phone_in_view(settings):
    obs = run(settings, no_phone, 8)
    assert states(obs, RAISED) == [("absent", "no_phone_in_view")] * 8


def test_raised_latched_phone_that_leaves_view_coasts_then_no_phone(settings):
    hold = RISE  # rises and is latched, then the detector loses it
    n = len(hold) + 8
    obs = run(settings, path_script(hold), n)
    assert sig(obs[len(hold) - 1], RAISED).state == SignalState.PRESENT
    t_last = (len(hold) - 1) * FRAME_MS
    for i in range(len(hold), n):
        r = sig(obs[i], RAISED)
        if i * FRAME_MS - t_last <= CFG.coast_visible_ms:
            assert (r.state, r.reason) == (SignalState.PRESENT, "track_held_in_raise_zone"), i
        else:
            assert (r.state, r.reason) == (SignalState.ABSENT, "no_phone_in_view"), i


# --------------------------------------------------------------------------------------------
# possible_screen_capture
# --------------------------------------------------------------------------------------------
# large (area 0.0416 >= capture_min_area), centred, upper ("in front of the screen") phone
BIG = dict(w=0.16, h=0.26)


def test_capture_constants_match_config():
    assert BIG["w"] * BIG["h"] >= CFG.capture_min_area
    assert 0.06 * 0.10 < CFG.capture_min_area  # "small" phone below
    assert 0.40 <= CFG.capture_zone_y_max
    assert CFG.capture_zone_x_min <= 0.5 <= CFG.capture_zone_x_max
    assert 0.10 < CFG.capture_zone_x_min and 0.78 > CFG.capture_zone_y_max


def test_capture_steady_large_centered_phone_is_present_after_steady_ms(settings):
    n = 16
    obs = run(settings, steady_script(0.5, 0.40, **BIG), n)
    first = frame_index_at(CFG.capture_steady_ms)
    for i in range(n):
        c = sig(obs[i], CAPTURE)
        if i < first:
            assert (c.state, c.reason) == (SignalState.INSUFFICIENT_EVIDENCE, "camera_side_not_observable"), i
            assert c.facts == {"camera_direction_observable": False}
        else:
            assert (c.state, c.reason) == (SignalState.PRESENT, "steady_phone_in_front_of_screen_zone"), i
            f = c.facts
            assert f["camera_direction_observable"] is False
            assert f["mean_area"] >= CFG.capture_min_area
            assert f["center_motion"] <= CFG.capture_max_motion
            assert CFG.capture_zone_x_min <= f["x_center"] <= CFG.capture_zone_x_max
            assert f["y_center"] <= CFG.capture_zone_y_max
            assert f["hit_ratio"] >= CFG.capture_min_hit_ratio
            assert f["steady_ms"] >= CFG.capture_steady_ms - FRAME_MS
            assert c.track_id == "ph-1"
            # confidence is bounded by both the detector score and the track quality
            assert c.confidence <= f["mean_detector_confidence"] + 1e-9
            assert c.confidence <= f["track_quality"] + 1e-9


def test_capture_small_jitter_still_counts_as_steady(settings):
    jitter = [0.5 + (0.01 if i % 2 else -0.01) for i in range(16)]
    obs = run(settings, path_script([0.40 + (0.005 if i % 3 else 0.0) for i in range(16)], cx=jitter, **BIG), 16)
    assert sig(obs[-1], CAPTURE).state == SignalState.PRESENT
    assert sig(obs[-1], CAPTURE).facts["center_motion"] <= CFG.capture_max_motion


def _triangle(lo: float, hi: float, step: float, n: int) -> list[float]:
    out, x, d = [], lo, step
    for _ in range(n):
        out.append(round(x, 4))
        if not lo <= x + d <= hi:
            d = -d
        x += d
    return out


NEGATIVE_CAPTURE = {
    # name: (script, frames)
    "moving": (path_script([0.40] * 40, cx=_triangle(0.30, 0.70, 0.05, 40), **BIG), 40),
    "small": (steady_script(0.5, 0.40, w=0.06, h=0.10), 24),
    "side": (steady_script(0.10, 0.40, **BIG), 24),
    "low": (steady_script(0.5, 0.78, **BIG), 24),
    "flicker": (lambda i: [box(0.5, 0.40, **BIG)] if i % 2 == 0 else [], 24),
}


@pytest.mark.parametrize("scenario", sorted(NEGATIVE_CAPTURE))
def test_capture_non_matching_patterns_are_insufficient_evidence(settings, scenario):
    script, n = NEGATIVE_CAPTURE[scenario]
    obs = run(settings, script, n)
    for i, o in enumerate(obs):
        c = sig(o, CAPTURE)
        assert (c.state, c.reason) == (SignalState.INSUFFICIENT_EVIDENCE, "camera_side_not_observable"), (scenario, i)
        assert c.facts["camera_direction_observable"] is False
        assert c.confidence is None
    # the phone itself stayed visible: the absence of a capture pattern is not "no phone"
    assert sig(obs[-1], VISIBLE).state == SignalState.PRESENT or scenario == "flicker"


def test_capture_moving_phone_is_one_track(settings):
    script, n = NEGATIVE_CAPTURE["moving"]
    obs = run(settings, script, n)
    assert {d.track_id for o in obs for d in o.detections} == {"ph-1"}  # motion, not a track break


def test_capture_no_phone_is_insufficient_evidence_phone_not_in_view(settings):
    obs = run(settings, no_phone, 10)
    for o in obs:
        c = sig(o, CAPTURE)
        assert (c.state, c.reason) == (SignalState.INSUFFICIENT_EVIDENCE, "phone_not_in_view")
        assert c.facts == {"camera_direction_observable": False}


def test_capture_after_phone_leaves_goes_to_not_in_view_never_absent(settings):
    frames = 12
    n = frames + 10
    obs = run(settings, steady_script(0.5, 0.40, frames=frames, **BIG), n)
    assert sig(obs[frames - 1], CAPTURE).state == SignalState.PRESENT
    t_last = (frames - 1) * FRAME_MS
    for i in range(frames, n):
        c = sig(obs[i], CAPTURE)
        assert c.state in (SignalState.PRESENT, SignalState.INSUFFICIENT_EVIDENCE), i
        if i * FRAME_MS - t_last > CFG.coast_visible_ms:
            assert (c.state, c.reason) == (SignalState.INSUFFICIENT_EVIDENCE, "phone_not_in_view"), i


# --------------------------------------------------------------------------------------------
# cross-scenario invariants
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Scenario:
    script: Script
    frames: int
    images: Callable[[int], object] | None = None


SCENARIOS: dict[str, Scenario] = {
    "no_phone": Scenario(no_phone, 10),
    "black_frame": Scenario(no_phone, 6, lambda i: BLACK),
    "black_frame_with_coasting_phone": Scenario(steady_script(0.5, 0.40, frames=4, **BIG), 10, lambda i: BLACK if i >= 4 else IMG),
    "single_low_conf_blip": Scenario(lambda i: [box(0.5, 0.5, conf=0.3)] if i == 0 else [], 4),
    "high_conf_single": Scenario(lambda i: [box(0.5, 0.5, conf=0.9)] if i == 0 else [], 4),
    "coasting": Scenario(lambda i: [box(0.5, 0.5)] if i < 4 else [], 12),
    "fast_rise_and_release": Scenario(path_script(RAISE_PATH), len(RAISE_PATH)),
    "slow_drift": Scenario(path_script(_slow_drift() + [0.5] * 36), len(_slow_drift()) + 36),
    "appear_in_zone": Scenario(steady_script(0.5, 0.40, 0.10, 0.18), 12),
    "low_phone": Scenario(steady_script(0.5, 0.85, 0.10, 0.18), 12),
    "steady_capture_then_leave": Scenario(steady_script(0.5, 0.40, frames=12, **BIG), 22),
    "two_phones": Scenario(lambda i: [box(0.3, 0.4, **BIG), box(0.75, 0.8, conf=0.4)], 16),
    **{f"capture_{k}": Scenario(s, n) for k, (s, n) in NEGATIVE_CAPTURE.items()},
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_scenario_validates_and_never_claims_a_photo(settings, name):
    sc = SCENARIOS[name]
    obs = run(settings, sc.script, sc.frames, images=sc.images)  # run() applies check_observation per frame
    assert len(obs) == sc.frames


def test_possible_screen_capture_is_never_absent_across_all_scenarios(settings):
    seen: dict[str, set[str]] = {}
    for name, sc in SCENARIOS.items():
        for o in run(settings, sc.script, sc.frames, images=sc.images):
            c = sig(o, CAPTURE)
            seen.setdefault(c.state.value, set()).add(name)
            assert c.state != SignalState.ABSENT, name
    assert "absent" not in seen
    # the scenarios really exercised every reachable state of the signal
    assert {"present", "insufficient_evidence", "unknown"} <= set(seen)


def test_phone_visible_and_raised_reach_unknown_and_absent_distinctly(settings):
    """unknown != absent: an unusable frame never reads as 'no phone'."""
    black = run(settings, no_phone, 2, images=lambda i: BLACK)
    empty = run(settings, no_phone, 2)
    for name in (VISIBLE, RAISED):
        assert {sig(o, name).state for o in black} == {SignalState.UNKNOWN}
        assert {sig(o, name).state for o in empty} == {SignalState.ABSENT}
