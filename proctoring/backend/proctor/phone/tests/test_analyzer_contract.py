"""PhoneAnalyzer as a FrameAnalyzer: wire contract, frame validation, ordering, lifecycle, health (owner: A03).

FAKE DETECTIONS ONLY. Every box in this file is scripted by ``FakeDetector`` (or a tiny subclass of it);
no model runs here. These tests check the analyzer's plumbing: that every emitted PhoneObservation is a
valid ``qorgau.v1`` record (Pydantic AND the generated JSON Schema), that metadata is copied from the
frame, that bad input never raises, that duplicate / out-of-order / foreign-session frames are dropped,
that health reflects failures, that frames are never written to and that no camera/network is touched.
They say NOTHING about how well YOLO11n finds phones (see the ``real_model`` tests for that).

A phone in the frame is not evidence that anything was photographed: ``possible_screen_capture`` is at
most a pattern and is checked here to never be ``absent`` and never use wording that claims a photo.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import socket
import threading
import urllib.request
from dataclasses import replace
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from pydantic import TypeAdapter

from proctor.phone import PHONE_MODULE_VERSION, create_phone_analyzer
from proctor.phone.analyzer import PhoneAnalyzer
from proctor.phone.config import PhoneConfig
from proctor.phone.detector import RawDetection
from proctor.phone.tests.helpers import FRAME_MS, SESSION, FakeDetector, box, make_frame, make_image
from proctor.settings import PROCTORING_ROOT
from proctor_contracts.interfaces import FrameAnalyzer, FramePacket
from proctor_contracts.v1 import (
    Component,
    Health,
    HealthStatus,
    Observation,
    ObservationStatus,
    PhoneObservation,
    PhoneSignalName,
    SignalState,
    SourceMode,
)

CFG = PhoneConfig()
IMG = make_image()  # textured, usable, read-only
ALL_SIGNALS = {PhoneSignalName.PHONE_VISIBLE, PhoneSignalName.PHONE_RAISED, PhoneSignalName.POSSIBLE_SCREEN_CAPTURE}
MAX_DETECTIONS = 16  # PhoneObservation.detections max_length (contract)

_SCHEMA = json.loads((PROCTORING_ROOT / "contracts" / "schema" / "v1" / "qorgau.v1.schema.json").read_text(encoding="utf-8"))


def _schema_validator(name: str) -> jsonschema.Draft202012Validator:
    """Same construction as A01's contracts/tests/test_contracts.py."""
    schema = {"$schema": _SCHEMA["$schema"], "$defs": _SCHEMA["$defs"], "$ref": f"#/$defs/{name}"}
    return jsonschema.Draft202012Validator(schema)


PHONE_OBS_SCHEMA = _schema_validator("PhoneObservation")
HEALTH_SCHEMA = _schema_validator("Health")
OBSERVATION_UNION = TypeAdapter(Observation)

# Wording that would turn a phone detection into a claim about a photo / a verdict.
_PHOTO_CLAIM = re.compile(r"photo|picture|screenshot|snapshot|taken|took|captured|cheat|violation|guilt")


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_phone_env(monkeypatch):
    """create_phone_analyzer() reads QORGAU_PHONE_*; keep the developer's shell out of these tests."""
    import os

    for key in list(os.environ):
        if key.startswith("QORGAU_PHONE_"):
            monkeypatch.delenv(key, raising=False)


def loaded(settings, detector=None, config: PhoneConfig | None = None, *, session: str = SESSION, mode: SourceMode = SourceMode.REPLAY) -> PhoneAnalyzer:
    analyzer = PhoneAnalyzer(settings, config or CFG, detector=detector if detector is not None else FakeDetector())
    health = analyzer.load()
    assert health.status == HealthStatus.OK and health.code == "model_loaded"
    analyzer.start_session(session, mode)
    return analyzer


