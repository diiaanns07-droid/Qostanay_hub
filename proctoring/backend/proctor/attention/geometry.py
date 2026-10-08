"""Pure geometry for faces (no MediaPipe import): bbox, head pose, eye/iris features.

Conventions (CONTRACTS.md §Coordinates/§Directions):
  * landmarks are normalized to the UNMIRRORED frame: x right, y down, origin top-left;
  * angles are subject-centric degrees: +yaw = the student turns to THEIR right
    (= towards the image LEFT of an unmirrored camera frame), +pitch = up,
    +roll = tilt towards their right shoulder;
  * head pose is expressed relative to the line of sight from the face to the camera
    ("ray frame"), so a student who moves sideways but keeps looking at the camera keeps
    yaw ~ 0. This is NOT the camera optical axis.

MediaPipe facial transformation matrix (Tasks FaceLandmarker, geometry pipeline): maps the
canonical face model into a right-handed camera space with x right, y up, z towards the
viewer (the face sits at negative z). Verified on 2026-10-08 with public MediaPipe test images:
the yaw computed here has the opposite sign of the image-space nose offset and flips sign under
horizontal mirroring while pitch stays (see handoffs/A04/STATUS.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np

# --- MediaPipe 478-point face mesh indices used here ------------------------------------
# "right"/"left" are the SUBJECT's eyes. In an unmirrored frame the subject's right eye is on
# the image LEFT. Verified on the public test images: iris 468 lies between 33 and 133.
NOSE_TIP = 1
FOREHEAD = 10
CHIN = 152
CHEEK_RIGHT = 234  # subject's right (image left)
CHEEK_LEFT = 454
R_EYE_OUTER, R_EYE_INNER, R_EYE_UPPER, R_EYE_LOWER, R_IRIS = 33, 133, 159, 145, 468
L_EYE_INNER, L_EYE_OUTER, L_EYE_UPPER, L_EYE_LOWER, L_IRIS = 362, 263, 386, 374, 473
NUM_MESH = 468
NUM_WITH_IRIS = 478


@dataclass(frozen=True)
class Pose:
    yaw: float
    pitch: float
    roll: float
    distance: float  # |translation| in the matrix units (cm for MediaPipe), informative only


@dataclass(frozen=True)
class EyeFeatures:
    """Eye cues of one face. Iris offsets are fractions of the eye width.

    h_*: + = iris towards the image RIGHT (= the subject looks to THEIR left);
    v_*: + = iris lower. None when the eye could not be measured.
    bs_yaw: blendshape horizontal score, + = eyes turned to the subject's RIGHT (about -1..1);
    bs_pitch: blendshape vertical score eyeLookUp - eyeLookDown, + = up (about -1..1).
    Measured on public test images (STATUS.md): the vertical iris offset barely moves when the
    eyes look down (lids follow the iris), the eyeLookDown blendshape does, so vertical eye
    rotation prefers blendshapes and falls back to the iris offset.
    """

    h_right: float | None
    v_right: float | None
    h_left: float | None
    v_left: float | None
    open_right: float | None  # lid gap / eye width (geometric)
    open_left: float | None
    width_right_px: float
    width_left_px: float
    closed_right: bool  # blink/closed: no usable eye cue from this eye
    closed_left: bool
    bs_yaw: float | None
    bs_pitch: float | None


def face_bbox(landmarks: np.ndarray) -> tuple[float, float, float, float]:
    """Normalized bbox (x_min, y_min, x_max, y_max) clipped to [0, 1]."""
    xy = landmarks[:NUM_MESH, :2]
    x0, y0 = np.clip(xy.min(axis=0), 0.0, 1.0)
    x1, y1 = np.clip(xy.max(axis=0), 0.0, 1.0)
    return float(x0), float(y0), float(max(x0, x1)), float(max(y0, y1))


def outside_ratio(landmarks: np.ndarray) -> float:
    xy = landmarks[:NUM_MESH, :2]
    out = (xy < 0.0) | (xy > 1.0)
    return float(out.any(axis=1).mean())


def _ray_basis(t: np.ndarray) -> np.ndarray:
    """Columns: x', y', d — d points from the face to the camera; identity for a centred face."""
    n = float(np.linalg.norm(t))
    d = -t / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
    x = np.cross(np.array([0.0, 1.0, 0.0]), d)
    nx = float(np.linalg.norm(x))
    x = x / nx if nx > 1e-6 else np.array([1.0, 0.0, 0.0])
    y = np.cross(d, x)
    return np.stack([x, y, d], axis=1)


def head_pose_from_matrix(matrix: np.ndarray | None) -> Pose | None:
    """Subject-centric, ray-relative head pose from a 4x4 face pose matrix. None if invalid."""
    if matrix is None:
        return None
    m = np.asarray(matrix, dtype=np.float64)
    if m.shape != (4, 4) or not np.all(np.isfinite(m)):
        return None
    r = m[:3, :3]
    t = m[:3, 3]
    try:
        u_, _, vt = np.linalg.svd(r)
    except np.linalg.LinAlgError:
        return None
    r = u_ @ vt  # drop any scale/shear
    if np.linalg.det(r) <= 0:
        return None
    basis = _ray_basis(t)
    f = basis.T @ (r @ np.array([0.0, 0.0, 1.0]))  # face forward, in the ray frame
    u = basis.T @ (r @ np.array([0.0, 1.0, 0.0]))  # face up
    yaw = math.degrees(math.atan2(-f[0], f[2]))
    pitch = math.degrees(math.atan2(f[1], math.hypot(f[0], f[2])))
    u0 = np.array([0.0, 1.0, 0.0]) - f[1] * f
    n0 = float(np.linalg.norm(u0))
    if n0 < 1e-6:  # looking straight up/down: roll undefined
        roll = 0.0
    else:
        u0 /= n0
        roll = math.degrees(math.atan2(float(np.dot(np.cross(u0, u), f)), float(np.dot(u0, u))))
    return Pose(yaw=yaw, pitch=pitch, roll=roll, distance=float(np.linalg.norm(t)))


def matrix_from_pose(yaw: float, pitch: float, roll: float, t: tuple[float, float, float] = (0.0, 0.0, -60.0)) -> np.ndarray:
    """Inverse of head_pose_from_matrix (used by fakes/tests): ray-relative angles -> 4x4."""
    yr, pr, rr = (math.radians(a) for a in (yaw, pitch, roll))
    f = np.array([-math.sin(yr) * math.cos(pr), math.sin(pr), math.cos(yr) * math.cos(pr)])
    u0 = np.array([0.0, 1.0, 0.0]) - f[1] * f
    u0 /= np.linalg.norm(u0)
    u = u0 * math.cos(rr) + np.cross(f, u0) * math.sin(rr)
    x = np.cross(u, f)
    r_ray = np.stack([x, u, f], axis=1)
    tv = np.asarray(t, dtype=np.float64)
    m = np.eye(4)
    m[:3, :3] = _ray_basis(tv) @ r_ray
    m[:3, 3] = tv
    return m


def mirror_matrix(matrix: np.ndarray) -> np.ndarray:
    """The pose matrix of the horizontally mirrored image (x -> -x)."""
    s = np.diag([-1.0, 1.0, 1.0, 1.0])
    return s @ np.asarray(matrix, dtype=np.float64) @ s


# Left/right index pairs used by this module. A mirrored face still looks like a normal face to
# the model, so on a flipped image MediaPipe puts index 33 on the image LEFT again: mirroring a
# landmark set = flip x AND swap these pairs (other symmetric mesh points are not needed here).
MIRROR_PAIRS = (
    (R_EYE_OUTER, L_EYE_OUTER), (R_EYE_INNER, L_EYE_INNER), (R_EYE_UPPER, L_EYE_UPPER),
    (R_EYE_LOWER, L_EYE_LOWER), (CHEEK_RIGHT, CHEEK_LEFT),
    (468, 473), (469, 474), (470, 475), (471, 476), (472, 477),
)


def mirror_landmarks(landmarks: np.ndarray) -> np.ndarray:
    """What the model would report for the horizontally mirrored image (for tests/fixtures)."""
    out = np.array(landmarks, dtype=np.float32, copy=True)
    out[:, 0] = 1.0 - out[:, 0]
    n = out.shape[0]
    for a, b in MIRROR_PAIRS:
        if a < n and b < n:
            out[[a, b]] = out[[b, a]]
    return out


def _eye(px: np.ndarray, a: int, b: int, upper: int, lower: int, iris: int | None):
    """a = corner on the image-left side, b = image-right side (upright face)."""
    pa, pb = px[a], px[b]
    axis = pb - pa
    width = float(np.hypot(axis[0], axis[1]))
    if width < 1e-6:
        return None, None, None, 0.0
    ex = axis / width
    ey = np.array([-ex[1], ex[0]])  # image "down" for an upright face
    mid = (pa + pb) / 2.0
    opening = abs(float(np.dot(px[upper] - px[lower], ey))) / width
    if iris is None:
        return None, None, opening, width
    rel = px[iris] - mid
    return float(np.dot(rel, ex)) / width, float(np.dot(rel, ey)) / width, opening, width


def eye_features(
    landmarks: np.ndarray,
    width_px: int,
    height_px: int,
    *,
    closed_openness: float,
    blendshapes: Mapping[str, float] | None = None,
    blink_threshold: float = 0.55,
) -> EyeFeatures:
    """2D iris offsets in the eye opening (roll-invariant via the eye axis). Head turns still
    leak into them through parallax (the iris lies in front of the eye corners); that is
    compensated in gaze.eye_rotation, because MediaPipe's iris z did not carry it (STATUS.md)."""
    px = landmarks[:, :2].astype(np.float64) * np.array([width_px, height_px], dtype=np.float64)
    has_iris = landmarks.shape[0] >= NUM_WITH_IRIS
    hr, vr, opr, wr = _eye(px, R_EYE_OUTER, R_EYE_INNER, R_EYE_UPPER, R_EYE_LOWER, R_IRIS if has_iris else None)
    hl, vl, opl, wl = _eye(px, L_EYE_INNER, L_EYE_OUTER, L_EYE_UPPER, L_EYE_LOWER, L_IRIS if has_iris else None)
    bs = blendshapes or {}
    # MediaPipe blendshapes follow ARKit naming: "...Left" = the SUBJECT's left eye (checked on
    # mirrored public test images, see STATUS.md). With blendshapes the blink score decides
    # closure, because the geometric lid gap also shrinks when the student looks down.
    if bs:
        closed_r = bs.get("eyeBlinkRight", 0.0) >= blink_threshold
        closed_l = bs.get("eyeBlinkLeft", 0.0) >= blink_threshold
        bs_yaw = (bs.get("eyeLookOutRight", 0.0) + bs.get("eyeLookInLeft", 0.0)
                  - bs.get("eyeLookOutLeft", 0.0) - bs.get("eyeLookInRight", 0.0)) / 2.0
        bs_pitch = (bs.get("eyeLookUpLeft", 0.0) + bs.get("eyeLookUpRight", 0.0)
                    - bs.get("eyeLookDownLeft", 0.0) - bs.get("eyeLookDownRight", 0.0)) / 2.0
    else:
        closed_r = opr is not None and opr < closed_openness
        closed_l = opl is not None and opl < closed_openness
        bs_yaw = bs_pitch = None
    return EyeFeatures(
        h_right=hr, v_right=vr, h_left=hl, v_left=vl,
        open_right=opr, open_left=opl, width_right_px=wr, width_left_px=wl,
        closed_right=bool(closed_r), closed_left=bool(closed_l),
        bs_yaw=None if bs_yaw is None else float(bs_yaw), bs_pitch=None if bs_pitch is None else float(bs_pitch),
    )
