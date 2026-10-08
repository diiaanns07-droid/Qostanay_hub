"""Phone signals from tracks (owner: A03). Raw per-frame features for fusion (A05) — not incidents.

phone_visible
    present  a detection in this frame belongs to a confirmed track ("detected_in_frame"), or a single
             detection scores >= high_conf_single ("detected_high_confidence"), or a confirmed track missed
             the detector for <= coast_visible_ms ("track_coasting", confidence null)
    unknown  only unconfirmed low-score detections ("unconfirmed_detection"), or the frame is unusable
    absent   no phone detection in a usable frame ("no_detection")

phone_raised  (temporal heuristic; zone = frame band above raise_zone_y_max, see config.py)
    present  a confirmed track's center rose >= raise_min_rise within raise_window_ms and is in the zone
             ("track_rose_into_raise_zone"), or it appeared in the zone, was never seen below it, and stayed
             there >= raise_hold_ms ("appeared_in_raise_zone", rise_observed false). Latched until the center drops below
             raise_exit_y (hysteresis) or the track is lost; while latched: "track_held_in_raise_zone".
    unknown  frame unusable / only unconfirmed tracks / appeared in the zone but not held long enough yet
             ("insufficient_track_history") / came up from below the zone too slowly ("rise_below_threshold")
    absent   no phone in view ("no_phone_in_view") or every confirmed track is below the zone

possible_screen_capture  (cautious pattern; the phone's camera side is NEVER determined)
    present  a confirmed track held steady (center motion <= capture_max_motion) for >= capture_steady_ms,
             large (area >= capture_min_area), in the central upper "in front of the screen" zone.
             This is "a pattern compatible with pointing a phone at the screen", not "a photo was taken".
    insufficient_evidence  otherwise: the webcam cannot see which way the phone camera points, and a phone
             outside the field of view is invisible ("camera_side_not_observable" / "phone_not_in_view")
    unknown  frame unusable
"""

from __future__ import annotations

from dataclasses import dataclass

from proctor_contracts.v1 import PhoneSignal, PhoneSignalName, SignalState

from .config import PhoneConfig
from .detector import RawDetection
from .tracker import Track


@dataclass(frozen=True)
class FrameContext:
    t: float
    usable: bool
    detections: list[RawDetection]
    assigned: list[Track | None]
    tracks: list[Track]


def _r(value: float, nd: int = 3) -> float:
    return round(float(value), nd)


def _mean_conf(points: list) -> float:
    return sum(p.conf for p in points) / len(points) if points else 0.0


# --------------------------------------------------------------------------------------------- visible
def phone_visible(ctx: FrameContext, cfg: PhoneConfig) -> PhoneSignal:
    name = PhoneSignalName.PHONE_VISIBLE
    confirmed_hits = [
        (det, tr) for det, tr in zip(ctx.detections, ctx.assigned) if tr is not None and tr.confirmed(cfg)
    ]
    strong = [det for det in ctx.detections if det.confidence >= cfg.high_conf_single]
    base_facts: dict[str, float | int | bool | str] = {
        "detections": len(ctx.detections),
        "max_confidence": _r(max((d.confidence for d in ctx.detections), default=0.0)),
        "confirmed_tracks": sum(1 for tr in ctx.tracks if tr.confirmed(cfg)),
    }
    if confirmed_hits or strong:
        if confirmed_hits:
            det, tr = max(confirmed_hits, key=lambda p: (p[0].confidence, p[1].age_ms(ctx.t)))
            reason = "detected_in_frame"
        else:
            det = max(strong, key=lambda d: d.confidence)
            tr = ctx.assigned[ctx.detections.index(det)]
            reason = "detected_high_confidence"
        return PhoneSignal(
            name=name,
            state=SignalState.PRESENT,
            confidence=_r(det.confidence),
            track_id=tr.track_id if tr is not None else None,
            reason=reason,
            facts={**base_facts, "visible_ms": _r(tr.age_ms(ctx.t), 1) if tr is not None else 0.0},
        )
    coasting = [
        tr
        for tr in ctx.tracks
        if tr.confirmed(cfg) and 0 < tr.ms_since_detection(ctx.t) <= cfg.coast_visible_ms
    ]
    if coasting:
        tr = max(coasting, key=lambda tr: (tr.hits, -tr.ms_since_detection(ctx.t)))
        return PhoneSignal(
            name=name,
            state=SignalState.PRESENT,
            confidence=None,
            track_id=tr.track_id,
            reason="track_coasting",
            facts={**base_facts, "ms_since_detection": _r(tr.ms_since_detection(ctx.t), 1), "visible_ms": _r(tr.age_ms(ctx.t), 1)},
        )
    if ctx.detections:
        return PhoneSignal(
            name=name,
            state=SignalState.UNKNOWN,
            confidence=_r(max(d.confidence for d in ctx.detections)),
            reason="unconfirmed_detection",
            facts=base_facts,
        )
    if not ctx.usable:
        return PhoneSignal(name=name, state=SignalState.UNKNOWN, reason="frame_unusable", facts=base_facts)
    return PhoneSignal(name=name, state=SignalState.ABSENT, reason="no_detection", facts=base_facts)