def check_contract(obs, frame: FramePacket, analyzer: PhoneAnalyzer) -> PhoneObservation:
    """Every invariant an emitted PhoneObservation must satisfy, whatever its status."""
    assert isinstance(obs, PhoneObservation)
    dumped = obs.model_dump(mode="json")
    # Pydantic round trip + discriminated union + generated JSON Schema
    assert PhoneObservation.model_validate(dumped) == obs
    assert isinstance(OBSERVATION_UNION.validate_python(dumped), PhoneObservation)
    PHONE_OBS_SCHEMA.validate(dumped)
    PHONE_OBS_SCHEMA.validate(json.loads(obs.model_dump_json()))
    assert dumped["kind"] == "phone"

    meta = frame.meta
    assert obs.observation_id == f"phone-{meta.frame_id}"
    assert obs.session_id == meta.session_id
    assert obs.frame_id == meta.frame_id
    assert obs.t_session_ms == meta.t_session_ms
    assert obs.wall_time == meta.wall_time
    assert obs.source_mode == meta.source_mode

    assert obs.producer.module == "phone"
    assert obs.producer.version == PHONE_MODULE_VERSION
    assert obs.producer.config_version == analyzer.config.config_version
    assert obs.latency_ms is not None and obs.latency_ms >= 0.0

    assert len(obs.signals) == 3
    assert {s.name for s in obs.signals} == ALL_SIGNALS
    assert len(obs.detections) <= MAX_DETECTIONS
    for det in obs.detections:
        b = det.bbox
        assert 0.0 <= b.x_min <= b.x_max <= 1.0
        assert 0.0 <= b.y_min <= b.y_max <= 1.0
        assert 0.0 <= det.confidence <= 1.0
    assert len(obs.quality_flags) <= 16
    assert len(set(obs.quality_flags)) == len(obs.quality_flags)

    for s in obs.signals:
        if s.name == PhoneSignalName.POSSIBLE_SCREEN_CAPTURE:
            assert s.state != SignalState.ABSENT, "the webcam cannot rule out a capture: never 'absent'"
        assert not _PHOTO_CLAIM.search(s.reason), s.reason
        assert not any(_PHOTO_CLAIM.search(k) for k in s.facts), s.facts

    if obs.status == ObservationStatus.ERROR:
        assert obs.detections == []
        assert obs.quality is None
        assert all(s.state == SignalState.UNKNOWN for s in obs.signals)
    return obs


def run_one(analyzer: PhoneAnalyzer, frame: FramePacket) -> PhoneObservation:
    out = analyzer.process(frame)
    assert isinstance(out, (list, tuple)) and len(out) == 1, out
    return check_contract(out[0], frame, analyzer)


def signal(obs: PhoneObservation, name: PhoneSignalName):
    return next(s for s in obs.signals if s.name == name)


def grid_boxes(n: int) -> list[RawDetection]:
    """n non-overlapping fake phone boxes spread over the frame, decreasing scores."""
    out = []
    for k in range(n):
        i, j = k % 5, k // 5
        out.append(box(0.1 + 0.2 * i, 0.12 + 0.22 * j, w=0.1, h=0.12, conf=round(0.9 - 0.02 * k, 3)))
    return out


# --------------------------------------------------------------------------------------------
# protocol / factory
# --------------------------------------------------------------------------------------------
def test_factory_returns_frame_analyzer_named_phone(settings):
    analyzer = create_phone_analyzer(settings)
    assert isinstance(analyzer, FrameAnalyzer)
    assert analyzer.name == "phone"
    assert analyzer.config.config_version == CFG.config_version  # env cleared -> defaults
    fake = PhoneAnalyzer(settings, CFG, detector=FakeDetector())
    assert isinstance(fake, FrameAnalyzer) and fake.name == "phone"


def test_factory_is_cheap_and_health_before_load_is_starting(settings):
    analyzer = create_phone_analyzer(settings)
    h = analyzer.health()
    assert h.component == Component.PHONE
    assert h.status == HealthStatus.STARTING and h.code == "not_loaded"
    HEALTH_SCHEMA.validate(h.model_dump(mode="json"))


