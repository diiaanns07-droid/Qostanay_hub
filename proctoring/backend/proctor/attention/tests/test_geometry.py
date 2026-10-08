"""Geometry conventions: head pose signs, ray frame, mirroring, eye features (synthetic ground truth)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from proctor.attention import geometry as g
from proctor.attention.config import AttentionConfig
from proctor.attention.gaze import EyeRest, eye_rotation
from proctor.attention.testing import mirrored, synthetic_face

CFG = AttentionConfig()


@pytest.mark.parametrize("yaw", [-40.0, -12.5, 0.0, 7.0, 35.0])
@pytest.mark.parametrize("pitch", [-30.0, 0.0, 18.0])
@pytest.mark.parametrize("roll", [-20.0, 0.0, 11.0])
@pytest.mark.parametrize("t", [(0.0, 0.0, -60.0), (18.0, -9.0, -55.0)])
def test_pose_matrix_roundtrip(yaw, pitch, roll, t):
    pose = g.head_pose_from_matrix(g.matrix_from_pose(yaw, pitch, roll, t))
    assert pose is not None
    assert pose.yaw == pytest.approx(yaw, abs=1e-6)
    assert pose.pitch == pytest.approx(pitch, abs=1e-6)
    assert pose.roll == pytest.approx(roll, abs=1e-6)


def test_subject_right_turn_points_face_to_image_left():
    """+yaw (student turns to THEIR right) => the nose moves to the image LEFT (unmirrored)."""
    face = synthetic_face(yaw=25.0)
    nose_x = face.landmarks[g.NOSE_TIP, 0]
    mid_x = (face.landmarks[g.CHEEK_RIGHT, 0] + face.landmarks[g.CHEEK_LEFT, 0]) / 2
    assert nose_x < mid_x
    assert g.head_pose_from_matrix(face.matrix).yaw > 20


def test_pitch_up_and_roll_signs():
    up = synthetic_face(pitch=20.0)
    assert up.landmarks[g.NOSE_TIP, 1] < synthetic_face().landmarks[g.NOSE_TIP, 1]  # nose rises in the image
    tilt = synthetic_face(roll=20.0)  # towards their right shoulder = image left
    assert tilt.landmarks[g.FOREHEAD, 0] < tilt.landmarks[g.CHIN, 0]


def test_ray_frame_off_center_face_looking_parallel_to_axis():
    """A face at the image right looking parallel to the optical axis is turned to its LEFT
    relative to the line of sight to the camera (camera is on the student's right)."""
    m = np.eye(4)
    m[:3, 3] = (20.0, 0.0, -60.0)
    pose = g.head_pose_from_matrix(m)
    assert pose.yaw == pytest.approx(-math.degrees(math.atan2(20, 60)), abs=1e-6)
    assert pose.pitch == pytest.approx(0.0, abs=1e-9)


def test_mirror_flips_yaw_and_roll_keeps_pitch():
    m = g.matrix_from_pose(17.0, -9.0, 6.0, (10.0, 5.0, -58.0))
    p, q = g.head_pose_from_matrix(m), g.head_pose_from_matrix(g.mirror_matrix(m))
    assert q.yaw == pytest.approx(-p.yaw, abs=1e-6)
    assert q.roll == pytest.approx(-p.roll, abs=1e-6)
    assert q.pitch == pytest.approx(p.pitch, abs=1e-6)


@pytest.mark.parametrize("bad", [None, np.full((4, 4), np.nan), np.eye(3), np.diag([-1.0, 1.0, 1.0, 1.0])])
def test_invalid_matrix_gives_none(bad):
    assert g.head_pose_from_matrix(bad) is None


def test_scaled_matrix_is_orthonormalized():
    m = g.matrix_from_pose(10.0, 5.0, -3.0)
    m[:3, :3] *= 1.7
    p = g.head_pose_from_matrix(m)
    assert (p.yaw, p.pitch, p.roll) == pytest.approx((10.0, 5.0, -3.0), abs=1e-6)


def _eye_yaw(face):
    pose = g.head_pose_from_matrix(face.matrix)
    eyes = g.eye_features(face.landmarks, 640, 480, closed_openness=CFG.eyes_closed_openness, blendshapes=face.blendshapes)
    return eye_rotation(eyes, pose.yaw, pose.pitch, EyeRest.generic(CFG), CFG), eyes


def test_eye_rotation_signs_and_mirror():
    right, eyes = _eye_yaw(synthetic_face(eye_yaw=15.0))
    assert right.yaw > 9  # eyes to the student's right
    assert eyes.h_right is not None and eyes.h_left is not None
    left, _ = _eye_yaw(mirrored(synthetic_face(eye_yaw=15.0)))
    assert left.yaw < -9
    down, _ = _eye_yaw(synthetic_face(eye_pitch=-15.0))
    assert down.pitch < -10
    up, _ = _eye_yaw(synthetic_face(eye_pitch=12.0))
    assert up.pitch > 8


def test_head_turn_alone_barely_moves_eye_estimate():
    """Parallax compensation: turning only the head must not read as a big eye rotation."""
    rot, _ = _eye_yaw(synthetic_face(yaw=20.0))
    assert abs(rot.yaw) < 6.0
    rot_m, _ = _eye_yaw(mirrored(synthetic_face(yaw=20.0)))
    assert abs(rot_m.yaw) < 6.0


def test_eye_features_roll_invariant():
    a, _ = _eye_yaw(synthetic_face(eye_yaw=10.0))
    b, _ = _eye_yaw(synthetic_face(eye_yaw=10.0, roll=25.0))
    assert b.yaw == pytest.approx(a.yaw, abs=1.5)


def test_blink_and_closed_eyes():
    rot, eyes = _eye_yaw(synthetic_face(blink=True))
    assert eyes.closed_left and eyes.closed_right
    assert rot.yaw is None and rot.pitch is None and "eyes_closed" in rot.reasons
    # without blendshapes the geometric lid gap decides
    f = synthetic_face(blink=True, blendshapes=False)
    e = g.eye_features(f.landmarks, 640, 480, closed_openness=CFG.eyes_closed_openness)
    assert e.closed_left and e.closed_right and e.bs_pitch is None


def test_far_eye_ignored_on_strong_turn():
    rot, _ = _eye_yaw(synthetic_face(yaw=40.0))
    assert "far_eye_ignored" in rot.reasons


def test_mirror_landmarks_keeps_image_left_eye_as_index_33():
    f = synthetic_face(yaw=15.0)
    m = g.mirror_landmarks(f.landmarks)
    assert m[g.R_EYE_OUTER, 0] < m[g.L_EYE_OUTER, 0]
    assert g.mirror_landmarks(m) == pytest.approx(f.landmarks)


def test_bbox_clipped_and_outside_ratio():
    f = synthetic_face(center=(0.97, 0.5))
    x0, y0, x1, y1 = g.face_bbox(f.landmarks)
    assert 0.0 <= x0 <= x1 <= 1.0 and x1 == 1.0
    assert g.outside_ratio(f.landmarks) > 0.1
    assert g.outside_ratio(synthetic_face().landmarks) == 0.0
