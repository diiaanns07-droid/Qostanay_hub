"""AttentionAnalyzer contract and value semantics with the fake backend (no model needed)."""

from __future__ import annotations

import json

import jsonschema
import numpy as np
import pytest

from proctor.attention import create_attention_analyzer
from proctor.attention.analyzer import MediaPipeAttentionAnalyzer
from proctor.attention.config import AttentionConfig
from proctor.attention.landmarker import load_manifest
from proctor.attention.testing import FakeFaceBackend, make_frame, mirrored, synthetic_face, textured_image
from proctor.settings import PROCTORING_ROOT, Settings
from proctor_contracts.interfaces import AttentionAnalyzer, FrameAnalyzer
from proctor_contracts.v1 import (
    AttentionObservation,
    Direction,
    GazeMethod,
    HealthStatus,
    ObservationStatus,
    SourceMode,
)

SCHEMA = json.loads((PROCTORING_ROOT / "contracts" / "schema" / "v1" / "qorgau.v1.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": "#/$defs/AttentionObservation"})
CFG = AttentionConfig()


def check_wire(obs: AttentionObservation) -> dict:
    data = obs.model_dump(mode="json")
    VALIDATOR.validate(data)
    assert AttentionObservation.model_validate(data) == obs
    return data


def test_factory_returns_protocol_implementation(tmp_path):
    a = create_attention_analyzer(Settings(models_dir=tmp_path))
    assert isinstance(a, AttentionAnalyzer) and isinstance(a, FrameAnalyzer) and a.name == "attention"


def test_missing_model_is_unavailable_not_an_exception(tmp_path):
    a = create_attention_analyzer(Settings(models_dir=tmp_path))
    h = a.load()
    assert h.status == HealthStatus.UNAVAILABLE and h.code == "model_missing"
    a.start_session("s-missing", SourceMode.LIVE)
    obs = a.process(make_frame("s-missing", 0, 0.0))
    assert len(obs) == 1 and obs[0].status == ObservationStatus.ERROR and obs[0].face_count is None
    assert obs[0].reasons == ["face_model_unavailable"]
    check_wire(obs[0])
    a.close()


def test_wrong_model_file_is_invalid(tmp_path):
    manifest = load_manifest()
    path = tmp_path / manifest.file
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\0" * manifest.size_bytes)  # right size, wrong content
    h = create_attention_analyzer(Settings(models_dir=tmp_path)).load()
    assert h.status == HealthStatus.UNAVAILABLE and h.code == "model_invalid"
    path.write_bytes(b"x")
    assert create_attention_analyzer(Settings(models_dir=tmp_path)).load().code == "model_invalid"


def test_manifest_is_complete():
    m = load_manifest()
    assert m.module == "attention" and m.format == "task" and m.file == "attention/face_landmarker.task"
    assert m.source_url.startswith("https://storage.googleapis.com/mediapipe-models/") and m.license == "Apache-2.0"


def test_observation_fields_follow_the_frame(feeder):
    obs = feeder.feed([synthetic_face()], 3)
    ids = {o.observation_id for o in obs}
    assert len(ids) == 3
    for i, o in enumerate(obs):
        data = check_wire(o)
        assert o.frame_id == i and o.session_id == "sess-a04" and o.source_mode == SourceMode.REPLAY
        assert o.t_session_ms == pytest.approx(i * 1000 / 15)
        assert o.producer.module == "attention" and o.producer.config_version == CFG.config_version
        assert o.latency_ms is not None and o.latency_ms >= 0
        assert data["kind"] == "attention" and o.faces[0].confidence is None  # model gives no per-face score


def test_frame_is_not_mutated(feeder):
    img = textured_image(seed=3)
    before = img.copy()
    feeder.feed([synthetic_face()], 2, image=img)
    assert np.array_equal(img, before) and not img.flags.writeable


