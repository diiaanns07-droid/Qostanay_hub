"""A04 attention configuration: every threshold used by the module, versioned.

The values are engineering defaults chosen on public test images and geometry, NOT tuned on
exam recordings (none were available). They are reported in Producer.config_version so every
observation can be traced back to the exact thresholds.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

MODULE_VERSION = "0.1.0"
CONFIG_NAME = "att-cfg-1"


@dataclass(frozen=True)
class AttentionConfig:
    # --- MediaPipe Tasks FaceLandmarker -------------------------------------------------
    # num_faces must be >= 2 to see a second face at all. Measured on 2026-10-08 (mediapipe
    # 0.10.35, VIDEO mode): built-in landmark smoothing is active only with num_faces == 1
    # (inter-frame jitter 0.014 px vs 0.305 px with num_faces 2/4), so this module smooths
    # its own signals instead of relying on MediaPipe.
    num_faces: int = 4
    min_face_detection_confidence: float = 0.5
    min_face_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    output_blendshapes: bool = True
    max_input_width: int = 1280  # larger frames are downscaled before inference (coords stay normalized)

    # --- frame / face quality (heuristics, see quality.py) ---------------------------------
    frame_dark_mean: float = 35.0  # mean luma 0..255 below which "no face" is not trusted
    face_dark_mean: float = 50.0  # face ROI mean luma below which the face is low_light
    face_bright_mean: float = 225.0  # face ROI mean luma above which it is overexposed
    blur_laplacian_var: float = 25.0  # Laplacian variance of a 112 px face crop below which = blur
    frame_blur_laplacian_var: float = 8.0  # whole-frame (downscaled) variance: "no face" untrusted below
    small_face_px: float = 60.0  # face bbox height (px) below which = small_face
    min_eye_width_px: float = 14.0  # iris offsets are not used below this eye width
    partial_face_ratio: float = 0.08  # share of landmarks outside the frame -> partial_face
    min_quality_for_direction: float = 0.3  # below: direction unknown

    # --- primary face selection (geometry/time only, never identity) -----------------------
    primary_ambiguity_ratio: float = 0.8  # second candidate scoring >= ratio*best -> ambiguous
    track_gate_center: float = 0.6  # max center distance in face widths to continue the track
    track_min_iou: float = 0.2
    track_lost_ms: float = 1500.0  # keep the lost primary track this long for re-acquisition

    # --- head pose / gaze -------------------------------------------------------------------
    max_abs_roll_deg: float = 50.0  # beyond: direction unknown (head lying sideways)
    extreme_yaw_deg: float = 45.0  # beyond: quality flag extreme_pose (sign still reliable)
    extreme_pitch_deg: float = 35.0
    # Eye-in-head rotation is approximate: gains come from eyeball geometry (radius ~12 mm,
    # eye width ~30 mm), not from measurements on people. Calibration absorbs offsets, not gains.
    eye_yaw_gain_deg: float = 140.0  # iris offset (fraction of eye width) -> degrees
    eye_pitch_gain_deg: float = 110.0  # vertical iris offset -> degrees (fallback only)
    # Iris-in-front-of-corners parallax (fraction of eye width). Anatomy (~9 mm / ~30 mm) predicts
    # ~0.3; on 4 public photos of people facing the camera the uncorrected eye yaw leaked
    # 0.56-0.84 x head yaw, consistent with p ~ 0.27. Weak evidence: few faces, assumed gaze.
    iris_parallax: float = 0.27
    bs_yaw_gain_deg: float = 30.0  # blendshape horizontal score -> degrees (fallback only)
    bs_pitch_gain_deg: float = 30.0  # blendshape vertical score -> degrees (primary for pitch)
    # Generic rest offsets measured on 6 frontal public test faces (2026-10-08): the iris sits
    # temporally of the corner midpoint (symmetric, cancels when both eyes are averaged) and
    # above the corner line. Replaced per person by the calibration center target.
    rest_h_right: float = -0.065
    rest_h_left: float = 0.075
    rest_v: float = -0.08
    rest_bs_pitch: float = -0.15
    eyes_closed_openness: float = 0.13  # lid gap / eye width (no blendshapes): closed below
    blink_blendshape: float = 0.55  # eyeBlink* score above which the eye counts as closed
    blink_hold_ms: float = 350.0  # keep the last eye cue this long during a blink
    min_iris_openness: float = 0.16  # iris offsets are not used when the lids are this low
    max_eye_yaw_for_both_eyes_deg: float = 30.0  # beyond: only the eye nearer to the camera is used

    # --- direction classification -----------------------------------------------------------
    # Uncalibrated generic geometry (camera above a laptop screen, ~55-65 cm away).
    default_center_yaw_deg: float = 0.0
    default_center_pitch_deg: float = -8.0
    default_edge_left_deg: float = -16.0
    default_edge_right_deg: float = 16.0
    default_edge_up_deg: float = 4.0
    default_edge_down_deg: float = -22.0
    edge_margin: float = 0.3  # how far beyond a screen edge (fraction of center->edge span)
    edge_margin_min_deg: float = 4.0  # ... but at least this many degrees
    hysteresis: float = 0.15  # leave a direction only below (1 + margin - hysteresis)
    debounce_ms: float = 150.0  # a new direction must persist this long (single-frame flicker)
    head_yaw_threshold_deg: float = 22.0  # head_direction (head pose only), relative to center
    head_up_threshold_deg: float = 15.0
    head_down_threshold_deg: float = 18.0
    head_hysteresis_deg: float = 3.0
    filter_min_cutoff_hz: float = 1.2  # One-Euro filter for pose/gaze angles
    filter_beta: float = 0.05
    filter_d_cutoff_hz: float = 1.0
    filter_reset_gap_ms: float = 600.0
    face_lost_context_ms: float = 1500.0  # "face lost right after looking down/aside" window

    # --- calibration ------------------------------------------------------------------------
    calibration_required_samples: int = 20
    calibration_settle_ms: float = 400.0  # ignore frames right after the target was shown
    calibration_target_timeout_ms: float = 12000.0  # explicit failure (not completion) after this
    calibration_min_sample_quality: float = 0.45
    calibration_max_spread_deg: float = 5.0  # robust spread (1.4826*MAD) of fused yaw/pitch
    calibration_min_separation_deg: float = 3.0  # edge targets must differ from center by this

    extra: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def config_version(self) -> str:
        digest = hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()[:10]
        return f"{CONFIG_NAME}-{digest}"
