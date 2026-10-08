"""A13 identity analyzer on stub FaceDetectorYN/FaceRecognizerSF: enrollment, matching, unknowns, health.
These tests say nothing about face-recognition accuracy (see test_real_model.py / handoffs/A13 LIVE)."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter

from proctor.identity import IdentityAnalyzer, IdentityConfig, create_identity_analyzer
from proctor.identity.tests.helpers import SESSION, make_frame, make_image, stub_engine
from proctor.settings import Settings
from proctor_contracts.v1 import Component, HealthStatus, IdentityObservation, Observation, ObservationStatus, SignalState, SourceMode

OBS = TypeAdapter(Observation)
FRAME_MS = 100.0  # 10 fps camera; the analyzer decides its own cadence


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "models")


def _analyzer(settings, **engine_kw):
    engine, det, rec = stub_engine(**engine_kw)
    analyzer = IdentityAnalyzer(settings, engine=engine)
    assert analyzer.load().code == "model_loaded"
    analyzer.start_session(SESSION, SourceMode.REPLAY)
    return analyzer, det, rec


class Feed:
    """Feeds frames at FRAME_MS and collects the observations the analyzer chose to emit."""

    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.frame_id = 0
        self.t = 0.0

    def run(self, seconds: float, person: int = 1, faces: int = 1) -> list[IdentityObservation]:
        out: list[IdentityObservation] = []
        image = make_image(person, faces)
        end = self.t + seconds * 1000
        while self.t < end:
            for obs in self.analyzer.process(make_frame(self.frame_id, self.t, image)):
                OBS.validate_python(obs.model_dump(mode="json"))  # contract round-trip
                out.append(obs)
            self.frame_id += 1
            self.t += FRAME_MS
        return out


def _enroll(analyzer) -> Feed:
    feed = Feed(analyzer)
    analyzer.exam_started(0.0)
    feed.run(3.0, person=1)
    assert analyzer.health().details["enrolled"] is True
    return feed


# ------------------------------------------------------------------------------------- enrollment
def test_reference_is_built_from_5_good_frames_in_first_3_seconds(settings):
    analyzer, _, _ = _analyzer(settings)
    analyzer.exam_started(0.0)
    obs = Feed(analyzer).run(3.0, person=1)
    times = [o.t_session_ms for o in obs]
    assert times[:5] == [0.0, 500.0, 1000.0, 1500.0, 2000.0]  # 2 Hz while enrolling
    for o in obs[:4]:
        assert (o.enrolled, o.same_person, o.similarity) == (False, SignalState.UNKNOWN, None)
        assert o.reasons == ["enrolling"]
    fifth = obs[4]
    assert fifth.enrolled is True and fifth.same_person == SignalState.PRESENT and "enrolled_now" in fifth.reasons
    assert fifth.similarity is not None and fifth.similarity > 0.9
    assert all(b - a >= 1000.0 for a, b in zip(times[4:], times[5:]))  # then 1 Hz


def test_after_enrollment_analysis_runs_at_most_once_per_second(settings):
    analyzer, det, _ = _analyzer(settings)
    feed = _enroll(analyzer)
    calls_before = det.calls
    obs = feed.run(10.0, person=1)
    assert len(obs) == 10 and det.calls - calls_before == 10
    gaps = [b.t_session_ms - a.t_session_ms for a, b in zip(obs, obs[1:])]
    assert min(gaps) >= 1000.0
    assert all(o.same_person == SignalState.PRESENT and o.enrolled and o.status == ObservationStatus.OK for o in obs)


def test_no_reference_without_faces(settings):
    analyzer, _, rec = _analyzer(settings)
    analyzer.exam_started(0.0)
    obs = Feed(analyzer).run(6.0, person=1, faces=0)
    assert obs and all(o.same_person == SignalState.UNKNOWN and not o.enrolled for o in obs)
    assert all(o.reasons == ["no_face"] for o in obs)
    assert rec.feature_calls == 0 and analyzer.health().details["enrolled"] is False


def test_no_reference_with_several_faces_or_small_face(settings):
    analyzer, _, _ = _analyzer(settings)
    analyzer.exam_started(0.0)
    obs = Feed(analyzer).run(4.0, person=1, faces=2)
    assert all(o.reasons == ["multiple_faces"] and o.same_person == SignalState.UNKNOWN and not o.enrolled for o in obs)
    assert all("multiple_faces" in o.quality_flags for o in obs)

    small, _, _ = _analyzer(settings, face_px=30.0)
    small.exam_started(0.0)
    obs = Feed(small).run(4.0, person=1)
    assert all(o.reasons == ["face_too_small"] and not o.enrolled for o in obs)


def test_no_reference_before_exam_start(settings):
    """Frames during preflight/calibration (before RUNNING) never build the reference."""
    analyzer, det, _ = _analyzer(settings)
    feed = Feed(analyzer)
    obs = feed.run(5.0, person=2)
    assert all(o.reasons == ["exam_not_started"] and not o.enrolled for o in obs)
    assert det.calls == 0  # models are not even run before the exam starts
    gaps = [b.t_session_ms - a.t_session_ms for a, b in zip(obs, obs[1:])]
    assert min(gaps) >= 1000.0
    analyzer.exam_started(feed.t)
    obs = feed.run(3.0, person=1)
    assert any(o.enrolled for o in obs)


def test_enrollment_delayed_is_flagged_then_enrolls_late(settings):
    analyzer, _, _ = _analyzer(settings)
    analyzer.exam_started(0.0)
    feed = Feed(analyzer)
    feed.run(4.0, faces=0)
    obs = feed.run(1.0, person=1)  # first good face only after the 3 s window
    assert obs[0].reasons == ["enrolling", "enrollment_delayed"]
    obs = feed.run(3.0, person=1)
    assert any(o.enrolled for o in obs)


def test_reference_is_not_a_blend_of_two_people(settings):
    """A different person in the middle of enrollment is rejected from the reference."""
    analyzer, _, _ = _analyzer(settings)
    analyzer.exam_started(0.0)
    feed = Feed(analyzer)
    feed.run(1.0, person=1)  # 2 samples of A
    feed.run(0.5, person=2)  # 1 sample of B
    obs = feed.run(1.5, person=1)  # 3 more samples of A
    assert analyzer.health().details["enroll_rejected"] >= 1
    assert obs[-1].enrolled and obs[-1].same_person == SignalState.PRESENT
    later = feed.run(2.0, person=2)
    assert all(o.same_person == SignalState.ABSENT for o in later)


# --------------------------------------------------------------------------------------- matching
def test_other_person_gives_absent_with_similarity(settings):
    analyzer, _, _ = _analyzer(settings)
    feed = _enroll(analyzer)
    obs = feed.run(5.0, person=2)
    assert len(obs) == 5
    for o in obs:
        assert o.same_person == SignalState.ABSENT and o.enrolled is True
        assert o.similarity is not None and o.similarity < IdentityConfig().match_threshold
        assert o.reasons == ["below_threshold"] and o.status == ObservationStatus.OK
    back = feed.run(2.0, person=1)
    assert all(o.same_person == SignalState.PRESENT and o.similarity >= 0.363 for o in back)


def test_no_face_or_several_faces_after_enrollment_is_unknown(settings):
    analyzer, _, _ = _analyzer(settings)
    feed = _enroll(analyzer)
    for faces, reason in ((0, "no_face"), (2, "multiple_faces")):
        obs = feed.run(3.0, person=1, faces=faces)
        assert obs and all(o.same_person == SignalState.UNKNOWN and o.similarity is None for o in obs)
        assert all(o.reasons == [reason] and o.enrolled is True for o in obs)


def test_threshold_is_the_opencv_sface_cosine():
    assert IdentityConfig().match_threshold == 0.363
    with pytest.raises(ValueError):
        IdentityConfig.from_env({"QORGAU_IDENTITY_INTERVAL_MS": "200"})  # never more often than 1/s


# ------------------------------------------------------------------------------- privacy/lifecycle
def test_only_similarity_leaves_the_module(settings, tmp_path):
    analyzer, _, _ = _analyzer(settings)
    feed = _enroll(analyzer)
    obs = feed.run(2.0, person=2)
    dumped = json.dumps([o.model_dump(mode="json") for o in obs])
    payload = json.loads(dumped)[0]
    assert set(payload) == set(IdentityObservation.model_fields)  # contract fields only, no embedding
    assert all(not isinstance(v, list) or len(v) <= 16 for v in payload.values())
    assert list(tmp_path.rglob("*")) == []  # nothing written to disk


def test_end_session_drops_reference(settings):
    analyzer, _, _ = _analyzer(settings)
    _enroll(analyzer)
    analyzer.end_session()
    assert analyzer.health().details["enrolled"] is False
    analyzer.start_session("s-2", SourceMode.REPLAY)
    out = analyzer.process(make_frame(0, 0.0, make_image(1), session_id="s-2"))
    assert out[0].enrolled is False and out[0].reasons == ["exam_not_started"]


def test_other_session_frames_are_ignored(settings):
    analyzer, _, _ = _analyzer(settings)
    assert analyzer.process(make_frame(0, 0.0, session_id="other")) == []
    assert analyzer.health().details["session_mismatch"] == 1


def test_resume_does_not_re_enroll(settings):
    analyzer, _, _ = _analyzer(settings)
    feed = _enroll(analyzer)
    analyzer.exam_started(feed.t)  # A01 calls it on RUNNING only once, but a second call must be harmless
    obs = feed.run(3.0, person=2)
    assert all(o.same_person == SignalState.ABSENT for o in obs)


# ------------------------------------------------------------------------------------------ health
def test_missing_model_is_health_and_unknown_never_present(settings):
    analyzer = create_identity_analyzer(settings)  # empty models dir
    health = analyzer.load()
    assert health.component == Component.IDENTITY
    assert health.status == HealthStatus.UNAVAILABLE and health.code == "model_missing"
    assert "prepare --download" in health.message and str(settings.models_dir) not in health.message
    analyzer.start_session(SESSION, SourceMode.LIVE)
    analyzer.exam_started(0.0)
    obs = Feed(analyzer).run(6.0, person=1)
    assert len(obs) == 6  # still 1 Hz, so the gap is visible
    assert all(o.same_person == SignalState.UNKNOWN and o.reasons == ["model_missing"] for o in obs)
    assert all(o.status == ObservationStatus.ERROR and not o.enrolled for o in obs)
    assert analyzer.health().code == "model_missing"


def test_invalid_env_config_is_health_not_crash(settings, monkeypatch):
    monkeypatch.setenv("QORGAU_IDENTITY_MATCH_THRESHOLD", "abc")
    analyzer = create_identity_analyzer(settings)
    assert analyzer.load().code == "config_invalid"


def test_engine_error_becomes_error_observation(settings):
    analyzer, det, _ = _analyzer(settings)
    analyzer.exam_started(0.0)

    def boom(image):
        raise RuntimeError("cv2 failed")

    det.detect = boom
    obs = Feed(analyzer).run(1.0)
    assert obs[0].status == ObservationStatus.ERROR and obs[0].same_person == SignalState.UNKNOWN
    assert obs[0].reasons == ["analyzer_error", "runtimeerror"]
    assert analyzer.health().details["errors"] >= 1