def test_session_mismatch_and_no_session_give_nothing(tmp_path, backend):
    a = MediaPipeAttentionAnalyzer(Settings(models_dir=tmp_path), CFG, backend_factory=lambda c: backend)
    a.load()
    backend.set_faces([synthetic_face()])
    assert a.process(make_frame("s1", 0, 0.0)) == []  # before start_session
    a.start_session("s1", SourceMode.REPLAY)
    assert a.process(make_frame("other", 0, 0.0)) == []
    assert len(a.process(make_frame("s1", 0, 0.0))) == 1
    a.close()


def test_no_face_on_usable_frame_is_zero(feeder):
    o = feeder.feed([], 1)[0]
    assert o.status == ObservationStatus.OK and o.face_count == 0 and o.primary_face_present is False
    assert o.head_pose is None and o.gaze is None and o.head_direction == Direction.UNKNOWN
    assert "no_face_detected" in o.reasons
    check_wire(o)


@pytest.mark.parametrize("image,code,flag", [
    (textured_image(luma=10.0, noise=3.0), "no_face_low_light", "low_light"),
    (np.full((480, 640, 3), 128, np.uint8), "no_face_poor_image", "blur"),  # covered/defocused camera
])
def test_no_face_on_unusable_frame_is_unknown_not_zero(feeder, image, code, flag):
    o = feeder.feed([], 1, image=image)[0]
    assert o.status == ObservationStatus.UNKNOWN and o.face_count is None and o.primary_face_present is None
    assert o.reasons == [code] and flag in o.quality_flags


def test_two_faces_counted_with_one_primary(feeder):
    student = synthetic_face()
    other = synthetic_face(center=(0.86, 0.3), face_height=0.22)
    o = feeder.feed([other, student], 1)[0]
    assert o.face_count == 2 and "multiple_faces" in o.reasons
    assert [f.is_primary for f in o.faces] == [False, True]
    assert o.primary_face_present is True and o.head_pose is not None
    check_wire(o)


def test_face_count_limit_is_reported(feeder):
    faces = [synthetic_face(center=(x, 0.4), face_height=0.15) for x in (0.15, 0.38, 0.62, 0.85)]
    o = feeder.feed(faces, 1)[0]
    assert o.face_count == CFG.num_faces and "face_count_at_limit" in o.reasons


def test_ambiguous_primary_is_unknown(feeder):
    left = synthetic_face(center=(0.25, 0.45), face_height=0.35)
    right = synthetic_face(center=(0.75, 0.45), face_height=0.35)
    o = feeder.feed([left, right], 1)[0]
    assert o.face_count == 2 and o.primary_face_present is None and o.status == ObservationStatus.DEGRADED
    assert o.head_direction == Direction.UNKNOWN and o.gaze is None and "primary_ambiguous" in o.reasons


def test_head_turn_directions_are_subject_centric_and_mirror(feeder):
    right = feeder.feed([synthetic_face(yaw=35.0, pitch=-8.0)], 10)[-1]
    assert right.head_pose.yaw_deg > 30 and right.head_direction == Direction.RIGHT
    assert right.gaze.direction == Direction.RIGHT
    feeder.feed([], 20)  # reset filters
    left = feeder.feed([mirrored(synthetic_face(yaw=35.0, pitch=-8.0))], 10)[-1]
    assert left.head_pose.yaw_deg < -30 and left.head_direction == Direction.LEFT
    assert left.gaze.direction == Direction.LEFT
    assert left.head_pose.pitch_deg == pytest.approx(right.head_pose.pitch_deg, abs=0.5)


def test_eyes_only_down_look_is_down_with_fused_method(feeder):
    o = feeder.feed([synthetic_face(pitch=-12.0, eye_pitch=-25.0)], 10)[-1]
    assert o.head_direction == Direction.CENTER  # head alone: not down
    assert o.gaze.direction == Direction.DOWN and o.gaze.method == GazeMethod.FUSED
    assert not o.gaze.calibrated and "uncalibrated" in o.quality_flags


def test_brief_glance_is_debounced(feeder):
    feeder.feed([synthetic_face(pitch=-8.0)], 10)
    glance = feeder.feed([synthetic_face(pitch=-45.0)], 1)
    back = feeder.feed([synthetic_face(pitch=-8.0)], 5)
    assert all(o.gaze.direction == Direction.CENTER for o in glance + back)