def test_missing_model_is_unavailable_and_frames_yield_error_observations(settings):
    """Empty models_dir: load() reports model_missing (never raises/downloads); process() stays contract-valid."""
    analyzer = create_phone_analyzer(settings)
    h = analyzer.load()
    assert h.status == HealthStatus.UNAVAILABLE and h.code == "model_missing"
    assert "prepare" in h.message  # tells the operator how to install the weights offline
    HEALTH_SCHEMA.validate(h.model_dump(mode="json"))
    analyzer.start_session(SESSION, SourceMode.REPLAY)
    for i in range(3):
        frame = make_frame(i, image=IMG)
        obs = run_one(analyzer, frame)
        assert obs.status == ObservationStatus.ERROR
        assert obs.quality_flags == ["model_unavailable"]
        assert all(s.reason == "model_unavailable" for s in obs.signals)
        assert obs.producer.model_id is None and obs.producer.model_sha256 is None
    assert analyzer.health().status == HealthStatus.UNAVAILABLE  # not upgraded to "degraded"/"ok"


def test_fake_detector_is_labelled_as_fake_in_health_and_producer(settings):
    analyzer = PhoneAnalyzer(settings, CFG, detector=FakeDetector())
    h = analyzer.load()
    assert h.details.get("fake_detector") is True
    assert "TEST" in h.message
    analyzer.start_session(SESSION, SourceMode.REPLAY)
    obs = run_one(analyzer, make_frame(0, image=IMG))
    assert obs.producer.model_id == "fake-detector"  # never mistaken for the real model
    assert obs.producer.model_sha256 is None


# --------------------------------------------------------------------------------------------
# contract of normal observations
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", list(SourceMode))
def test_observations_copy_frame_metadata_and_validate(settings, mode):
    det = FakeDetector(script=lambda i: [box(0.5, 0.35 - 0.01 * (i % 3), conf=0.45)] if i % 4 else [])
    analyzer = loaded(settings, det, mode=mode)
    for i in range(12):
        frame = make_frame(i, image=IMG, mode=mode)
        obs = run_one(analyzer, frame)
        assert obs.status in (ObservationStatus.OK, ObservationStatus.DEGRADED)
        assert obs.quality is not None and 0.0 <= obs.quality <= 1.0
        for d in obs.detections:
            assert d.class_name == "cell phone" and d.class_index == 67
            assert d.track_id is not None and d.track_id.startswith("ph-")
    assert det.calls == 12


def test_no_detection_on_usable_frame_is_absent_not_unknown(settings):
    analyzer = loaded(settings)
    obs = run_one(analyzer, make_frame(0, image=IMG))
    assert obs.status == ObservationStatus.OK and obs.quality_flags == []
    assert signal(obs, PhoneSignalName.PHONE_VISIBLE).state == SignalState.ABSENT
    assert signal(obs, PhoneSignalName.PHONE_RAISED).state == SignalState.ABSENT
    assert signal(obs, PhoneSignalName.POSSIBLE_SCREEN_CAPTURE).state == SignalState.INSUFFICIENT_EVIDENCE


def test_unusable_frame_is_unknown_never_absent(settings):
    """Unknown != absent (CONTRACTS.md): a black frame must not read as 'no phone'."""
    analyzer = loaded(settings)
    obs = run_one(analyzer, make_frame(0, image=make_image(value=0)))
    assert obs.status == ObservationStatus.UNKNOWN
    assert all(s.state == SignalState.UNKNOWN for s in obs.signals)
    assert obs.quality == 0.0


def test_at_most_16_detections_even_if_detector_returns_20(settings):
    boxes = grid_boxes(20)
    assert len(boxes) == 20 and all(0.0 <= v <= 1.0 for b in boxes for v in (b.x_min, b.y_min, b.x_max, b.y_max))
    det = FakeDetector(script=lambda i: boxes)
    analyzer = loaded(settings, det)
    for i in range(4):
        obs = run_one(analyzer, make_frame(i, image=IMG))
        assert len(obs.detections) == MAX_DETECTIONS
        # the analyzer keeps the detector's (score) order and drops the tail
        assert [d.confidence for d in obs.detections] == [round(b.confidence, 4) for b in boxes[:MAX_DETECTIONS]]
        ids = [d.track_id for d in obs.detections if d.track_id is not None]
        assert len(ids) == len(set(ids)) <= CFG.max_tracks
        assert signal(obs, PhoneSignalName.PHONE_VISIBLE).facts["detections"] == MAX_DETECTIONS


