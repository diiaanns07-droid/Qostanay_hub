"""Evaluation tooling (metrics + clip runner) with fake detections. Checks the arithmetic, not CV accuracy."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from proctor.phone.analyzer import PhoneAnalyzer
from proctor.phone.eval import clips as clip_eval
from proctor.phone.eval.images import read_labels
from proctor.phone.eval.metrics import (
    Interval,
    average_precision,
    iou,
    match_boxes,
    match_events,
    ratio,
    runs_to_intervals,
)
from proctor.phone.tests.helpers import FakeDetector, box


def test_iou_and_box_matching_is_one_to_one():
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1.0
    assert iou((0, 0, 1, 1), (2, 2, 3, 3)) == 0.0
    gts = [(0.0, 0.0, 0.2, 0.2)]
    preds = [((0.0, 0.0, 0.2, 0.21), 0.9), ((0.0, 0.0, 0.2, 0.2), 0.5)]
    pred_tp, gt_hit = match_boxes(preds, gts, 0.5)
    assert pred_tp == [True, False] and gt_hit == [True]  # higher score wins, duplicate is a FP


def test_average_precision_and_ratio():
    assert average_precision([(0.9, True), (0.8, False), (0.7, True)], 2) == pytest.approx(0.8333, abs=1e-4)
    assert average_precision([], 0) is None
    assert ratio(1, 0) is None and ratio(1, 4) == 0.25


def test_runs_to_intervals_merges_gaps_and_drops_short_runs():
    samples = [(t * 100.0, flag) for t, flag in enumerate([0, 1, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0])]
    samples = [(t, bool(f)) for t, f in samples]
    # runs: 100-300, 400-600 (gap 100 -> merged), 1000-1100 (100 ms, dropped by min 150)
    assert runs_to_intervals(samples, merge_gap_ms=150, min_duration_ms=150, frame_ms=100) == [Interval(100, 600)]
    assert runs_to_intervals([], 100, 0) == []


def test_match_events_uses_tolerance_and_matches_once():
    labelled = [Interval(1000, 2000)]
    pred = [Interval(2300, 2600), Interval(1100, 1900)]
    pm, lm = match_events(pred, labelled, min_overlap_ms=200, tolerance_ms=500)
    assert lm == [True] and pm == [False, True]  # the larger overlap wins; the other is a false alarm
    pm, lm = match_events([Interval(2600, 3000)], labelled, min_overlap_ms=200, tolerance_ms=500)
    assert lm == [False] and pm == [False]


def test_read_labels_parses_yolo_format(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("67 0.5 0.5 0.2 0.4\n0 0.1 0.1 0.1 0.1\nbad line\n", encoding="utf-8")
    labels = read_labels(path)
    assert labels[0][0] == 67 and labels[0][1] == pytest.approx((0.4, 0.3, 0.6, 0.7))
    assert len(labels) == 2 and read_labels(tmp_path / "missing.txt") == []


def _clip_manifest(tmp_path, clips):
    path = tmp_path / "clips.json"
    path.write_text(json.dumps({"dataset_id": "t", "clips": clips}), encoding="utf-8")
    return path


def test_clip_manifest_rejects_split_leaks_and_duplicates(tmp_path):
    base = {"frames_dir": "f", "fps": 8, "events": []}
    leak = [{**base, "clip_id": "a", "split": "tune", "subject_group": "p1"}, {**base, "clip_id": "b", "split": "test", "subject_group": "p1"}]
    with pytest.raises(clip_eval.ManifestError, match="leak"):
        clip_eval.load_clip_manifest(_clip_manifest(tmp_path, leak))
    dup = [{**base, "clip_id": "a", "split": "tune"}, {**base, "clip_id": "a", "split": "test"}]
    with pytest.raises(clip_eval.ManifestError, match="twice"):
        clip_eval.load_clip_manifest(_clip_manifest(tmp_path, dup))
    bad = [{**base, "clip_id": "a", "split": "tune", "events": [{"signal": "phone_visible", "t_start_ms": 5, "t_end_ms": 1}]}]
    with pytest.raises(clip_eval.ManifestError):
        clip_eval.load_clip_manifest(_clip_manifest(tmp_path, bad))


def test_clip_evaluation_end_to_end_with_fake_detector(settings, tmp_path):
    """16 frames at 8 fps; a fake phone is 'detected' on frames 4..11 (500-1500 ms)."""
    frames = tmp_path / "frames" / "c1"
    frames.mkdir(parents=True)
    rng = np.random.default_rng(1)
    for i in range(16):
        cv2.imwrite(str(frames / f"{i:06d}.png"), rng.integers(60, 190, size=(120, 160, 3), dtype=np.uint8))
    neg = tmp_path / "frames" / "c2"
    neg.mkdir()
    for i in range(8):
        cv2.imwrite(str(neg / f"{i:06d}.png"), rng.integers(60, 190, size=(120, 160, 3), dtype=np.uint8))
    clips = [
        {"clip_id": "c1", "split": "test", "frames_dir": "frames/c1", "fps": 8,
         "events": [{"signal": "phone_visible", "t_start_ms": 500, "t_end_ms": 1500}]},
        {"clip_id": "c2", "split": "test", "frames_dir": "frames/c2", "fps": 8, "events": []},
    ]
    manifest = clip_eval.load_clip_manifest(_clip_manifest(tmp_path, clips))
    def script(i):
        # call order: c1 frames 0..15, then c2 frames 16..23
        return [box(0.5, 0.75, conf=0.7)] if 4 <= i <= 11 else []

    analyzer = PhoneAnalyzer(settings, detector=FakeDetector(script=script))
    assert analyzer.load().code == "model_loaded"
    report = clip_eval.evaluate(manifest, tmp_path, analyzer, cv2, split="test")
    rows = {r["signal"]: r for r in report["results"] if r["split"] == "test"}
    assert rows["phone_visible"]["event_recall_frac"] == "1/1"
    assert rows["phone_visible"]["event_precision_frac"] == "1/1"
    assert rows["phone_visible"]["negative_clips_with_false_alarm"] == "0/1"
    assert report["clip_stats"]["c1"]["frames"] == 16 and report["clip_stats"]["c2"]["frames"] == 8
    # possible_screen_capture is never "present" here and has no labels: precision has an empty denominator
    assert rows["possible_screen_capture"]["event_precision_frac"] == "0/0"