def test_blink_holds_eye_cue_then_falls_back(feeder):
    feeder.feed([synthetic_face(pitch=-10.0, eye_pitch=-25.0)], 10)
    blink = feeder.feed([synthetic_face(pitch=-10.0, eye_pitch=-25.0, blink=True)], 3)
    assert all("blink_hold" in o.reasons and o.gaze.method == GazeMethod.FUSED for o in blink)
    assert all(o.gaze.direction == Direction.DOWN for o in blink)
    closed = feeder.feed([synthetic_face(pitch=-10.0, blink=True)], 8)[-1]
    assert closed.gaze.method == GazeMethod.HEAD_POSE_ONLY and "eyes_closed" in closed.reasons


def test_low_quality_face_is_unknown_not_looking_away(feeder):
    dark = textured_image(luma=14.0, noise=3.0)
    o = feeder.feed([synthetic_face(yaw=40.0)], 6, image=dark)[-1]
    assert o.face_count == 1 and "low_light" in o.quality_flags
    assert o.gaze.direction == Direction.UNKNOWN and o.head_direction == Direction.UNKNOWN
    assert "low_face_quality" in o.reasons and o.status == ObservationStatus.DEGRADED


def test_extreme_roll_is_unknown(feeder):
    o = feeder.feed([synthetic_face(roll=70.0)], 3)[-1]
    assert o.head_direction == Direction.UNKNOWN and "extreme_roll" in o.reasons


def test_missing_pose_matrix_is_degraded_unknown(feeder):
    o = feeder.feed([synthetic_face(with_matrix=False)], 1)[0]
    assert o.status == ObservationStatus.DEGRADED and o.head_pose is None and "pose_unavailable" in o.reasons
    assert o.primary_face_present is True and o.face_count == 1


def test_face_lost_right_after_looking_down(feeder):
    feeder.feed([synthetic_face(pitch=-40.0)], 10)
    o = feeder.feed([], 1)[0]
    assert o.face_count == 0 and "face_lost_after_down" in o.reasons
    later = feeder.feed([], 40)[-1]
    assert "face_lost_after_down" not in later.reasons


def test_inference_errors_are_error_observations_and_health(feeder, analyzer, backend):
    backend.fail_next = 30
    obs = feeder.feed([synthetic_face()], 30)
    assert all(o.status == ObservationStatus.ERROR and o.reasons == ["inference_error"] for o in obs)
    feeder.feed([synthetic_face()], 5)
    h = analyzer.health()
    assert h.status == HealthStatus.DEGRADED and h.code == "inference_errors"
    feeder.feed([synthetic_face()], 60)  # recovers once recent frames succeed again
    assert analyzer.health().status == HealthStatus.OK


def test_restart_between_sessions(analyzer, backend, feeder):
    feeder.feed([synthetic_face()], 5)
    resets = backend.resets
    analyzer.end_session()
    assert backend.closed  # the model's tracking state is dropped with the session
    analyzer.end_session()  # idempotent
    analyzer.start_session("sess-2", SourceMode.REPLAY)
    assert backend.resets == resets + 1 and not backend.closed
    o = analyzer.process(make_frame("sess-2", 0, 0.0))  # timestamps restart at 0
    assert len(o) == 1 and o[0].session_id == "sess-2" and o[0].frame_id == 0
    assert analyzer.process(make_frame("sess-a04", 99, 9999.0)) == []
    analyzer.close()
    assert backend.closed and analyzer.health().status == HealthStatus.STOPPED


def test_duplicate_timestamps_are_made_strictly_increasing(feeder, analyzer, backend):
    backend.set_faces([synthetic_face()])
    for fid in range(3):
        analyzer.process(make_frame("sess-a04", fid, 10.0))  # replay may repeat a timestamp
    assert backend.calls == sorted(set(backend.calls)) and len(backend.calls) == 3


def test_large_frames_are_downscaled_for_inference(feeder, backend):
    big = textured_image(width=1920, height=1080)
    o = feeder.feed([synthetic_face(width=1920, height=1080)], 1, image=big)[0]
    assert o.face_count == 1
    assert len(backend.calls) == 1