def test_detector_box_outside_unit_range_never_raises(settings):
    """A misbehaving (injected) detector must not crash the consumer thread: the box cannot become a
    contract BBox, so the frame is reported as an analyzer error instead of an invalid record."""
    bad = RawDetection(-0.2, 0.1, 1.3, 0.5, 0.6, 67, "cell phone")
    det = FakeDetector(script={0: [bad]})
    analyzer = loaded(settings, det)
    obs = run_one(analyzer, make_frame(0, image=IMG))
    assert obs.status == ObservationStatus.ERROR and obs.quality_flags == ["analyzer_error"]
    assert all(s.reason == "inference_error" for s in obs.signals)
    assert analyzer.runtime_stats()["errors"] == 1
    obs = run_one(analyzer, make_frame(1, image=IMG))
    assert obs.status == ObservationStatus.OK


# --------------------------------------------------------------------------------------------
# invalid frames
# --------------------------------------------------------------------------------------------
INVALID_IMAGES = [
    pytest.param(lambda: np.zeros((480, 640), dtype=np.uint8), "image_not_hxwx3", id="gray_2d"),
    pytest.param(lambda: np.zeros((480, 640, 4), dtype=np.uint8), "image_not_hxwx3", id="bgra_4ch"),
    pytest.param(lambda: np.zeros((480, 640, 1), dtype=np.uint8), "image_not_hxwx3", id="single_channel_3d"),
    pytest.param(lambda: np.zeros((480, 640, 3, 1), dtype=np.uint8), "image_not_hxwx3", id="rank_4"),
    pytest.param(lambda: np.zeros((480, 640, 3), dtype=np.float32), "image_dtype_not_uint8", id="float32"),
    pytest.param(lambda: np.zeros((480, 640, 3), dtype=np.uint16), "image_dtype_not_uint8", id="uint16"),
    pytest.param(lambda: np.zeros((0, 0, 3), dtype=np.uint8), "image_too_small", id="empty_0x0x3"),
    pytest.param(lambda: np.full((8, 8, 3), 128, dtype=np.uint8), "image_too_small", id="tiny_8x8x3"),
    pytest.param(lambda: np.full((480, 8, 3), 128, dtype=np.uint8), "image_too_small", id="narrow_480x8x3"),
    pytest.param(lambda: [[[0, 0, 0]] * 640] * 480, "image_not_ndarray", id="python_list"),
    pytest.param(lambda: None, "image_not_ndarray", id="none"),
]


@pytest.mark.parametrize("make_bad,detail", INVALID_IMAGES)
def test_invalid_frame_yields_one_error_observation_and_never_raises(settings, make_bad, detail):
    det = FakeDetector(script=lambda i: [box(0.5, 0.4)])
    analyzer = loaded(settings, det)
    frame = FramePacket(meta=make_frame(0).meta, image=make_bad())  # FramePacket does not validate pixels
    obs = run_one(analyzer, frame)
    assert obs.status == ObservationStatus.ERROR
    assert obs.quality_flags == ["invalid_frame"]
    assert all(s.state == SignalState.UNKNOWN and s.reason == "invalid_frame" for s in obs.signals)
    assert all(s.facts.get("detail") == detail for s in obs.signals)
    assert det.calls == 0  # the detector never sees a malformed image
    stats = analyzer.runtime_stats()
    assert stats["invalid_frames"] == 1 and stats["processed"] == 0
    # the stream continues normally afterwards
    ok = run_one(analyzer, make_frame(1, image=IMG))
    assert ok.status != ObservationStatus.ERROR and det.calls == 1


def test_smallest_accepted_frame_is_processed(settings):
    analyzer = loaded(settings)
    tiny = make_image(16, 16)
    obs = run_one(analyzer, make_frame(0, image=tiny))
    assert obs.status != ObservationStatus.ERROR


