"""Lightweight multi-phone tracker (owner: A03). Time base: t_session_ms (deterministic in replay).

Association: greedy, IoU first (score 1+IoU for IoU >= track_iou_min), then a center-distance gate for
fast motion (score in (0,1), only when sizes are within 3x). A track survives short detector dropouts for
``track_max_miss_ms`` and keeps its id; after that it is deleted (ids are never reused in a session).

track_quality (association quality, independent from the detector score):
    hit_ratio(last N updates) * (0.5 + 0.5 * EMA(association IoU)) * min(1, hits / confirm_hits)
A brand-new track therefore starts at <= 0.25 and approaches its hit ratio after stable IoU matches.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .config import PhoneConfig
from .detector import RawDetection

_SIZE_RATIO_MAX = 3.0
_IOU_EMA_ALPHA = 0.5


def box_iou(a: RawDetection, b: RawDetection) -> float:
    ix = max(0.0, min(a.x_max, b.x_max) - max(a.x_min, b.x_min))
    iy = max(0.0, min(a.y_max, b.y_max) - max(a.y_min, b.y_min))
    inter = ix * iy
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


@dataclass(frozen=True, slots=True)
class TrackPoint:
    t: float
    cx: float
    cy: float
    area: float
    conf: float


@dataclass
class Track:
    track_id: str
    first_t: float
    last_det_t: float
    det: RawDetection  # last matched detection
    hits: int = 1
    assoc_iou_ema: float = 0.0
    recent: deque[bool] = field(default_factory=deque)
    updates: deque[tuple[float, bool]] = field(default_factory=deque)  # (t, detected) per update
    points: deque[TrackPoint] = field(default_factory=deque)  # detected positions only
    max_cy: float = 0.0  # lowest position ever observed (max center y), NOT pruned with the history
    # phone_raised latch (owned by signals.update_raise)
    raised: bool = False
    raised_since: float | None = None
    raise_reason: str | None = None
    raise_rise: float = 0.0

    def confirmed(self, cfg: PhoneConfig) -> bool:
        return self.hits >= cfg.track_confirm_hits

    def age_ms(self, t: float) -> float:
        return max(0.0, t - self.first_t)

    def ms_since_detection(self, t: float) -> float:
        return max(0.0, t - self.last_det_t)

    def quality(self, cfg: PhoneConfig) -> float:
        hit_ratio = sum(self.recent) / len(self.recent) if self.recent else 0.0
        q = hit_ratio * (0.5 + 0.5 * self.assoc_iou_ema) * min(1.0, self.hits / cfg.track_confirm_hits)
        return round(min(1.0, max(0.0, q)), 3)

    def window_points(self, t: float, window_ms: float) -> list[TrackPoint]:
        return [p for p in self.points if p.t >= t - window_ms]

    def hit_ratio(self, t: float, window_ms: float) -> float:
        ups = [hit for (ut, hit) in self.updates if ut >= t - window_ms]
        return sum(ups) / len(ups) if ups else 0.0

    # ----------------------------------------------------------------------------------------
    def _record(self, t: float, hit: bool, cfg: PhoneConfig) -> None:
        self.recent.append(hit)
        while len(self.recent) > cfg.track_recent_len:
            self.recent.popleft()
        self.updates.append((t, hit))
        horizon = t - cfg.track_history_ms
        while self.updates and self.updates[0][0] < horizon:
            self.updates.popleft()
        while self.points and self.points[0].t < horizon:
            self.points.popleft()

    def _hit(self, det: RawDetection, t: float, iou: float, cfg: PhoneConfig) -> None:
        self.det = det
        self.hits += 1
        self.last_det_t = t
        self.assoc_iou_ema = _IOU_EMA_ALPHA * iou + (1 - _IOU_EMA_ALPHA) * self.assoc_iou_ema
        cx, cy = det.center
        self.max_cy = max(self.max_cy, cy)
        self.points.append(TrackPoint(t, cx, cy, det.area, det.confidence))
        self._record(t, True, cfg)


class PhoneTracker:
    def __init__(self, cfg: PhoneConfig):
        self.cfg = cfg
        self.tracks: list[Track] = []
        self._next_id = 1
        self.created = 0
        self.evicted = 0

    def reset(self) -> None:
        self.tracks = []
        self._next_id = 1
        self.created = 0
        self.evicted = 0

    def update(self, detections: list[RawDetection], t: float) -> list[Track | None]:
        """Associate this frame's detections; returns the track for each detection (None if untracked).
        Caller guarantees non-decreasing t."""
        cfg = self.cfg
        self.tracks = [tr for tr in self.tracks if t - tr.last_det_t <= cfg.track_max_miss_ms]

        candidates: list[tuple[float, int, int, float]] = []  # (score, track idx, det idx, iou)
        for ti, tr in enumerate(self.tracks):
            tcx, tcy = tr.det.center
            for di, det in enumerate(detections):
                iou = box_iou(tr.det, det)
                if iou >= cfg.track_iou_min:
                    candidates.append((1.0 + iou, ti, di, iou))
                    continue
                dcx, dcy = det.center
                dist = ((dcx - tcx) ** 2 + (dcy - tcy) ** 2) ** 0.5
                ratio = det.area / tr.det.area if tr.det.area > 0 else float("inf")
                if dist <= cfg.track_center_gate and 1 / _SIZE_RATIO_MAX <= ratio <= _SIZE_RATIO_MAX:
                    candidates.append((1.0 - dist / cfg.track_center_gate if cfg.track_center_gate else 0.0, ti, di, iou))
        candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

        assigned: list[Track | None] = [None] * len(detections)
        used_tracks: set[int] = set()
        for _, ti, di, iou in candidates:
            if ti in used_tracks or assigned[di] is not None:
                continue
            used_tracks.add(ti)
            self.tracks[ti]._hit(detections[di], t, iou, cfg)
            assigned[di] = self.tracks[ti]
        for ti, tr in enumerate(self.tracks):
            if ti not in used_tracks:
                tr._record(t, False, cfg)

        for di, det in enumerate(detections):
            if assigned[di] is not None:
                continue
            if len(self.tracks) >= cfg.max_tracks:
                idle = [tr for tr in self.tracks if tr.last_det_t < t]
                if not idle:
                    continue  # every track was matched in this frame: leave the detection untracked
                victim = min(idle, key=lambda tr: (tr.last_det_t, tr.hits))
                self.tracks.remove(victim)
                self.evicted += 1
            cx, cy = det.center
            track = Track(track_id=f"ph-{self._next_id}", first_t=t, last_det_t=t, det=det, max_cy=cy)
            track.points.append(TrackPoint(t, cx, cy, det.area, det.confidence))
            track._record(t, True, cfg)
            self._next_id += 1
            self.created += 1
            self.tracks.append(track)
            assigned[di] = track
        return assigned