# ---------------------------------------------------------------------------------------------- raised
def update_raise(track: Track, t: float, cfg: PhoneConfig) -> None:
    """Advance the per-track raise latch (call once per frame for every live track)."""
    if not track.points:
        return
    cy = track.points[-1].cy
    if track.raised:
        if cy > cfg.raise_exit_y:
            track.raised, track.raised_since, track.raise_reason = False, None, None
        return
    if not track.confirmed(cfg) or cy > cfg.raise_zone_y_max:
        return
    window = track.window_points(t, cfg.raise_window_ms)
    rise = max(p.cy for p in window) - cy if window else 0.0
    history_ms = track.points[-1].t - track.points[0].t
    if rise >= cfg.raise_min_rise and history_ms >= cfg.raise_min_history_ms:
        track.raised, track.raised_since, track.raise_reason, track.raise_rise = True, t, "track_rose_into_raise_zone", rise
        return
    # Appeared already in the zone (never observed below it, over the whole track life) and stayed there.
    if track.max_cy <= cfg.raise_zone_y_max and track.age_ms(t) >= cfg.raise_hold_ms:
        track.raised, track.raised_since, track.raise_reason, track.raise_rise = True, t, "appeared_in_raise_zone", rise


def phone_raised(ctx: FrameContext, cfg: PhoneConfig) -> PhoneSignal:
    name = PhoneSignalName.PHONE_RAISED
    alive = [tr for tr in ctx.tracks if tr.ms_since_detection(ctx.t) <= cfg.coast_visible_ms]
    raised = [tr for tr in alive if tr.raised]
    if raised:
        tr = max(raised, key=lambda tr: (tr.det.confidence, tr.hits))
        window = tr.window_points(ctx.t, max(cfg.raise_window_ms, cfg.raise_hold_ms))
        mean_conf = _mean_conf(window) or tr.det.confidence
        just_now = tr.raised_since == ctx.t
        reason = tr.raise_reason if just_now else "track_held_in_raise_zone"
        return PhoneSignal(
            name=name,
            state=SignalState.PRESENT,
            confidence=_r(min(mean_conf, tr.quality(cfg))),
            track_id=tr.track_id,
            reason=reason or "track_held_in_raise_zone",
            facts={
                "rise_ratio": _r(tr.raise_rise),
                "rise_observed": tr.raise_reason == "track_rose_into_raise_zone",
                "y_center": _r(tr.det.center[1]),
                "zone_y_max": cfg.raise_zone_y_max,
                "raised_ms": _r(ctx.t - (tr.raised_since or ctx.t), 1),
                "track_age_ms": _r(tr.age_ms(ctx.t), 1),
                "mean_detector_confidence": _r(mean_conf),
                "track_quality": tr.quality(cfg),
            },
        )
    confirmed = [tr for tr in alive if tr.confirmed(cfg)]
    if not ctx.usable and not confirmed:
        return PhoneSignal(name=name, state=SignalState.UNKNOWN, reason="frame_unusable")
    if not confirmed:
        if alive:
            return PhoneSignal(name=name, state=SignalState.UNKNOWN, reason="track_unconfirmed", track_id=alive[0].track_id)
        return PhoneSignal(name=name, state=SignalState.ABSENT, reason="no_phone_in_view")
    in_zone = [tr for tr in confirmed if tr.det.center[1] <= cfg.raise_zone_y_max]
    if in_zone:
        tr = max(in_zone, key=lambda tr: tr.hits)
        if tr.max_cy > cfg.raise_zone_y_max:  # came up from below, but too slowly to count as a raise
            window = tr.window_points(ctx.t, cfg.raise_window_ms)
            rise = (max(p.cy for p in window) - tr.points[-1].cy) if window else 0.0
            return PhoneSignal(
                name=name,
                state=SignalState.UNKNOWN,
                track_id=tr.track_id,
                reason="rise_below_threshold",
                facts={
                    "rise_ratio": _r(max(0.0, rise)),
                    "min_rise": cfg.raise_min_rise,
                    "rise_window_ms": cfg.raise_window_ms,
                    "y_center": _r(tr.det.center[1]),
                },
            )
        return PhoneSignal(
            name=name,
            state=SignalState.UNKNOWN,
            track_id=tr.track_id,
            reason="insufficient_track_history",
            facts={"y_center": _r(tr.det.center[1]), "track_age_ms": _r(tr.age_ms(ctx.t), 1), "hold_ms_required": cfg.raise_hold_ms},
        )
    tr = max(confirmed, key=lambda tr: tr.hits)
    window = tr.window_points(ctx.t, cfg.raise_window_ms)
    rise = (max(p.cy for p in window) - tr.points[-1].cy) if window else 0.0
    return PhoneSignal(
        name=name,
        state=SignalState.ABSENT,
        track_id=tr.track_id,
        reason="track_below_raise_zone",
        facts={"rise_ratio": _r(max(0.0, rise)), "y_center": _r(tr.det.center[1]), "zone_y_max": cfg.raise_zone_y_max},
    )