# --------------------------------------------------------------------------------------------
# ordering / session isolation
# --------------------------------------------------------------------------------------------
def test_duplicate_and_out_of_order_frames_are_dropped_and_counted(settings):
    det = FakeDetector()
    analyzer = loaded(settings, det)
    for i in range(3):
        run_one(analyzer, make_frame(i, image=IMG))
    assert analyzer.process(make_frame(2, image=IMG)) == []  # duplicate frame_id
    assert analyzer.process(make_frame(1, image=IMG)) == []  # decreasing frame_id
    assert analyzer.process(make_frame(3, t_ms=1 * FRAME_MS, image=IMG)) == []  # newer id, older time
    stats = analyzer.runtime_stats()
    assert stats["dropped_out_of_order"] == 3
    assert stats["processed"] == 3 and det.calls == 3  # dropped frames never reach the detector
    run_one(analyzer, make_frame(3, image=IMG))
    run_one(analyzer, make_frame(4, t_ms=3 * FRAME_MS, image=IMG))  # same t, higher id: not out of order
    assert analyzer.runtime_stats()["dropped_out_of_order"] == 3
    assert analyzer.runtime_stats()["processed"] == 5


def test_frame_from_another_session_is_dropped(settings):
    det = FakeDetector()
    analyzer = loaded(settings, det)
    run_one(analyzer, make_frame(0, image=IMG))
    assert analyzer.process(make_frame(100, image=IMG, session_id="s-other")) == []
    stats = analyzer.runtime_stats()
    assert stats["session_mismatch"] == 1 and det.calls == 1
    run_one(analyzer, make_frame(1, image=IMG))  # the foreign frame did not advance the frame_id cursor


# --------------------------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------------------------
def test_start_session_resets_tracks_counters_and_frame_cursor(settings):
    # two separated phones -> ph-1 / ph-2; a low score so phone_visible needs track confirmation
    det = FakeDetector(script=lambda i: [box(0.3, 0.4, conf=0.4), box(0.7, 0.4, conf=0.4)])
    analyzer = loaded(settings, det)
    first = run_one(analyzer, make_frame(0, image=IMG))
    assert signal(first, PhoneSignalName.PHONE_VISIBLE).state == SignalState.UNKNOWN  # unconfirmed
    for i in range(1, 5):
        obs = run_one(analyzer, make_frame(i, image=IMG))
    assert sorted(d.track_id for d in obs.detections) == ["ph-1", "ph-2"]
    assert signal(obs, PhoneSignalName.PHONE_VISIBLE).state == SignalState.PRESENT
    assert analyzer.runtime_stats()["tracks_created"] == 2

    analyzer.start_session("s-phone-test-2", SourceMode.REPLAY)
    stats = analyzer.runtime_stats()
    assert stats["live_tracks"] == 0 and stats["tracks_created"] == 0 and stats["processed"] == 0
    new = run_one(analyzer, make_frame(0, image=IMG, session_id="s-phone-test-2"))  # frame_id 0 again is fine
    assert sorted(d.track_id for d in new.detections) == ["ph-1", "ph-2"]  # ids restart per session
    assert signal(new, PhoneSignalName.PHONE_VISIBLE).state == SignalState.UNKNOWN  # no carried-over confirmation
    assert analyzer.process(make_frame(5, image=IMG)) == []  # old session's frames are now foreign
    assert analyzer.runtime_stats()["session_mismatch"] == 1


class _ClosingDetector(FakeDetector):
    closed = 0

    def close(self) -> None:
        self.closed += 1


def test_end_session_and_close_are_idempotent(settings):
    det = _ClosingDetector(script=lambda i: [box(0.5, 0.4)])
    analyzer = loaded(settings, det)
    for i in range(3):
        run_one(analyzer, make_frame(i, image=IMG))
    analyzer.end_session()
    analyzer.end_session()
    stats = analyzer.runtime_stats()
    assert stats["live_tracks"] == 0 and stats["processed"] == 0
    analyzer.start_session(SESSION, SourceMode.REPLAY)  # restart after end works
    assert run_one(analyzer, make_frame(0, image=IMG)).detections[0].track_id == "ph-1"

    analyzer.close()
    analyzer.close()
    assert det.closed == 1  # the detector is released exactly once
    h = analyzer.health()
    assert h.status == HealthStatus.STOPPED and h.code == "closed"
    HEALTH_SCHEMA.validate(h.model_dump(mode="json"))
    analyzer.end_session()  # still safe after close
    # a late frame after close() still never raises and stays contract-valid
    late = run_one(analyzer, make_frame(9, image=IMG))
    assert late.status == ObservationStatus.ERROR and late.quality_flags == ["model_unavailable"]


