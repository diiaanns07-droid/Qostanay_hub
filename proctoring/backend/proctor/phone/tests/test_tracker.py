"""PhoneTracker / Track tests (owner: A03).

FAKE BOXES ONLY. Every detection in this file is a hand-made RawDetection (helpers.box) fed straight
into the tracker (or through PhoneAnalyzer with an injected FakeDetector) with t in milliseconds of
t_session_ms, 125 ms per frame (8 fps). These tests check association, the track-id lifecycle,
track_quality arithmetic, time windows and determinism -- they say NOTHING about how well YOLO11n finds
phones (CV accuracy needs the real model and measured clips). A tracked phone box is a detector output,
never evidence that anything was photographed.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import replace

import pytest

from proctor.phone import PhoneAnalyzer
from proctor.phone.config import PhoneConfig
from proctor.phone.detector import RawDetection
from proctor.phone.tests.helpers import FRAME_MS, SESSION, FakeDetector, box, make_frame
from proctor.phone.tracker import PhoneTracker, Track, TrackPoint, box_iou
from proctor_contracts.v1 import BBox, HealthStatus, PhoneDetection, PhoneObservation, SourceMode

CFG = PhoneConfig()
TRACK_ID_RE = re.compile(r"^ph-[1-9][0-9]*$")


def ids(assigned: list[Track | None]) -> list[str | None]:
    return [tr.track_id if tr is not None else None for tr in assigned]


def live_ids(tracker: PhoneTracker) -> list[str]:
    return sorted((tr.track_id for tr in tracker.tracks), key=lambda s: int(s.split("-")[1]))


def t_of(frame_index: int) -> float:
    return frame_index * FRAME_MS


# ----------------------------------------------------------------------------------------- box_iou
def test_box_iou_identical_disjoint_partial_symmetric_and_degenerate():
    a = box(0.5, 0.5, 0.10, 0.20)
    assert box_iou(a, a) == pytest.approx(1.0)
    assert box_iou(a, box(0.8, 0.5, 0.10, 0.20)) == 0.0
    shifted = box(0.55, 0.5, 0.10, 0.20)  # half a width to the right: inter 0.05*0.2, union 0.02+0.02-0.01
    assert box_iou(a, shifted) == pytest.approx(0.01 / 0.03)
    assert box_iou(shifted, a) == pytest.approx(box_iou(a, shifted))
    touching = box(0.60, 0.5, 0.10, 0.20)  # shares only an edge (helper arithmetic leaves ~1e-16)
    assert box_iou(a, touching) == pytest.approx(0.0, abs=1e-9)
    zero = RawDetection(0.3, 0.3, 0.3, 0.3, 0.5, 67, "cell phone")
    assert box_iou(zero, zero) == 0.0


# ------------------------------------------------------------------------------------ basic update
def test_update_returns_one_entry_per_detection_and_empty_frame_returns_empty_list():
    tracker = PhoneTracker(CFG)
    assert tracker.tracks == [] and tracker.created == 0 and tracker.evicted == 0
    assert tracker.update([], 0.0) == []
    assigned = tracker.update([box(0.2, 0.3), box(0.7, 0.3)], FRAME_MS)
    assert len(assigned) == 2 and all(isinstance(tr, Track) for tr in assigned)
    assert ids(assigned) == ["ph-1", "ph-2"]  # new tracks numbered in detection order
    assert tracker.update([], 2 * FRAME_MS) == []
    assert live_ids(tracker) == ["ph-1", "ph-2"]  # a miss alone does not delete a track


def test_single_phone_moving_smoothly_keeps_one_id():
    tracker = PhoneTracker(CFG)
    n = 24  # 3 s
    last = None
    for i in range(n):
        last = box(0.30 + 0.012 * i, 0.55 - 0.008 * i)
        assert ids(tracker.update([last], t_of(i))) == ["ph-1"], f"frame {i}"
    assert tracker.created == 1 and tracker.evicted == 0
    (tr,) = tracker.tracks
    assert tr.track_id == "ph-1"
    assert tr.hits == n
    assert tr.det == last
    assert tr.first_t == 0.0 and tr.last_det_t == t_of(n - 1)
    assert tr.age_ms(t_of(n - 1)) == pytest.approx(t_of(n - 1))
    assert tr.ms_since_detection(t_of(n - 1)) == 0.0


@pytest.mark.parametrize("confirm_hits", [1, 2, 4])
def test_track_confirmed_exactly_after_track_confirm_hits(confirm_hits):
    cfg = replace(CFG, track_confirm_hits=confirm_hits)
    tracker = PhoneTracker(cfg)
    for i in range(confirm_hits + 2):
        (tr,) = tracker.update([box(0.5, 0.4)], t_of(i))
        assert tr.hits == i + 1
        assert tr.confirmed(cfg) is (i + 1 >= confirm_hits), f"hit {i + 1}"


def test_misses_do_not_count_towards_confirmation():
    tracker = PhoneTracker(CFG)  # track_confirm_hits = 2
    (tr,) = tracker.update([box(0.5, 0.4)], 0.0)
    tracker.update([], t_of(1))
    tracker.update([], t_of(2))
    assert tr.hits == 1 and not tr.confirmed(CFG)
    (again,) = tracker.update([box(0.5, 0.4)], t_of(3))
    assert again is tr and tr.hits == 2 and tr.confirmed(CFG)


# ---------------------------------------------------------------------------------- id lifecycle
def test_track_ids_are_sequential_never_reused_after_deletion_and_restart_after_reset():
    tracker = PhoneTracker(CFG)
    seen = []
    t = 0.0
    for _ in range(3):
        (tr,) = tracker.update([box(0.5, 0.4)], t)  # same place every time ...
        seen.append(tr.track_id)
        assert live_ids(tracker) == [tr.track_id]  # ... but the previous track expired before this update
        t += CFG.track_max_miss_ms + FRAME_MS
    assert seen == ["ph-1", "ph-2", "ph-3"]
    assert tracker.created == 3 and tracker.evicted == 0

    tracker.reset()
    assert tracker.tracks == [] and tracker.created == 0 and tracker.evicted == 0
    assert ids(tracker.update([box(0.5, 0.4), box(0.1, 0.8)], t)) == ["ph-1", "ph-2"]
    assert tracker.created == 2


@pytest.mark.parametrize("missed_frames", [1, 2, 4])  # gap since last detection: 250, 375, 625 ms
def test_short_dropout_keeps_track_id(missed_frames):
    tracker = PhoneTracker(CFG)
    tracker.update([box(0.5, 0.4)], t_of(0))
    (tr,) = tracker.update([box(0.5, 0.4)], t_of(1))
    for k in range(missed_frames):
        t = t_of(2 + k)
        assert tracker.update([], t) == []
        assert live_ids(tracker) == ["ph-1"]
        assert tr.ms_since_detection(t) == pytest.approx(t - t_of(1))
    t_back = t_of(2 + missed_frames)
    assert t_back - t_of(1) < CFG.track_max_miss_ms
    (again,) = tracker.update([box(0.505, 0.4)], t_back)
    assert again is tr and again.track_id == "ph-1"
    assert tr.hits == 3 and tracker.created == 1
    assert tr.age_ms(t_back) == pytest.approx(t_back)


@pytest.mark.parametrize("missed_frames", [5, 8])  # gap since last detection: 750, 1125 ms
def test_long_dropout_deletes_track_and_creates_new_id(missed_frames):
    tracker = PhoneTracker(CFG)
    tracker.update([box(0.5, 0.4)], t_of(0))
    (old,) = tracker.update([box(0.5, 0.4)], t_of(1))
    for k in range(missed_frames):
        tracker.update([], t_of(2 + k))
    t_back = t_of(2 + missed_frames)
    assert t_back - t_of(1) > CFG.track_max_miss_ms
    (new,) = tracker.update([box(0.5, 0.4)], t_back)
    assert new is not old and new.track_id == "ph-2"
    assert live_ids(tracker) == ["ph-2"]
    assert new.hits == 1 and not new.confirmed(CFG)
    assert tracker.created == 2 and tracker.evicted == 0  # expiry is not an eviction


def test_dropout_boundary_is_inclusive_of_track_max_miss_ms():
    """Docstring: a track survives dropouts FOR track_max_miss_ms; deleted only after that."""
    tracker = PhoneTracker(CFG)
    tracker.update([box(0.5, 0.4)], 0.0)
    assert ids(tracker.update([box(0.5, 0.4)], CFG.track_max_miss_ms)) == ["ph-1"]
    assert ids(tracker.update([box(0.5, 0.4)], 2 * CFG.track_max_miss_ms + 0.5)) == ["ph-2"]


def test_track_max_miss_ms_override_is_respected():
    cfg = replace(CFG, track_max_miss_ms=300.0)
    tracker = PhoneTracker(cfg)
    tracker.update([box(0.5, 0.4)], t_of(0))
    tracker.update([], t_of(1))
    tracker.update([], t_of(2))
    # 375 ms gap: kept with the default 700 ms, deleted with 300 ms
    assert ids(tracker.update([box(0.5, 0.4)], t_of(3))) == ["ph-2"]


# ----------------------------------------------------------------------------- multiple phones
def test_two_far_apart_phones_get_two_ids_and_keep_them_in_parallel_motion():
    tracker = PhoneTracker(CFG)
    for i in range(16):
        a = box(0.20 + 0.01 * i, 0.60 - 0.012 * i)
        b = box(0.70 + 0.01 * i, 0.60 - 0.012 * i)
        order = ("a", "b") if i % 2 == 0 else ("b", "a")  # input order must not matter
        dets = [a if name == "a" else b for name in order]
        got = dict(zip(order, ids(tracker.update(dets, t_of(i)))))
        assert got == {"a": "ph-1", "b": "ph-2"}, f"frame {i}"
    assert tracker.created == 2 and live_ids(tracker) == ["ph-1", "ph-2"]
    tr_a, tr_b = sorted(tracker.tracks, key=lambda tr: tr.track_id)
    assert tr_a.hits == tr_b.hits == 16
    # identical motion -> identical association quality
    assert tr_a.quality(CFG) == tr_b.quality(CFG)


def test_one_detection_goes_to_the_best_overlapping_track_other_track_misses():
    tracker = PhoneTracker(CFG)
    tracker.update([box(0.30, 0.4), box(0.62, 0.4)], 0.0)  # ph-1 left, ph-2 right
    (tr,) = tracker.update([box(0.33, 0.4)], FRAME_MS)
    assert tr.track_id == "ph-1"
    right = next(t for t in tracker.tracks if t.track_id == "ph-2")
    assert right.hits == 1 and right.updates[-1] == (FRAME_MS, False)


def test_two_detections_on_one_track_best_iou_wins_and_other_starts_new_track():
    tracker = PhoneTracker(CFG)
    tracker.update([box(0.5, 0.4)], 0.0)
    near = box(0.53, 0.4)  # IoU ~0.54 with the track
    exact = box(0.5, 0.4)  # IoU 1.0 with the track
    assert ids(tracker.update([near, exact], FRAME_MS)) == ["ph-2", "ph-1"]


# ---------------------------------------------------------------------------- fast motion / gate
@pytest.mark.parametrize("dx,dy", [(0.15, 0.0), (0.0, 0.17), (0.12, 0.12)])
def test_fast_motion_with_low_iou_inside_center_gate_keeps_id(dx, dy):
    tracker = PhoneTracker(CFG)
    a, b = box(0.3, 0.3), box(0.3 + dx, 0.3 + dy)
    assert box_iou(a, b) < CFG.track_iou_min
    assert math.hypot(dx, dy) < CFG.track_center_gate
    tracker.update([a], 0.0)
    (tr,) = tracker.update([b], FRAME_MS)
    assert tr.track_id == "ph-1" and tr.hits == 2 and tracker.created == 1


def test_sustained_zigzag_fast_motion_keeps_one_id():
    tracker = PhoneTracker(CFG)
    for i in range(12):
        cx = 0.35 if i % 2 == 0 else 0.50  # 0.15 jump each frame, no overlap at all
        assert ids(tracker.update([box(cx, 0.4)], t_of(i))) == ["ph-1"], f"frame {i}"
    assert tracker.created == 1


@pytest.mark.parametrize("dx,dy", [(0.20, 0.0), (0.0, 0.25), (0.13, 0.13)])
def test_motion_beyond_center_gate_starts_new_track(dx, dy):
    tracker = PhoneTracker(CFG)
    a, b = box(0.3, 0.3), box(0.3 + dx, 0.3 + dy)
    assert box_iou(a, b) < CFG.track_iou_min
    assert math.hypot(dx, dy) > CFG.track_center_gate
    tracker.update([a], 0.0)
    (tr,) = tracker.update([b], FRAME_MS)
    assert tr.track_id == "ph-2"
    assert live_ids(tracker) == ["ph-1", "ph-2"]  # the old track is kept (missed) until it expires
    old = next(t for t in tracker.tracks if t.track_id == "ph-1")
    assert old.hits == 1 and old.updates[-1] == (FRAME_MS, False)


def test_center_gate_override_is_respected():
    cfg = replace(CFG, track_center_gate=0.25)
    tracker = PhoneTracker(cfg)
    tracker.update([box(0.3, 0.3)], 0.0)
    assert ids(tracker.update([box(0.5, 0.3)], FRAME_MS)) == ["ph-1"]  # 0.20 jump: new id with the default gate


# --------------------------------------------------------------------------------- size ratio
@pytest.mark.parametrize(
    "w,h,dx,same_track",
    [
        (0.16, 0.288, 0.13, True),  # 2.56x the area, no overlap, inside the gate
        (0.20, 0.360, 0.16, False),  # 4x the area
        (0.05, 0.090, 0.10, False),  # 0.25x the area
    ],
)
def test_size_ratio_above_3x_prevents_center_gated_association(w, h, dx, same_track):
    tracker = PhoneTracker(CFG)
    a = box(0.5, 0.5)  # 0.10 x 0.18
    b = box(0.5 + dx, 0.5, w, h)
    ratio = b.area / a.area
    assert box_iou(a, b) < CFG.track_iou_min
    assert dx < CFG.track_center_gate
    assert (1 / 3 <= ratio <= 3) is same_track
    tracker.update([a], 0.0)
    (tr,) = tracker.update([b], FRAME_MS)
    assert tr.track_id == ("ph-1" if same_track else "ph-2")


def test_size_ratio_blocks_gate_even_with_identical_centers():
    tracker = PhoneTracker(CFG)
    a = box(0.5, 0.5)
    huge = box(0.5, 0.5, 0.30, 0.54)  # 9x the area, concentric: IoU 1/9 < track_iou_min, distance 0
    assert box_iou(a, huge) < CFG.track_iou_min
    tracker.update([a], 0.0)
    assert ids(tracker.update([huge], FRAME_MS)) == ["ph-2"]


def test_iou_association_is_not_subject_to_size_ratio():
    """Docstring: IoU first; the 3x size limit applies only to the center-distance gate."""
    tracker = PhoneTracker(CFG)
    a = box(0.5, 0.5)
    big = box(0.5, 0.5, 0.20, 0.36)  # 4x the area, concentric: IoU 0.25 >= track_iou_min
    assert box_iou(a, big) >= CFG.track_iou_min
    tracker.update([a], 0.0)
    assert ids(tracker.update([big], FRAME_MS)) == ["ph-1"]


# --------------------------------------------------------------------------------- max_tracks
GRID = [(x, y) for y in (0.25, 0.75) for x in (0.1, 0.3, 0.5, 0.7, 0.9)]  # 10 far-apart phones


def test_more_detections_than_max_tracks_leaves_extras_untracked_when_all_tracks_matched():
    assert CFG.max_tracks == 8
    tracker = PhoneTracker(CFG)
    dets = [box(x, y) for x, y in GRID]
    first = ids(tracker.update(dets, 0.0))
    assert first == [f"ph-{i}" for i in range(1, 9)] + [None, None]
    second = ids(tracker.update(dets, FRAME_MS))  # every track is matched again in this frame
    assert second == first
    assert len(tracker.tracks) == 8 and tracker.created == 8 and tracker.evicted == 0

    # The two untracked phones alone: all 8 tracks are idle now -> two are evicted for the new ones.
    third = ids(tracker.update(dets[8:], 2 * FRAME_MS))
    assert third == ["ph-9", "ph-10"]
    assert tracker.evicted == 2 and tracker.created == 10 and len(tracker.tracks) == 8
    live = live_ids(tracker)
    assert "ph-9" in live and "ph-10" in live
    assert len({f"ph-{i}" for i in range(1, 9)} - set(live)) == 2


def test_max_tracks_one_extra_detection_in_same_frame_is_untracked():
    cfg = replace(CFG, max_tracks=1)
    tracker = PhoneTracker(cfg)
    assert ids(tracker.update([box(0.2, 0.4), box(0.8, 0.4)], 0.0)) == ["ph-1", None]
    assert tracker.created == 1 and tracker.evicted == 0


def test_eviction_picks_oldest_last_detection_and_ids_are_not_reused():
    cfg = replace(CFG, max_tracks=3)
    tracker = PhoneTracker(cfg)
    a, b, c = box(0.1, 0.2), box(0.5, 0.2), box(0.9, 0.2)
    d, e = box(0.5, 0.8), box(0.1, 0.8)
    assert ids(tracker.update([a, b, c], t_of(0))) == ["ph-1", "ph-2", "ph-3"]
    tracker.update([a, b], t_of(1))  # C (ph-3) last seen at t=0
    assert ids(tracker.update([a, d], t_of(2))) == ["ph-1", "ph-4"]  # B last seen 125, C last seen 0
    assert live_ids(tracker) == ["ph-1", "ph-2", "ph-4"] and tracker.evicted == 1
    assert ids(tracker.update([a, e], t_of(3))) == ["ph-1", "ph-5"]  # B (125) older than D (250)
    assert live_ids(tracker) == ["ph-1", "ph-4", "ph-5"] and tracker.evicted == 2
    # The evicted phone C comes back: it is a NEW track, its old id is not reused.
    assert ids(tracker.update([a, c], t_of(4))) == ["ph-1", "ph-6"]


def test_eviction_tie_on_last_detection_prefers_track_with_fewer_hits():
    cfg = replace(CFG, max_tracks=2)
    tracker = PhoneTracker(cfg)
    a, b, c = box(0.2, 0.3), box(0.8, 0.3), box(0.5, 0.8)
    tracker.update([a], t_of(0))
    tracker.update([a, b], t_of(1))  # ph-1: 2 hits, ph-2: 1 hit, both last seen at 125
    assert ids(tracker.update([c], t_of(2))) == ["ph-3"]
    assert live_ids(tracker) == ["ph-1", "ph-3"] and tracker.evicted == 1


# ------------------------------------------------------------------------------- track_quality
@pytest.mark.parametrize("confirm_hits", [2, 3, 5])
def test_brand_new_track_quality_is_low(confirm_hits):
    cfg = replace(CFG, track_confirm_hits=confirm_hits)
    (tr,) = PhoneTracker(cfg).update([box(0.5, 0.4, conf=0.99)], 0.0)
    q = tr.quality(cfg)
    assert 0.0 < q <= 0.25
    if confirm_hits == CFG.track_confirm_hits:
        assert q == 0.25  # hit_ratio 1 * (0.5 + 0.5*0) * 1/2


def test_quality_increases_with_stable_iou_matches_and_stays_in_unit_interval():
    tracker = PhoneTracker(CFG)
    qs = []
    for i in range(14):
        (tr,) = tracker.update([box(0.5, 0.4)], t_of(i))  # IoU 1.0 every frame
        qs.append(tr.quality(CFG))
    assert all(0.0 <= q <= 1.0 for q in qs)
    assert qs[0] <= 0.25
    assert all(b > a for a, b in zip(qs[:6], qs[1:7])), qs  # strictly increasing early on
    assert all(b >= a for a, b in zip(qs, qs[1:])), qs  # never decreasing (rounding may plateau at 1.0)
    assert qs[-1] >= 0.99  # approaches the hit ratio (1.0) after stable matches


def test_quality_decreases_with_misses_and_recovers_on_reacquisition():
    tracker = PhoneTracker(CFG)
    for i in range(6):
        (tr,) = tracker.update([box(0.5, 0.4)], t_of(i))
    before = tr.quality(CFG)
    qs = [before]
    for k in range(4):  # 500 ms of misses (< track_max_miss_ms: the track survives)
        tracker.update([], t_of(6 + k))
        qs.append(tr.quality(CFG))
    assert all(b < a for a, b in zip(qs, qs[1:])), qs
    assert all(0.0 <= q <= 1.0 for q in qs)
    (again,) = tracker.update([box(0.5, 0.4)], t_of(10))
    assert again is tr and tr.quality(CFG) > qs[-1]


def test_low_iou_gated_association_yields_lower_quality_than_stable_track():
    stable, fast = PhoneTracker(CFG), PhoneTracker(CFG)
    for i in range(10):
        (s,) = stable.update([box(0.5, 0.4)], t_of(i))
        (f,) = fast.update([box(0.35 if i % 2 == 0 else 0.50, 0.4)], t_of(i))  # IoU 0, center gate
    assert s.track_id == f.track_id == "ph-1"
    assert s.hits == f.hits == 10
    assert f.quality(CFG) <= 0.5 < s.quality(CFG)


def test_quality_is_independent_of_detection_confidence():
    def run(conf: float) -> list[tuple[str | None, float | None]]:
        tracker = PhoneTracker(CFG)
        out = []
        for i in range(16):
            dets = [] if i in (5, 6, 11) else [box(0.4 + 0.01 * i, 0.5 - 0.005 * i, conf=conf)]
            for tr in tracker.update(dets, t_of(i)):
                out.append((tr.track_id, tr.quality(CFG)))
            out.append(("live", tuple((t.track_id, t.quality(CFG)) for t in tracker.tracks)))
        return out

    low, high = run(0.21), run(0.97)
    assert low == high


def test_old_misses_drop_out_of_track_recent_len_window():
    cfg = replace(CFG, track_recent_len=4)
    with_miss, without = PhoneTracker(cfg), PhoneTracker(cfg)
    pattern = [True, True, True, False, True, True, True, True]  # 7 hits, one miss
    for i, hit in enumerate(pattern):
        with_miss.update([box(0.5, 0.4)] if hit else [], t_of(i))
    for i in range(7):  # 7 hits, no miss
        without.update([box(0.5, 0.4)], t_of(i))
    (x,), (y,) = with_miss.tracks, without.tracks
    assert x.hits == y.hits == 7
    assert len(x.recent) == len(y.recent) == 4
    # the miss is older than the last 4 updates: it no longer lowers track_quality
    assert x.quality(cfg) == y.quality(cfg)


# ------------------------------------------------------------------------ time windows / history
def _hit_miss_track() -> tuple[PhoneTracker, Track, RawDetection]:
    """Hits at 0,125,250, misses at 375,500, hit at 625."""
    tracker = PhoneTracker(CFG)
    det = box(0.5, 0.4, conf=0.42)
    for i, hit in enumerate([True, True, True, False, False, True]):
        tracker.update([det] if hit else [], t_of(i))
    (tr,) = tracker.tracks
    return tracker, tr, det


def test_hit_ratio_respects_time_window():
    _, tr, _ = _hit_miss_track()
    t = t_of(5)  # 625
    assert tr.hit_ratio(t, 1000.0) == pytest.approx(4 / 6)
    assert tr.hit_ratio(t, 375.0) == pytest.approx(2 / 4)  # boundary inclusive: 250, 375, 500, 625
    assert tr.hit_ratio(t, 300.0) == pytest.approx(1 / 3)  # 375(miss), 500(miss), 625(hit)
    assert tr.hit_ratio(t, 0.0) == 1.0
    assert tr.hit_ratio(t + 10_000.0, 100.0) == 0.0  # no update inside the window


def test_window_points_contain_only_detected_positions_inside_window():
    _, tr, det = _hit_miss_track()
    t = t_of(5)
    assert [p.t for p in tr.window_points(t, 1000.0)] == [0.0, 125.0, 250.0, 625.0]
    assert [p.t for p in tr.window_points(t, 375.0)] == [250.0, 625.0]  # boundary inclusive
    assert [p.t for p in tr.window_points(t, 300.0)] == [625.0]
    assert tr.window_points(t + 10_000.0, 100.0) == []
    cx, cy = det.center
    assert tr.points[-1] == TrackPoint(625.0, cx, cy, det.area, det.confidence)
    assert tr.ms_since_detection(t) == 0.0
    assert tr.ms_since_detection(t + 375.0) == pytest.approx(375.0)
    assert tr.ms_since_detection(t - 125.0) == 0.0  # clamped, never negative
    assert tr.age_ms(t) == pytest.approx(625.0)


@pytest.mark.parametrize("history_ms", [CFG.track_history_ms, 1500.0])
def test_history_is_trimmed_to_track_history_ms(history_ms):
    cfg = replace(CFG, track_history_ms=history_ms)
    tracker = PhoneTracker(cfg)
    n = 48  # 6 s
    for i in range(n):
        (tr,) = tracker.update([box(0.5 + 0.002 * (i % 3), 0.4)], t_of(i))
    t_last = t_of(n - 1)
    expected = int(history_ms // FRAME_MS) + 1
    assert len(tr.points) == len(tr.updates) == expected
    assert all(p.t >= t_last - history_ms for p in tr.points)
    assert all(ut >= t_last - history_ms for ut, _ in tr.updates)
    assert tr.points[-1].t == t_last
    assert len(tr.recent) == cfg.track_recent_len
    assert tr.hits == n  # the hit counter itself is not trimmed
    # asking for a longer window cannot reach past the kept history
    assert tr.window_points(t_last, 10 * history_ms) == list(tr.points)
    assert tr.hit_ratio(t_last, 10 * history_ms) == 1.0


def test_points_are_trimmed_by_time_even_while_the_track_only_misses():
    cfg = replace(CFG, track_history_ms=1500.0, track_max_miss_ms=5000.0)
    tracker = PhoneTracker(cfg)
    for i in range(4):
        (tr,) = tracker.update([box(0.5, 0.4)], t_of(i))
    for i in range(4, 20):  # 2 s of misses; the long max_miss keeps the track alive
        tracker.update([], t_of(i))
    t = t_of(19)
    assert tracker.tracks == [tr]
    assert all(p.t >= t - 1500.0 for p in tr.points)
    assert tr.points == type(tr.points)()  # every detected point is older than the history
    assert all(ut >= t - 1500.0 for ut, _ in tr.updates)
    assert tr.hit_ratio(t, 1500.0) == 0.0


# ------------------------------------------------------------------------------- determinism
def _scenario(seed: int, n_frames: int = 96) -> list[list[RawDetection]]:
    rng = random.Random(seed)
    phones = [[0.25, 0.35], [0.70, 0.40], [0.50, 0.78]]
    frames = []
    for i in range(n_frames):
        dets = []
        for p in phones:
            p[0] = min(0.9, max(0.1, p[0] + rng.uniform(-0.03, 0.03)))
            p[1] = min(0.9, max(0.1, p[1] + rng.uniform(-0.03, 0.03)))
            if i % 37 < 30 and rng.random() < 0.8:  # random dropouts + a long gap every 37 frames
                dets.append(box(p[0], p[1], conf=round(rng.uniform(0.2, 0.95), 3)))
        if rng.random() < 0.15:  # small clutter boxes
            dets.append(box(rng.uniform(0.05, 0.95), rng.uniform(0.05, 0.95), 0.05, 0.08, conf=0.21))
        rng.shuffle(dets)
        frames.append(dets)
    return frames


def _trace(tracker: PhoneTracker, frames: list[list[RawDetection]]) -> list[tuple]:
    out: list[tuple] = []
    for i, dets in enumerate(frames):
        t = t_of(i)
        assigned = tracker.update(list(dets), t)
        out.append(tuple(None if tr is None else (tr.track_id, tr.quality(tracker.cfg), tr.hits) for tr in assigned))
        out.append(tuple((tr.track_id, tr.quality(tracker.cfg), tr.hits, tr.last_det_t) for tr in tracker.tracks))
    out.append(("counters", tracker.created, tracker.evicted))
    return out


def test_identical_input_sequences_give_identical_ids_and_qualities():
    frames = _scenario(7)
    first = _trace(PhoneTracker(CFG), frames)
    second = _trace(PhoneTracker(CFG), frames)
    assert first == second
    reused = PhoneTracker(CFG)
    _trace(reused, _scenario(99))
    reused.reset()
    assert _trace(reused, frames) == first  # reset() restores a clean, identical replay


def test_random_scenario_ids_are_unique_in_time_and_contract_compatible():
    tracker = PhoneTracker(CFG)
    gone: set[str] = set()
    max_seen = 0
    for i, dets in enumerate(_scenario(3)):
        t = t_of(i)
        before = {tr.track_id for tr in tracker.tracks}
        assigned = tracker.update(list(dets), t)
        after = {tr.track_id for tr in tracker.tracks}
        gone |= before - after
        assert not (after & gone), f"frame {i}: an id came back after deletion"
        assert len(tracker.tracks) <= CFG.max_tracks
        for det, tr in zip(dets, assigned):
            if tr is None:
                continue
            assert TRACK_ID_RE.match(tr.track_id)
            max_seen = max(max_seen, int(tr.track_id.split("-")[1]))
            q = tr.quality(CFG)
            assert 0.0 <= q <= 1.0
            PhoneDetection.model_validate(
                PhoneDetection(
                    bbox=BBox(x_min=det.x_min, y_min=det.y_min, x_max=det.x_max, y_max=det.y_max),
                    confidence=det.confidence,
                    class_name=det.class_name,
                    class_index=det.class_index,
                    track_id=tr.track_id,
                    track_quality=q,
                    track_age_ms=tr.age_ms(t),
                ).model_dump(mode="json")
            )
    assert max_seen == tracker.created
    assert gone  # the scenario does exercise deletion


# --------------------------------------------------- through PhoneAnalyzer (fake detector, logic only)
def test_analyzer_observations_carry_tracker_ids_and_quality_and_reset_per_session(settings):
    """FakeDetector: checks how tracks reach PhoneDetection, not CV accuracy."""
    plan = {
        0: [box(0.50, 0.4)],
        1: [box(0.51, 0.4)],
        2: [box(0.52, 0.4)],
        # 3: miss (250 ms gap before frame 4 -> same track)
        4: [box(0.53, 0.4)],
        # 5..10: misses (875 ms gap before frame 11 -> new track)
        11: [box(0.53, 0.4)],
        12: [box(0.53, 0.4)],  # first frame of the second session
    }
    analyzer = PhoneAnalyzer(settings, PhoneConfig(), detector=FakeDetector(script=plan))
    assert analyzer.load().status == HealthStatus.OK
    analyzer.start_session(SESSION, SourceMode.REPLAY)
    seen: dict[int, list[PhoneDetection]] = {}
    for i in range(12):
        (obs,) = analyzer.process(make_frame(i))
        PhoneObservation.model_validate(obs.model_dump(mode="json"))
        seen[i] = list(obs.detections)
    assert [d.track_id for d in seen[0]] == ["ph-1"]
    assert seen[0][0].track_quality == 0.25 and seen[0][0].track_age_ms == 0.0
    assert [d.track_id for i in (1, 2, 4) for d in seen[i]] == ["ph-1"] * 3
    assert seen[4][0].track_age_ms == pytest.approx(500.0)
    assert all(seen[i] == [] for i in (3, 5, 6, 7, 8, 9, 10))
    assert [d.track_id for d in seen[11]] == ["ph-2"]
    assert seen[11][0].track_quality == 0.25  # brand-new track again
    # detector confidence is reported separately from association quality
    assert seen[2][0].confidence == pytest.approx(0.6) and seen[2][0].track_quality != seen[2][0].confidence

    analyzer.start_session("s-phone-test-2", SourceMode.REPLAY)
    (obs,) = analyzer.process(make_frame(0, session_id="s-phone-test-2"))
    PhoneObservation.model_validate(obs.model_dump(mode="json"))
    assert [d.track_id for d in obs.detections] == ["ph-1"]  # ids restart per session
    analyzer.close()
