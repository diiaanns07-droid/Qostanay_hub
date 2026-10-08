"""Primary face selection: geometry/time only, ambiguity -> no guess."""

from __future__ import annotations

from proctor.attention.tracking import PrimaryFaceTracker, iou

BIG_CENTER = (0.35, 0.2, 0.65, 0.7)
SMALL_CORNER = (0.8, 0.05, 0.92, 0.25)
LEFT_EQ = (0.05, 0.25, 0.35, 0.75)
RIGHT_EQ = (0.65, 0.25, 0.95, 0.75)


def tracker() -> PrimaryFaceTracker:
    return PrimaryFaceTracker(gate_center=0.6, min_iou=0.2, lost_ms=1500.0, ambiguity_ratio=0.8)


def test_iou_basics():
    assert iou(BIG_CENTER, BIG_CENTER) == 1.0
    assert iou(BIG_CENTER, SMALL_CORNER) == 0.0


def test_single_face_acquired_and_continued():
    tr = tracker()
    d = tr.update([BIG_CENTER], 0.0)
    assert d.index == 0 and not d.switched and d.reasons == []
    shifted = (0.37, 0.21, 0.67, 0.71)
    assert tr.update([shifted], 66.0).index == 0


def test_dominant_face_is_primary_regardless_of_order():
    tr = tracker()
    assert tr.update([SMALL_CORNER, BIG_CENTER], 0.0).index == 1


def test_two_equal_faces_are_ambiguous_not_guessed():
    tr = tracker()
    d = tr.update([LEFT_EQ, RIGHT_EQ], 0.0)
    assert d.index is None and d.ambiguous and "primary_ambiguous" in d.reasons


def test_anchor_from_calibration_breaks_the_tie():
    tr = tracker()
    tr.set_anchor(LEFT_EQ)
    assert tr.update([RIGHT_EQ, LEFT_EQ], 0.0).index == 1


def test_second_face_appearing_does_not_steal_primary():
    tr = tracker()
    tr.update([BIG_CENTER], 0.0)
    near = (0.6, 0.2, 0.9, 0.7)  # a second person leaning in, partly overlapping
    d = tr.update([near, BIG_CENTER], 66.0)
    assert d.index == 1 and not d.switched


def test_primary_leaves_other_face_stays_uncertain_then_reacquired():
    tr = tracker()
    tr.update([LEFT_EQ], 0.0)
    d = tr.update([RIGHT_EQ], 100.0)  # the only face is far from the primary track
    assert d.index is None and d.uncertain and "primary_uncertain" in d.reasons
    d = tr.update([RIGHT_EQ], 1000.0)
    assert d.index is None  # still within lost_ms of the last primary sighting
    d = tr.update([RIGHT_EQ], 1600.0)
    assert d.index == 0 and d.switched and "primary_reacquired" in d.reasons


def test_no_faces_then_return_is_reacquisition():
    tr = tracker()
    tr.update([BIG_CENTER], 0.0)
    assert tr.update([], 100.0).index is None
    d = tr.update([BIG_CENTER], 200.0)  # within lost_ms: same track continues
    assert d.index == 0 and not d.switched
    tr.update([], 300.0)
    d = tr.update([BIG_CENTER], 2500.0)
    assert d.index == 0 and d.switched


def test_reset_forgets_track_and_anchor():
    tr = tracker()
    tr.set_anchor(LEFT_EQ)
    tr.update([LEFT_EQ], 0.0)
    tr.reset()
    assert tr.anchor is None and tr.track is None
    d = tr.update([BIG_CENTER], 10.0)
    assert d.index == 0 and not d.switched
