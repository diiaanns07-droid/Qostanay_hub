"""Primary face selection by geometry and time only (no identity, no face embeddings).

Face presence/count is reported separately from this decision. The primary face is the face
that continues the previous primary track (overlap / center distance), or - with no track -
the clearly dominant face (size x closeness to the calibration anchor or the frame center).
When two candidates are about equally plausible the result is "ambiguous" and the analyzer
reports unknown instead of guessing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

BBoxT = tuple[float, float, float, float]  # normalized x_min, y_min, x_max, y_max


def iou(a: BBoxT, b: BBoxT) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def center(b: BBoxT) -> tuple[float, float]:
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


@dataclass
class PrimaryDecision:
    index: int | None  # index into the candidate list, None = no primary this frame
    reasons: list[str] = field(default_factory=list)
    switched: bool = False  # a new primary track started after a previous one existed
    ambiguous: bool = False
    uncertain: bool = False  # faces present but none continues the (recently lost) track


class PrimaryFaceTracker:
    def __init__(self, gate_center: float, min_iou: float, lost_ms: float, ambiguity_ratio: float, aspect: float = 4 / 3):
        self.gate_center = gate_center
        self.min_iou = min_iou
        self.lost_ms = lost_ms
        self.ambiguity_ratio = ambiguity_ratio
        self.aspect = aspect  # frame width / height, to measure distances in square units
        self.anchor: BBoxT | None = None
        self.reset()

    def reset(self) -> None:
        self.track: BBoxT | None = None
        self.last_seen_ms: float | None = None
        self.had_track = False
        self.anchor = None

    def set_anchor(self, bbox: BBoxT | None) -> None:
        self.anchor = bbox

    def _dist(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot((a[0] - b[0]) * self.aspect, a[1] - b[1])

    def _width(self, b: BBoxT) -> float:
        return max((b[2] - b[0]) * self.aspect, b[3] - b[1], 1e-3)

    def update(self, boxes: list[BBoxT], t_ms: float, aspect: float | None = None) -> PrimaryDecision:
        if aspect:
            self.aspect = aspect
        if self.track is not None and self.last_seen_ms is not None and t_ms - self.last_seen_ms > self.lost_ms:
            self.track = None  # expired: the next face starts a new track
        if not boxes:
            return PrimaryDecision(None)
        if self.track is not None:
            return self._continue(boxes, t_ms)
        return self._acquire(boxes, t_ms)

    def _continue(self, boxes: list[BBoxT], t_ms: float) -> PrimaryDecision:
        assert self.track is not None
        tw = self._width(self.track)
        tc = center(self.track)
        scored = []
        for i, b in enumerate(boxes):
            ov = iou(self.track, b)
            dist = self._dist(center(b), tc) / tw
            if ov >= self.min_iou or dist <= self.gate_center:
                scored.append((ov + max(0.0, 1.0 - dist / self.gate_center) * 0.5, i))
        if not scored:
            # Faces are visible but none continues the primary track (yet): do not guess who
            # the student is until the track expires (lost_ms).
            return PrimaryDecision(None, ["primary_uncertain"], uncertain=True)
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[1][0] >= self.ambiguity_ratio * scored[0][0]:
            return PrimaryDecision(None, ["primary_ambiguous"], ambiguous=True)
        best = scored[0][1]
        self.track, self.last_seen_ms = boxes[best], t_ms
        return PrimaryDecision(best)

    def _acquire(self, boxes: list[BBoxT], t_ms: float) -> PrimaryDecision:
        ref = center(self.anchor) if self.anchor is not None else (0.5, 0.5)
        ref_w = self._width(self.anchor) if self.anchor is not None else 0.35
        scored = []
        for i, b in enumerate(boxes):
            size = self._width(b)
            closeness = math.exp(-((self._dist(center(b), ref) / max(ref_w, 0.2)) ** 2))
            scored.append((size * (0.25 + closeness), i))
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[1][0] >= self.ambiguity_ratio * scored[0][0]:
            return PrimaryDecision(None, ["primary_ambiguous"], ambiguous=True)
        best = scored[0][1]
        reasons = []
        switched = False
        if self.had_track:
            switched = True
            reasons.append("primary_reacquired")
        self.track, self.last_seen_ms, self.had_track = boxes[best], t_ms, True
        return PrimaryDecision(best, reasons, switched=switched)
