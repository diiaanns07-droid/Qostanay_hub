"""Phone analyzer configuration (owner: A03).

All thresholds are heuristics chosen for a laptop webcam on top of the screen and are NOT tuned on
measured exam clips yet (see handoffs/A03/STATUS.md, "NOT EVALUATED"). Every value that changes
behaviour is part of ``config_version`` so observations/reports name the exact configuration.

Overrides: environment variables ``QORGAU_PHONE_<FIELD>`` (upper case), e.g.
``QORGAU_PHONE_INPUT_SIZE=480`` or ``QORGAU_PHONE_CONF_THRESHOLD=0.3``. Settings (A01) stay read-only.

Geometry convention (CONTRACTS.md §Coordinates): normalized [0,1] of the UNMIRRORED frame, origin
top-left, x right, y down. "Raise zone" = the band of the frame ABOVE ``raise_zone_y_max`` (y smaller),
i.e. chest/face height for a webcam that looks at the student's face; a phone lying on the desk is
normally out of view for such a camera.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, fields, replace

CONFIG_SCHEMA = "phone-cfg-1"


@dataclass(frozen=True)
class PhoneConfig:
    # ---- model / inference -------------------------------------------------------------------
    input_size: int = 640  # long side of the letterboxed network input; multiple of 32 (model has dynamic H/W)
    rect: bool = True  # pad only up to a stride multiple (e.g. 640x480 stays 640x480) instead of a square
    conf_threshold: float = 0.20  # per-box "cell phone" score floor (yolo11n phone scores are often 0.2-0.5)
    nms_iou: float = 0.45
    max_detections: int = 16  # contract limit for PhoneObservation.detections
    min_box_area: float = 0.0001  # normalized area floor (1% x 1% of the frame); smaller boxes are dropped
    phone_class_names: tuple[str, ...] = ("cell phone",)  # looked up in the MODEL's own names, never by index
    # Review-only objects (no automatic action anywhere): COCO "book" (notes/book) and "laptop"/"tv" (second screen).
    # They are reported as plain detections with class_name; the phone tracker and signals never see them.
    object_class_names: tuple[str, ...] = ("book", "laptop", "tv")  # () = off; names absent in the model are skipped
    object_conf_threshold: float = 0.50  # per-box score floor for objects (stricter than phones)
    intra_op_threads: int = 2  # ORT CPU threads; leaves cores for capture/attention
    execution_provider: str = "cpu"  # "cpu" (verified) | "auto" (first provider ORT reports; GPU is optional/unverified)
    min_interval_ms: float = 0.0  # extra inference period on top of settings.phone_max_fps (0 = off)
    warmup_runs: int = 1
    high_conf_single: float = 0.50  # one detection at/above this is reported as visible without track confirmation

    # ---- frame quality (mean/std on a 0..255 gray thumbnail) ---------------------------------
    quality_thumb_width: int = 160
    unusable_mean_low: float = 12.0  # darker -> "unknown" (detector cannot see anything)
    dark_mean: float = 45.0  # darker -> flag low_light
    bright_mean: float = 235.0  # brighter -> flag overexposed
    unusable_std: float = 3.0  # flatter -> blank/uniform frame -> "unknown"
    low_contrast_std: float = 12.0
    blur_laplacian_var: float = 15.0  # variance of Laplacian on the thumbnail; below -> flag blur
    stale_ms: float = 1000.0  # capture -> processing age above this -> flag stale
    small_object_area: float = 0.002  # detection area below this -> flag small_object
    edge_margin: float = 0.01  # detection touching the border within this margin -> flag edge_of_frame

    # ---- tracking (time based, t_session_ms) --------------------------------------------------
    track_iou_min: float = 0.20
    track_center_gate: float = 0.18  # max normalized center distance for a low-IoU (fast motion) association
    track_confirm_hits: int = 2
    track_max_miss_ms: float = 700.0  # short dropouts keep the track id; longer -> track deleted
    track_history_ms: float = 3000.0
    track_recent_len: int = 10  # per-update hit/miss memory used for track_quality
    max_tracks: int = 8
    coast_visible_ms: float = 400.0  # phone_visible stays present ("track_coasting") this long after a miss

    # ---- phone_raised -------------------------------------------------------------------------
    raise_zone_y_max: float = 0.60  # track center y <= this -> inside the raise zone
    raise_exit_y: float = 0.68  # hysteresis: latched "raised" ends when center y > this
    raise_min_rise: float = 0.12  # upward travel of the center (fraction of frame height) ...
    raise_window_ms: float = 1500.0  # ... within this window
    raise_hold_ms: float = 600.0  # a phone that APPEARS in the zone must stay this long (rise not observed)
    raise_min_history_ms: float = 200.0

    # ---- possible_screen_capture (pattern only; camera side is never claimed) -----------------
    capture_zone_x_min: float = 0.20
    capture_zone_x_max: float = 0.80
    capture_zone_y_max: float = 0.55
    capture_min_area: float = 0.012  # ~11% x 11% of the frame: held close, in front of the student
    capture_steady_ms: float = 800.0
    capture_max_motion: float = 0.035  # max center deviation from the window mean (normalized)
    capture_min_hit_ratio: float = 0.6  # detections / updates inside the steady window


    def __post_init__(self) -> None:
        problems = []
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, float) and not math.isfinite(value):
                problems.append(f"{f.name} must be a finite number")
        if self.input_size % 32 or not 160 <= self.input_size <= 1280:
            problems.append("input_size must be a multiple of 32 in [160, 1280]")
        for name in ("conf_threshold", "object_conf_threshold", "nms_iou", "high_conf_single", "track_iou_min", "capture_min_hit_ratio"):
            if not 0.0 < getattr(self, name) <= 1.0:
                problems.append(f"{name} must be in (0, 1]")
        for name in (
            "min_box_area",
            "track_center_gate",
            "raise_zone_y_max",
            "raise_exit_y",
            "raise_min_rise",
            "capture_zone_x_min",
            "capture_zone_x_max",
            "capture_zone_y_max",
            "capture_min_area",
            "capture_max_motion",
            "small_object_area",
            "edge_margin",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                problems.append(f"{name} must be in [0, 1]")
        if not 1 <= self.max_detections <= 16:
            problems.append("max_detections must be in [1, 16]")
        if self.intra_op_threads < 1 or self.track_confirm_hits < 1 or self.max_tracks < 1 or self.track_recent_len < 1:
            problems.append("thread/track counts must be >= 1")
        if self.raise_exit_y < self.raise_zone_y_max:
            problems.append("raise_exit_y must be >= raise_zone_y_max (hysteresis)")
        if self.capture_zone_x_min >= self.capture_zone_x_max:
            problems.append("capture_zone_x_min must be < capture_zone_x_max")
        if self.execution_provider not in ("cpu", "auto"):
            problems.append("execution_provider must be 'cpu' or 'auto'")
        if not self.phone_class_names:
            problems.append("phone_class_names must not be empty")
        if set(self.object_class_names) & set(self.phone_class_names):
            problems.append("object_class_names must not repeat phone classes")
        for name in (
            "min_interval_ms",
            "stale_ms",
            "track_max_miss_ms",
            "track_history_ms",
            "coast_visible_ms",
            "raise_window_ms",
            "raise_hold_ms",
            "raise_min_history_ms",
            "capture_steady_ms",
        ):
            if getattr(self, name) < 0:
                problems.append(f"{name} must be >= 0")
        if self.track_history_ms < max(self.raise_window_ms, self.capture_steady_ms):
            problems.append("track_history_ms must cover raise_window_ms and capture_steady_ms")
        if problems:
            raise ValueError("; ".join(problems))

    # ------------------------------------------------------------------------------------------
    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["phone_class_names"] = list(self.phone_class_names)
        data["object_class_names"] = list(self.object_class_names)
        return data

    @property
    def config_version(self) -> str:
        """Short, stable id of the behaviour-relevant values (fits Producer.config_version)."""
        blob = json.dumps({"schema": CONFIG_SCHEMA, **self.as_dict()}, sort_keys=True, separators=(",", ":"))
        return f"{CONFIG_SCHEMA}.{hashlib.sha256(blob.encode()).hexdigest()[:12]}"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None, **overrides: object) -> "PhoneConfig":
        """Defaults <- QORGAU_PHONE_<FIELD> <- overrides. Raises ValueError on invalid values."""
        env = os.environ if environ is None else environ
        base = cls()
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = env.get(f"QORGAU_PHONE_{f.name.upper()}")
            if raw is None:
                continue
            values[f.name] = _coerce(f.name, raw, getattr(base, f.name))
        return replace(base, **{**values, **overrides})


def _coerce(name: str, raw: str, default: object) -> object:
    text = raw.strip()
    try:
        if isinstance(default, bool):
            if text.lower() in {"1", "true", "yes", "on"}:
                return True
            if text.lower() in {"0", "false", "no", "off"}:
                return False
            raise ValueError(text)
        if isinstance(default, int):
            return int(text)
        if isinstance(default, float):
            return float(text)
        if isinstance(default, tuple):
            items = tuple(part.strip() for part in text.split(",") if part.strip())
            if not items:
                raise ValueError(text)
            return items
    except ValueError:
        raise ValueError(f"QORGAU_PHONE_{name.upper()}: invalid value {raw!r}") from None
    return text