# -------------------------------------------------------------------------------- screen capture
def _capture_pattern(tr: Track, t: float, cfg: PhoneConfig) -> dict[str, float | int | bool | str] | None:
    if not tr.confirmed(cfg) or tr.age_ms(t) < cfg.capture_steady_ms or tr.ms_since_detection(t) > cfg.coast_visible_ms:
        return None
    pts = tr.window_points(t, cfg.capture_steady_ms)
    if len(pts) < 2:
        return None
    hit_ratio = tr.hit_ratio(t, cfg.capture_steady_ms)
    if hit_ratio < cfg.capture_min_hit_ratio:
        return None
    if not all(
        cfg.capture_zone_x_min <= p.cx <= cfg.capture_zone_x_max and p.cy <= cfg.capture_zone_y_max and p.area >= cfg.capture_min_area
        for p in pts
    ):
        return None
    mx = sum(p.cx for p in pts) / len(pts)
    my = sum(p.cy for p in pts) / len(pts)
    motion = max(((p.cx - mx) ** 2 + (p.cy - my) ** 2) ** 0.5 for p in pts)
    if motion > cfg.capture_max_motion:
        return None
    return {
        "steady_ms": _r(pts[-1].t - pts[0].t, 1),
        "center_motion": _r(motion),
        "mean_area": _r(sum(p.area for p in pts) / len(pts), 4),
        "x_center": _r(mx),
        "y_center": _r(my),
        "hit_ratio": _r(hit_ratio),
        "mean_detector_confidence": _r(_mean_conf(pts)),
        "track_quality": tr.quality(cfg),
        "camera_direction_observable": False,
    }


def possible_screen_capture(ctx: FrameContext, cfg: PhoneConfig) -> PhoneSignal:
    name = PhoneSignalName.POSSIBLE_SCREEN_CAPTURE
    matches = [(tr, facts) for tr in ctx.tracks if (facts := _capture_pattern(tr, ctx.t, cfg)) is not None]
    if matches:
        tr, facts = max(matches, key=lambda m: (m[1]["mean_area"], m[0].hits))
        return PhoneSignal(
            name=name,
            state=SignalState.PRESENT,
            confidence=_r(min(float(facts["mean_detector_confidence"]), tr.quality(cfg))),
            track_id=tr.track_id,
            reason="steady_phone_in_front_of_screen_zone",
            facts=facts,
        )
    if not ctx.usable and not ctx.detections:
        return PhoneSignal(name=name, state=SignalState.UNKNOWN, reason="frame_unusable")
    visible = bool(ctx.detections) or any(tr.ms_since_detection(ctx.t) <= cfg.coast_visible_ms for tr in ctx.tracks)
    return PhoneSignal(
        name=name,
        state=SignalState.INSUFFICIENT_EVIDENCE,
        reason="camera_side_not_observable" if visible else "phone_not_in_view",
        facts={"camera_direction_observable": False},
    )
