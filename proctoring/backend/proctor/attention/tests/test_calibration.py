"""Calibration on real samples (synthetic faces via the fake backend): count/quality decide,
failures are explicit, retry works, data is dropped at session end. No timers decide success."""

from __future__ import annotations

import threading

import pytest

from proctor.attention.config import AttentionConfig
from proctor.attention.testing import FakeFaceBackend, synthetic_face, textured_image
from proctor_contracts.interfaces import InvalidStateError
from proctor_contracts.v1 import (
    CalibrationPhase,
    CalibrationTarget as T,
    CalibrationTargetState as S,
    Direction,
    SourceMode,
)

CFG = AttentionConfig()
PER_TARGET = 32  # > settle (6 frames at 15 fps) + required samples (20)

FACES = {
    T.CENTER: dict(yaw=0.0, pitch=-8.0),
    T.LEFT: dict(yaw=-10.0, pitch=-8.0, eye_yaw=-8.0),
    T.RIGHT: dict(yaw=10.0, pitch=-8.0, eye_yaw=8.0),
    T.UP: dict(yaw=0.0, pitch=-3.0, eye_pitch=6.0),
    T.DOWN: dict(yaw=0.0, pitch=-16.0, eye_pitch=-8.0),
}


def target(state, t):
    return next(x for x in state.targets if x.target == t)


def run_target(a, f, t, faces=None, n=PER_TARGET):
    a.calibration_target(t)
    f.feed(faces if faces is not None else [synthetic_face(**FACES[t])], n)
    return a.calibration_state()


def calibrate(a, f):
    a.calibration_start()
    for t in FACES:
        st = run_target(a, f, t)
        assert target(st, t).state == S.OK, target(st, t)
    return a.calibration_finish()


def test_full_calibration_completes_and_is_used(analyzer, feeder):
    st = calibrate(analyzer, feeder)
    assert st.phase == CalibrationPhase.COMPLETED and st.message_code == "calibration_ok"
    assert st.calibration_id and all(x.samples == CFG.calibration_required_samples for x in st.targets)
    assert all(x.quality is not None and 0 < x.quality <= 1 for x in st.targets)
    obs = feeder.feed([synthetic_face(**FACES[T.CENTER])], 3)[-1]
    assert obs.gaze.calibrated and obs.calibration_id == st.calibration_id
    assert "uncalibrated" not in obs.quality_flags and obs.gaze.direction == Direction.CENTER
    # far below the calibrated bottom edge -> down; the calibrated bottom edge itself -> center
    far_down = feeder.feed([synthetic_face(yaw=0.0, pitch=-30.0, eye_pitch=-15.0)], 8)[-1]
    assert far_down.gaze.direction == Direction.DOWN
    edge = feeder.feed([synthetic_face(**FACES[T.DOWN])], 12)[-1]
    assert edge.gaze.direction == Direction.CENTER


def test_calibration_is_reproducible(analyzer_factory):
    results = []
    for _ in range(2):
        a, f = analyzer_factory(FakeFaceBackend())
        st = calibrate(a, f)
        obs = f.feed([synthetic_face(yaw=14.0, pitch=-8.0, eye_yaw=6.0)], 6)[-1]
        results.append(([(x.target, x.state, x.samples, x.quality) for x in st.targets], obs.gaze.yaw_deg, obs.gaze.direction))
    assert results[0] == results[1]


def test_finish_without_samples_fails_explicitly(analyzer, feeder):
    analyzer.calibration_start()
    st = analyzer.calibration_finish()
    assert st.phase == CalibrationPhase.FAILED and st.message_code == "targets_incomplete"
    required = [x for x in st.targets if x.target != T.UP]  # "up" is optional (laptop webcam)
    assert all(x.state == S.FAILED and x.message_code == "not_collected" for x in required)


def test_one_sample_short_is_still_collecting(analyzer, feeder):
    analyzer.calibration_start()
    settle_frames = 6  # t - started < 400 ms at 15 fps
    st = run_target(analyzer, feeder, T.CENTER, n=settle_frames + CFG.calibration_required_samples - 1)
    c = target(st, T.CENTER)
    assert c.state == S.COLLECTING and c.samples == CFG.calibration_required_samples - 1
    feeder.feed([synthetic_face(**FACES[T.CENTER])], 1)
    assert target(analyzer.calibration_state(), T.CENTER).state == S.OK