# --------------------------------------------------------------------------------------------
# failures and health
# --------------------------------------------------------------------------------------------
def test_detector_exception_yields_error_observation(settings):
    det = FakeDetector(script=lambda i: [box(0.5, 0.4)], fail_on={1})
    analyzer = loaded(settings, det)
    run_one(analyzer, make_frame(0, image=IMG))
    obs = run_one(analyzer, make_frame(1, image=IMG))
    assert obs.status == ObservationStatus.ERROR
    assert obs.quality_flags == ["analyzer_error"]
    assert all(s.reason == "inference_error" and s.facts.get("detail") == "RuntimeError" for s in obs.signals)
    stats = analyzer.runtime_stats()
    assert stats["errors"] == 1 and stats["processed"] == 1
    after = run_one(analyzer, make_frame(2, image=IMG))
    assert after.status != ObservationStatus.ERROR
    assert after.detections[0].track_id == "ph-1"  # the failed frame did not break tracking


@pytest.mark.parametrize(
    "fail_on,expected",
    [
        (set(), HealthStatus.OK),
        ({3}, HealthStatus.OK),  # 10 %
        ({3, 7}, HealthStatus.DEGRADED),  # 20 % -> threshold reached
        (set(range(10)), HealthStatus.DEGRADED),  # everything failed
    ],
    ids=["no_failures", "10pct", "20pct", "100pct"],
)
def test_health_degrades_on_recent_inference_failures(settings, fail_on, expected):
    det = FakeDetector(fail_on=fail_on)
    analyzer = loaded(settings, det)
    for i in range(10):
        run_one(analyzer, make_frame(i, image=IMG))
    h = analyzer.health()
    HEALTH_SCHEMA.validate(h.model_dump(mode="json"))
    assert h.status == expected
    if expected == HealthStatus.DEGRADED:
        assert h.code == "inference_errors"
    else:
        assert h.code == "model_loaded"
    assert h.details["errors"] == len(fail_on)
    assert h.details["processed"] == 10 - len(fail_on)


def test_health_recovers_when_failures_leave_the_recent_window(settings):
    det = FakeDetector(fail_on=set(range(5)))
    analyzer = loaded(settings, det)
    for i in range(15):
        run_one(analyzer, make_frame(i, image=IMG))
    assert analyzer.health().status == HealthStatus.DEGRADED  # 5 of 15
    for i in range(15, 70):
        run_one(analyzer, make_frame(i, image=IMG))
    h = analyzer.health()
    assert h.status == HealthStatus.OK and h.code == "model_loaded"


# --------------------------------------------------------------------------------------------
# rate limit, quality flags
# --------------------------------------------------------------------------------------------
def test_min_interval_skips_frames_inside_the_interval(settings):
    cfg = replace(PhoneConfig(), min_interval_ms=10_000)
    assert cfg.config_version != CFG.config_version  # behaviour-relevant -> part of config_version
    det = FakeDetector()
    analyzer = loaded(settings, det, cfg)
    obs = run_one(analyzer, make_frame(0, image=IMG))
    assert obs.producer.config_version == cfg.config_version
    assert analyzer.process(make_frame(1, image=IMG)) == []
    assert analyzer.process(make_frame(2, image=IMG)) == []
    stats = analyzer.runtime_stats()
    assert stats["skipped_interval"] == 2 and stats["processed"] == 1 and det.calls == 1
    analyzer.start_session("s-phone-test-2", SourceMode.REPLAY)  # a new session is not rate-limited by the old one
    run_one(analyzer, make_frame(0, image=IMG, session_id="s-phone-test-2"))


def test_stale_flag_when_capture_age_exceeds_stale_ms(settings):
    analyzer = loaded(settings)
    fresh = run_one(analyzer, make_frame(0, image=IMG, capture_age_ms=0.0))
    assert "stale" not in fresh.quality_flags
    age = 3 * CFG.stale_ms
    stale = run_one(analyzer, make_frame(1, image=IMG, capture_age_ms=age))
    assert "stale" in stale.quality_flags
    assert stale.status == ObservationStatus.DEGRADED
    assert stale.latency_ms >= age


