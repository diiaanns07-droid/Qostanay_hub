"""Real MediaPipe Tasks FaceLandmarker through the analyzer (skipped without model/runtime).

The model is not in Git (*.task is ignored): prepare it with
    python -m proctor.attention.model_tool fetch   (one-time, network) or copy it to
    <models_dir>/attention/face_landmarker.task (verified against models.manifest.json).
Face-image checks run only when QORGAU_A04_TEST_IMAGES points to a folder with the PUBLIC MediaPipe
test images (portrait.jpg, man-woman-okay.jpg from storage.googleapis.com/mediapipe-assets/);
no images of people are stored in this repository. None of this measures accuracy on students.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from proctor.attention import create_attention_analyzer
from proctor.attention.testing import make_frame, textured_image
from proctor.settings import Settings
from proctor_contracts.v1 import Direction, HealthStatus, ObservationStatus, SourceMode


@pytest.fixture(scope="module")
def real():
    a = create_attention_analyzer(Settings())
    h = a.load()
    if h.status != HealthStatus.OK:
        a.close()
        pytest.skip(f"real face model not available here: {h.code} {h.message}")
    yield a
    a.close()


def _letterbox(img: np.ndarray, w: int = 640, h: int = 480) -> np.ndarray:
    scale = min(w / img.shape[1], h / img.shape[0])
    small = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    out = np.full((h, w, 3), 90, np.uint8)
    y, x = (h - small.shape[0]) // 2, (w - small.shape[1]) // 2
    out[y:y + small.shape[0], x:x + small.shape[1]] = small
    return out


def _run(a, session, frames):
    a.start_session(session, SourceMode.REPLAY)
    try:
        return [o for i, img in enumerate(frames) for o in a.process(make_frame(session, i, i * 66.7, img))]
    finally:
        a.end_session()


def test_real_model_health(real):
    h = real.health()
    assert h.status == HealthStatus.OK and h.details["num_faces"] >= 2
    assert h.details["model_id"] == "mediapipe-face-landmarker-f16-v1"


def test_no_face_scenes_and_restart(real):
    noise = [textured_image(seed=i) for i in range(5)]
    for session in ("real-1", "real-2"):  # restart: VIDEO timestamps begin again at 0
        obs = _run(real, session, noise)
        assert len(obs) == 5 and all(o.face_count == 0 and o.status == ObservationStatus.OK for o in obs)
    black = np.zeros((480, 640, 3), np.uint8)
    obs = _run(real, "real-3", [black] * 3)
    assert all(o.face_count is None and o.status == ObservationStatus.UNKNOWN and "low_light" in o.quality_flags for o in obs)


IMAGES = Path(os.environ.get("QORGAU_A04_TEST_IMAGES", "/nonexistent"))


def _image(name):
    p = IMAGES / name
    if not p.is_file():
        pytest.skip(f"public test image {name} not provided (QORGAU_A04_TEST_IMAGES)")
    return cv2.imread(str(p))


def test_public_two_person_image_counts_two_faces(real):
    img = _letterbox(_image("man-woman-okay.jpg"))
    obs = _run(real, "real-two", [img] * 3)
    assert all(o.face_count == 2 and "multiple_faces" in o.reasons for o in obs)
    assert all(sum(f.is_primary for f in o.faces) <= 1 for o in obs)


def test_public_portrait_mirror_symmetry(real):
    img = _letterbox(_image("portrait.jpg"))
    a = _run(real, "real-p", [img] * 4)[-1]
    b = _run(real, "real-pf", [cv2.flip(img, 1)] * 4)[-1]
    assert a.face_count == 1 and b.face_count == 1
    assert a.head_pose.yaw_deg == pytest.approx(-b.head_pose.yaw_deg, abs=4.0)
    assert a.head_pose.pitch_deg == pytest.approx(b.head_pose.pitch_deg, abs=4.0)
    assert a.head_direction != Direction.UNKNOWN


def test_second_face_appearing_mid_stream(real):
    """num_faces >= 2 in VIDEO mode reports a newly appearing face. Measured 2026-10-08: an
    inserted face ~83 px wide (640x480) is found from its first frame, ~62 px is not."""
    base = _letterbox(_image("portrait.jpg"))
    first = _run(real, "real-base", [base])[0]
    b = first.faces[0].bbox
    h, w = base.shape[:2]
    crop = base[int(b.y_min * h) - 20:int(b.y_max * h) + 20, int(b.x_min * w) - 20:int(b.x_max * w) + 20]
    small = cv2.resize(crop, (150, int(150 * crop.shape[0] / crop.shape[1])))
    with_two = base.copy()
    with_two[5:5 + small.shape[0], 485:635] = small
    counts = [o.face_count for o in _run(real, "real-mid", [base] * 5 + [with_two] * 5)]
    assert counts == [1] * 5 + [2] * 5


def test_offline_evaluation_tool_runs(tmp_path):
    from proctor.attention.evaluate import evaluate

    img = _letterbox(_image("portrait.jpg"))
    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(30):
        cv2.imwrite(str(frames / f"{i:05d}.png"), img)
    labels = {"person": "public-test-image", "session": "t1",
              "segments": [{"start": 0, "end": 29, "gaze": "center", "faces": 1, "label": "static portrait"}]}
    report = evaluate(frames, labels)
    seg = report["segments"][0]
    assert report["frames"] == 30 and seg["face_count"] == {"1": 30}
    assert sum(seg["gaze_pred"].values()) == 30 and "non_center_runs" in seg