def test_time_alone_never_completes_a_target(analyzer, feeder):
    analyzer.calibration_start()
    analyzer.calibration_target(T.CENTER)
    feeder.feed([], 300)  # 20 s of frames without a face
    c = target(analyzer.calibration_state(), T.CENTER)
    assert c.state == S.FAILED and c.message_code == "no_face" and c.samples == 0


def test_multiple_faces_are_rejected(analyzer, feeder):
    analyzer.calibration_start()
    analyzer.calibration_target(T.CENTER)
    second = synthetic_face(center=(0.85, 0.3), face_height=0.2)
    feeder.feed([synthetic_face(**FACES[T.CENTER]), second], 12)
    c = target(analyzer.calibration_state(), T.CENTER)
    assert c.state == S.COLLECTING and c.samples == 0 and c.message_code == "multiple_faces"
    feeder.feed([synthetic_face(**FACES[T.CENTER]), second], 200)
    c = target(analyzer.calibration_state(), T.CENTER)
    assert c.state == S.FAILED and c.message_code == "multiple_faces"


def test_retry_after_failure(analyzer, feeder):
    analyzer.calibration_start()
    analyzer.calibration_target(T.CENTER)
    feeder.feed([], 200)
    assert target(analyzer.calibration_state(), T.CENTER).state == S.FAILED
    st = run_target(analyzer, feeder, T.CENTER)
    assert target(st, T.CENTER).state == S.OK


def test_blinks_are_not_samples(analyzer, feeder):
    analyzer.calibration_start()
    analyzer.calibration_target(T.CENTER)
    feeder.feed([synthetic_face(blink=True, **FACES[T.CENTER])], 15)
    c = target(analyzer.calibration_state(), T.CENTER)
    assert c.samples == 0 and c.message_code == "eyes_closed"


def test_dark_face_is_rejected(analyzer, feeder):
    analyzer.calibration_start()
    analyzer.calibration_target(T.CENTER)
    feeder.feed([synthetic_face(**FACES[T.CENTER])], 15, image=textured_image(luma=12.0, noise=4.0))
    c = target(analyzer.calibration_state(), T.CENTER)
    assert c.samples == 0 and c.message_code in ("low_light", "low_quality")


def test_unstable_fixation_fails(analyzer, feeder):
    analyzer.calibration_start()
    analyzer.calibration_target(T.CENTER)
    for i in range(PER_TARGET):
        feeder.feed([synthetic_face(yaw=(-12.0 if i % 2 else 12.0), pitch=-8.0)], 1)
    c = target(analyzer.calibration_state(), T.CENTER)
    assert c.state == S.FAILED and c.message_code == "unstable_fixation"


def test_wrong_side_and_not_distinct(analyzer, feeder):
    analyzer.calibration_start()
    run_target(analyzer, feeder, T.CENTER)
    st = run_target(analyzer, feeder, T.LEFT, [synthetic_face(**FACES[T.RIGHT])])  # looked the wrong way
    assert target(st, T.LEFT).state == S.FAILED and target(st, T.LEFT).message_code == "target_wrong_side"
    st = run_target(analyzer, feeder, T.UP, [synthetic_face(**FACES[T.CENTER])])  # did not look up
    assert target(st, T.UP).message_code == "target_not_distinct"


def test_failed_finish_then_retry_and_complete(analyzer, feeder):
    analyzer.calibration_start()
    for t in (T.CENTER, T.LEFT, T.RIGHT, T.UP):
        run_target(analyzer, feeder, t)
    st = analyzer.calibration_finish()
    assert st.phase == CalibrationPhase.FAILED and target(st, T.DOWN).state == S.FAILED
    assert target(st, T.CENTER).state == S.OK  # completed targets are kept
    st = run_target(analyzer, feeder, T.DOWN)
    assert st.phase == CalibrationPhase.COLLECTING and target(st, T.DOWN).state == S.OK
    assert analyzer.calibration_finish().phase == CalibrationPhase.COMPLETED
    assert analyzer.calibration_finish().phase == CalibrationPhase.COMPLETED  # idempotent


