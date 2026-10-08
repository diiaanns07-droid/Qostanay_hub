"""Evaluation metrics (owner: A03). Pure functions, no model, no I/O.

Image level: greedy one-to-one matching of predicted boxes to ground-truth boxes by IoU (highest score
first), per confidence threshold; AP50 with all-point interpolation.
Event level: predicted intervals (runs of a signal == present) vs labelled intervals of the same signal;
a predicted interval matches a labelled one when their temporal overlap is >= min_overlap_ms (after
extending the labelled interval by tolerance_ms on both sides). Each labelled event matches at most once.
Every rate is reported together with its numerator and denominator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

Box = tuple[float, float, float, float]  # x_min, y_min, x_max, y_max (any consistent unit)


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match_boxes(preds: Sequence[tuple[Box, float]], gts: Sequence[Box], iou_threshold: float) -> tuple[list[bool], list[bool]]:
    """Return (pred_is_tp, gt_is_matched). preds are (box, score); higher scores match first."""
    order = sorted(range(len(preds)), key=lambda i: -preds[i][1])
    pred_tp = [False] * len(preds)
    gt_used = [False] * len(gts)
    for i in order:
        best, best_j = iou_threshold, -1
        for j, gt in enumerate(gts):
            if gt_used[j]:
                continue
            value = iou(preds[i][0], gt)
            if value >= best:
                best, best_j = value, j
        if best_j >= 0:
            gt_used[best_j] = True
            pred_tp[i] = True
    return pred_tp, gt_used


def ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def average_precision(scored_tp: Iterable[tuple[float, bool]], num_gt: int) -> float | None:
    """All-point interpolated AP from (score, is_tp) over the whole dataset."""
    items = sorted(scored_tp, key=lambda x: -x[0])
    if num_gt == 0:
        return None
    tp = fp = 0
    precisions: list[float] = []
    recalls: list[float] = []
    for _, is_tp in items:
        tp += int(is_tp)
        fp += int(not is_tp)
        precisions.append(tp / (tp + fp))
        recalls.append(tp / num_gt)
    ap, prev_r = 0.0, 0.0
    for k in range(len(precisions)):
        p_interp = max(precisions[k:])
        ap += (recalls[k] - prev_r) * p_interp
        prev_r = recalls[k]
    return round(ap, 4)


# ------------------------------------------------------------------------------------- events
@dataclass(frozen=True)
class Interval:
    start_ms: float
    end_ms: float

    @property
    def duration_ms(self) -> float:
        return max(0.0, self.end_ms - self.start_ms)


def runs_to_intervals(
    samples: Sequence[tuple[float, bool]], merge_gap_ms: float, min_duration_ms: float, frame_ms: float | None = None
) -> list[Interval]:
    """Turn per-frame (t_ms, present) samples into intervals. A run ends at the time of the next sample
    (or + frame_ms for the last one); runs separated by <= merge_gap_ms are merged; short runs dropped."""
    if not samples:
        return []
    samples = sorted(samples)
    if frame_ms is None:
        gaps = [b[0] - a[0] for a, b in zip(samples, samples[1:]) if b[0] > a[0]]
        frame_ms = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
    runs: list[Interval] = []
    start: float | None = None
    for k, (t, present) in enumerate(samples):
        nxt = samples[k + 1][0] if k + 1 < len(samples) else t + frame_ms
        if present and start is None:
            start = t
        if present and (k + 1 == len(samples) or not samples[k + 1][1]):
            runs.append(Interval(start if start is not None else t, nxt))
            start = None
    merged: list[Interval] = []
    for run in runs:
        if merged and run.start_ms - merged[-1].end_ms <= merge_gap_ms:
            merged[-1] = Interval(merged[-1].start_ms, max(merged[-1].end_ms, run.end_ms))
        else:
            merged.append(run)
    return [r for r in merged if r.duration_ms >= min_duration_ms]


def overlap_ms(a: Interval, b: Interval) -> float:
    return max(0.0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))


def match_events(
    predicted: Sequence[Interval], labelled: Sequence[Interval], min_overlap_ms: float, tolerance_ms: float
) -> tuple[list[bool], list[bool]]:
    """Return (pred_matched, label_matched). Greedy by largest overlap."""
    pairs = []
    for i, p in enumerate(predicted):
        for j, g in enumerate(labelled):
            widened = Interval(g.start_ms - tolerance_ms, g.end_ms + tolerance_ms)
            ov = overlap_ms(p, widened)
            if ov >= min_overlap_ms and ov > 0:
                pairs.append((ov, i, j))
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    pred_used = [False] * len(predicted)
    lab_used = [False] * len(labelled)
    for _, i, j in pairs:
        if pred_used[i] or lab_used[j]:
            continue
        pred_used[i] = lab_used[j] = True
    return pred_used, lab_used