def test_synthetic_flag_for_synthetic_source(settings):
    analyzer = loaded(settings, mode=SourceMode.SYNTHETIC)
    obs = run_one(analyzer, make_frame(0, image=IMG, mode=SourceMode.SYNTHETIC))
    assert obs.source_mode == SourceMode.SYNTHETIC
    assert obs.quality_flags == ["synthetic"]
    assert obs.status == ObservationStatus.OK  # "synthetic" alone does not degrade the measurement
    replay = loaded(settings)
    assert "synthetic" not in run_one(replay, make_frame(0, image=IMG)).quality_flags


def test_size_mismatch_flag_when_meta_differs_from_array(settings):
    det = FakeDetector(script=lambda i: [box(0.5, 0.4)])
    analyzer = loaded(settings, det)
    obs = run_one(analyzer, make_frame(0, image=IMG, width=320, height=240))
    assert "size_mismatch" in obs.quality_flags
    assert obs.status == ObservationStatus.DEGRADED
    assert len(obs.detections) == 1
    matched = run_one(analyzer, make_frame(1, image=IMG))
    assert "size_mismatch" not in matched.quality_flags


# --------------------------------------------------------------------------------------------
# frames are shared read-only; no camera / network at runtime
# --------------------------------------------------------------------------------------------
def _digest(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


@pytest.mark.parametrize("writeable", [False, True], ids=["read_only", "writeable_copy"])
def test_frame_image_is_never_modified(settings, writeable):
    image = make_image(seed=7)
    if writeable:
        image = image.copy()
        image.flags.writeable = True
    before = _digest(image)
    det = FakeDetector(script=lambda i: [box(0.5, 0.5 - 0.02 * i), box(0.15, 0.8, conf=0.3)])
    analyzer = loaded(settings, det)
    for i in range(10):
        run_one(analyzer, make_frame(i, image=image))
    run_one(analyzer, make_frame(10, image=image, width=320, height=240))
    assert _digest(image) == before
    assert image.flags.writeable is writeable


def test_analyzer_never_opens_a_camera_or_the_network(settings, monkeypatch):
    import cv2

    calls: list[str] = []

    def forbidden(name):
        def _raise(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"phone analyzer must not call {name}")

        return _raise

    monkeypatch.setattr(cv2, "VideoCapture", forbidden("cv2.VideoCapture"))
    monkeypatch.setattr(cv2, "VideoWriter", forbidden("cv2.VideoWriter"))
    monkeypatch.setattr(urllib.request, "urlopen", forbidden("urlopen"))
    monkeypatch.setattr(socket, "create_connection", forbidden("socket.create_connection"))
    monkeypatch.setattr(socket.socket, "connect", forbidden("socket.connect"))

    # real path without weights: must fail closed, offline
    real = create_phone_analyzer(settings)
    assert real.load().code == "model_missing"
    real.start_session(SESSION, SourceMode.REPLAY)
    for i in range(3):
        run_one(real, make_frame(i, image=IMG))
    real.end_session()
    real.close()

    # full session with the fake detector
    det = FakeDetector(script=lambda i: [box(0.5, 0.6 - 0.03 * i, w=0.15, h=0.25, conf=0.7)])
    analyzer = loaded(settings, det)
    for i in range(12):
        obs = run_one(analyzer, make_frame(i, image=IMG))
        assert "analyzer_error" not in obs.quality_flags  # a swallowed forbidden call would show up here
    analyzer.end_session()
    analyzer.close()
    assert calls == []


_RUNTIME_MODULES = ["__init__.py", "analyzer.py", "config.py", "detector.py", "manifest.py", "quality.py", "signals.py", "tracker.py"]


@pytest.mark.parametrize("filename", _RUNTIME_MODULES)
def test_runtime_modules_have_no_camera_or_download_code(filename):
    """Static guard: only prepare.py (explicit CLI, not imported at runtime) may touch the network."""
    path = Path(__file__).resolve().parents[1] / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    banned_imports = {"urllib", "urllib.request", "requests", "httpx", "http.client", "socket", ".prepare", "proctor.phone.prepare"}
    assert not (imported & banned_imports), imported & banned_imports
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for banned in ("VideoCapture", "urlopen", "urlretrieve"):
        assert banned not in attrs and banned not in names, f"{filename} uses {banned}"


# --------------------------------------------------------------------------------------------
# thread-safety smoke (A01 calls health()/runtime_stats() from API threads)
# --------------------------------------------------------------------------------------------
def test_health_and_stats_are_thread_safe_while_processing(settings):
    """4 API-like threads poll health()/runtime_stats() while the consumer thread processes 200 frames.

    Deterministic overlap (no timing assumptions): at call MID the fake detector blocks until every reader
    has completed a read that saw 0 < processed < n_frames. That also proves that health()/runtime_stats()
    do not wait for an inference in progress (the detector runs outside the analyzer lock)."""
    n_frames, n_readers, mid = 200, 4, 100
    seen_mid = [False] * n_readers
    readers_saw_mid = threading.Event()
    reads_during_inference: list[bool] = []

    def script(i: int) -> list[RawDetection]:
        if i == mid:  # an inference in progress: readers must still get through
            reads_during_inference.append(readers_saw_mid.wait(timeout=20))
        if i % 9 == 0:
            return []
        return [box(0.35 + 0.002 * (i % 40), 0.45, conf=0.55), box(0.75, 0.3 + 0.001 * (i % 30), conf=0.35)]

    det = FakeDetector(script=script)
    analyzer = loaded(settings, det)
    frames = [make_frame(i, image=IMG) for i in range(n_frames)]
    outputs: list = []
    errors: list[BaseException] = []
    reads = [0] * n_readers
    stop = threading.Event()
    barrier = threading.Barrier(n_readers + 1)

    def consumer() -> None:
        try:
            barrier.wait(timeout=10)
            for frame in frames:
                outputs.append(analyzer.process(frame))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            stop.set()

    def reader(k: int) -> None:
        last_processed = -1
        last_health_processed = -1
        try:
            barrier.wait(timeout=10)
            while True:
                done = stop.is_set()
                h = analyzer.health()
                s = analyzer.runtime_stats()
                assert isinstance(h, Health) and h.component == Component.PHONE
                assert h.status == HealthStatus.OK and h.code == "model_loaded"
                hp = int(h.details.get("processed", 0))
                assert hp >= last_health_processed
                last_health_processed = hp
                assert s["processed"] >= last_processed
                last_processed = s["processed"]
                assert 0 <= s["processed"] <= n_frames
                assert s["errors"] == s["invalid_frames"] == s["dropped_out_of_order"] == 0
                assert s["session_mismatch"] == s["skipped_interval"] == 0
                assert 0 <= s["live_tracks"] <= CFG.max_tracks
                assert s["tracks_created"] >= s["live_tracks"]
                reads[k] += 1
                if 0 < s["processed"] < n_frames and not seen_mid[k]:
                    seen_mid[k] = True
                    if all(seen_mid):
                        readers_saw_mid.set()
                if done:
                    break
                stop.wait(0.0005)  # throttle only (yields the GIL); correctness does not depend on it
        except BaseException as exc:
            readers_saw_mid.set()  # never leave the consumer blocked on a failed reader
            errors.append(exc)

    threads = [threading.Thread(target=consumer, name="phone-consumer")]
    threads += [threading.Thread(target=reader, args=(k,), name=f"api-{k}") for k in range(n_readers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "a thread did not finish"
    assert errors == [], errors
    assert all(r > 0 for r in reads), reads
    assert reads_during_inference == [True], "health()/runtime_stats() blocked while an inference was in progress"

    assert len(outputs) == n_frames and all(len(o) == 1 for o in outputs)
    for frame, out in zip(frames, outputs):
        check_contract(out[0], frame, analyzer)
    assert len({o[0].observation_id for o in outputs}) == n_frames
    final = analyzer.runtime_stats()
    assert final["processed"] == n_frames and det.calls == n_frames
    assert final["errors"] == 0 and final["dropped_out_of_order"] == 0
    h = analyzer.health()
    assert h.status == HealthStatus.OK and h.details["processed"] == n_frames
    HEALTH_SCHEMA.validate(h.model_dump(mode="json"))