def test_target_switch_interrupts_collecting_target(analyzer, feeder):
    analyzer.calibration_start()
    analyzer.calibration_target(T.CENTER)
    feeder.feed([synthetic_face(**FACES[T.CENTER])], 10)
    st = analyzer.calibration_target(T.LEFT)
    assert target(st, T.CENTER).state == S.PENDING and target(st, T.CENTER).message_code == "interrupted"
    assert st.current_target == T.LEFT


def test_cancel_skip_and_session_end_drop_data(analyzer, feeder):
    calibrate(analyzer, feeder)
    st = analyzer.calibration_cancel()
    assert st.phase == CalibrationPhase.CANCELLED and all(x.samples == 0 for x in st.targets)
    obs = feeder.feed([synthetic_face(**FACES[T.CENTER])], 1)[-1]
    assert not obs.gaze.calibrated and "uncalibrated" in obs.quality_flags
    st = analyzer.calibration_skip("operator: no time")
    assert st.phase == CalibrationPhase.SKIPPED and st.message_code == "skipped_by_operator"
    calibrate(analyzer, feeder)
    analyzer.end_session()
    st = analyzer.calibration_state()
    assert st.phase == CalibrationPhase.NOT_STARTED and st.calibration_id is None
    assert analyzer._calib.model() is None
    analyzer.end_session()  # idempotent


def test_select_requires_started_calibration_and_session(analyzer, tmp_path):
    with pytest.raises(InvalidStateError):
        analyzer.calibration_target(T.CENTER)
    analyzer.end_session()
    with pytest.raises(InvalidStateError):
        analyzer.calibration_start()
    analyzer.start_session("sess-new", SourceMode.REPLAY)
    assert analyzer.calibration_start().phase == CalibrationPhase.COLLECTING


def test_restart_calibration_resets(analyzer, feeder):
    st1 = calibrate(analyzer, feeder)
    st2 = analyzer.calibration_start()
    assert st2.phase == CalibrationPhase.COLLECTING and st2.calibration_id != st1.calibration_id
    assert all(x.state == S.PENDING for x in st2.targets)
    assert not feeder.feed([synthetic_face(**FACES[T.CENTER])], 1)[-1].gaze.calibrated


def test_api_threads_and_consumer_thread_concurrently(analyzer, feeder):
    errors = []
    stop = threading.Event()

    def api():
        try:
            while not stop.is_set():
                analyzer.calibration_state()
                analyzer.calibration_target(T.CENTER)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    analyzer.calibration_start()
    th = [threading.Thread(target=api) for _ in range(3)]
    for x in th:
        x.start()
    try:
        feeder.feed([synthetic_face(**FACES[T.CENTER])], 120)
    finally:
        stop.set()
        for x in th:
            x.join(5)
    assert not errors
    st = analyzer.calibration_state()
    assert st.phase == CalibrationPhase.COLLECTING and target(st, T.CENTER).samples <= CFG.calibration_required_samples


def test_up_is_optional_on_laptop_webcam(analyzer, feeder):
    """Looking at the top edge barely differs from the center on a webcam above the screen:
    a failed "up" must not fail the calibration or block "down" (live finding 2026-10-08)."""
    analyzer.calibration_start()
    for t in (T.CENTER, T.LEFT, T.RIGHT):
        run_target(analyzer, feeder, t)
    st = run_target(analyzer, feeder, T.UP, [synthetic_face(**FACES[T.CENTER])])  # up not distinct
    assert target(st, T.UP).state == S.FAILED and target(st, T.UP).message_code == "target_not_distinct"
    run_target(analyzer, feeder, T.DOWN)
    st = analyzer.calibration_finish()
    assert st.phase == CalibrationPhase.COMPLETED
    assert target(st, T.DOWN).state == S.OK


def test_up_never_collected_still_completes(analyzer, feeder):
    analyzer.calibration_start()
    for t in (T.CENTER, T.LEFT, T.RIGHT, T.DOWN):
        run_target(analyzer, feeder, t)
    assert analyzer.calibration_finish().phase == CalibrationPhase.COMPLETED
