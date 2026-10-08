"""A03: the REAL local YOLO11n ONNX model on onnxruntime CPU (every test is marked ``real_model``).

Every test needs the verified weights (``models/phone/yolo11n.onnx`` whose size + SHA-256 match
``models.manifest.json``) and is skipped through the ``real_models_dir`` fixture when they are absent
(e.g. CI without ``python -m proctor.phone.prepare --download``).

What these tests DO check, with the real network (no FakeDetector anywhere in this file):
* load / health / manifest integration: CPU provider, phone class index read from the model's OWN
  names (cross-checked here with onnxruntime directly), SHA-256 identity, producer fields on emitted
  observations, contract + JSON-schema validity;
* inference mechanics: runs for several input sizes / letterbox modes / frame sizes, never modifies
  the frame, is deterministic, matches an independent reference preprocessing + plain ORT session, and
  maps boxes back to normalized coordinates of the UNMIRRORED frame. The geometry checks use a drawn
  disc and a deliberately class-agnostic config (all 80 COCO names) only to obtain real boxes at a
  known place - they are NOT phone detection and say nothing about phone accuracy;
* no phone boxes on uniform / noise frames at the default threshold; a uniform frame is "unknown",
  never "absent" (CONTRACTS.md: unknown != absent);
* a coarse latency bound (infer p50 < 2000 ms, printed).

What they do NOT check: phone-detection ACCURACY on exam-webcam footage. The optional test driven by
``QORGAU_PHONE_EVAL_IMAGES`` (a dir with ``images/`` + ``labels/`` in YOLO txt format, optionally with a
split subfolder such as ``images/train2017``) prints recall WITH denominators and asserts only that the
pipeline runs. COCO train images (e.g. coco128) were seen in training -> pipeline sanity check only.

A phone in the frame is NOT evidence that anything was photographed: a single still image never yields
``phone_raised`` / ``possible_screen_capture`` = present, and the phone's camera side is never claimed.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import math
import os
import socket
import urllib.request
from pathlib import Path

import jsonschema
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
ort = pytest.importorskip("onnxruntime")

import proctor.phone.detector as detector_mod  # noqa: E402
from proctor.phone import PHONE_MODULE_VERSION, PhoneAnalyzer, PhoneConfig, create_phone_analyzer  # noqa: E402
from proctor.phone.detector import YoloOnnxDetector  # noqa: E402
from proctor.phone.manifest import load_manifest  # noqa: E402
from proctor.phone.tests.helpers import SESSION, make_frame, make_image  # noqa: E402
from proctor.settings import PROCTORING_ROOT, Settings  # noqa: E402
from proctor_contracts.interfaces import FrameAnalyzer  # noqa: E402
from proctor_contracts.v1 import (  # noqa: E402
    Component,
    Health,
    HealthStatus,
    ModelManifest,
    ObservationStatus,
    PhoneObservation,
    PhoneSignalName,
    SignalState,
    SourceMode,
)

SCHEMA = json.loads((PROCTORING_ROOT / "contracts" / "schema" / "v1" / "qorgau.v1.schema.json").read_text(encoding="utf-8"))
SIGNAL_NAMES = {PhoneSignalName.PHONE_VISIBLE, PhoneSignalName.PHONE_RAISED, PhoneSignalName.POSSIBLE_SCREEN_CAPTURE}
PHONE_CONFIG_ENV = {f"QORGAU_PHONE_{f.name.upper()}" for f in dataclasses.fields(PhoneConfig)}

EVAL_ENV = "QORGAU_PHONE_EVAL_IMAGES"
EVAL_LIMIT_ENV = "QORGAU_PHONE_EVAL_LIMIT"
EVAL_DEFAULT_LIMIT = 200
EVAL_FLOOR_CONF = 0.05
EVAL_IOU = 0.5
LABEL_PHONE_CLASS = 67  # COCO-80 label order ("cell phone"); cross-checked with the model's own names
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}

LATENCY_P50_LIMIT_MS = 2000.0  # deliberately generous: a smoke bound, not a performance target


# =============================================================================== helpers / fixtures


def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"})


def _validate_observation(obs: PhoneObservation) -> PhoneObservation:
    """Round-trip through the wire format + the published JSON schema."""
    assert isinstance(obs, PhoneObservation)
    data = obs.model_dump(mode="json")
    again = PhoneObservation.model_validate(data)
    _validator("PhoneObservation").validate(data)
    assert {s.name for s in again.signals} == SIGNAL_NAMES
    for det in again.detections:
        b = det.bbox
        assert 0.0 <= b.x_min <= b.x_max <= 1.0 and 0.0 <= b.y_min <= b.y_max <= 1.0
    return again


def _validate_health(health: Health) -> None:
    data = health.model_dump(mode="json")
    Health.model_validate(data)
    _validator("Health").validate(data)


def _signal(obs: PhoneObservation, name: PhoneSignalName):
    (match,) = [s for s in obs.signals if s.name == name]
    return match


def _settings(models_dir: Path, tmp_path: Path) -> Settings:
    return Settings(models_dir=models_dir, data_dir=tmp_path / "data")


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Independent IoU (does not use the module's own helpers)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _xyxy(det) -> tuple[float, float, float, float]:
    return (det.x_min, det.y_min, det.x_max, det.y_max)


def _disc_scene(width: int, height: int, cx: float, cy: float, r: float) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Read-only BGR frame: horizontal gray gradient + one filled red disc at (cx, cy) (normalized).
    Returns the frame and the disc's bounding box in normalized coordinates of THIS (unmirrored) frame."""
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:] = np.linspace(40, 220, width, dtype=np.uint8)[None, :, None]
    rad = int(r * min(width, height))
    px, py = int(cx * width), int(cy * height)
    cv2.circle(image, (px, py), rad, (0, 0, 255), -1)
    image.flags.writeable = False
    return image, ((px - rad) / width, (py - rad) / height, (px + rad + 1) / width, (py + rad + 1) / height)


def _any_class_detector(model_file: Path, manifest: ModelManifest, **overrides) -> YoloOnnxDetector:
    """Class-agnostic config (all 80 COCO names), used ONLY to obtain real boxes on drawn objects for
    geometry / determinism checks of decode + NMS + coordinate mapping. NOT phone detection."""
    names = tuple(manifest.class_names[k] for k in sorted(manifest.class_names, key=int))
    cfg = dataclasses.replace(PhoneConfig(), phone_class_names=names, conf_threshold=0.05, min_box_area=0.0, **overrides)
    det = YoloOnnxDetector(model_file, manifest, cfg)
    det.load()
    return det


@pytest.fixture(scope="module")
def manifest() -> ModelManifest:
    return load_manifest()


@pytest.fixture(scope="module")
def model_file(real_models_dir, manifest) -> Path:
    path = real_models_dir / manifest.file
    assert path.is_file()
    return path


@pytest.fixture(scope="module")
def reference_session(model_file):
    """A plain ORT CPU session built here, independent of YoloOnnxDetector's session options."""
    return ort.InferenceSession(str(model_file), providers=["CPUExecutionProvider"])


@pytest.fixture(scope="module")
def model_names(reference_session) -> dict[int, str]:
    """Class names read straight from the ONNX metadata (Ultralytics stores a dict literal)."""
    raw = reference_session.get_modelmeta().custom_metadata_map["names"]
    names = ast.literal_eval(raw)
    return {int(k): str(v) for k, v in names.items()}


@pytest.fixture(scope="module")
def phone_index(model_names) -> int:
    matches = [i for i, n in model_names.items() if n == "cell phone"]
    assert len(matches) == 1, matches
    return matches[0]


@pytest.fixture(scope="module")
def phone_detector(model_file, manifest):
    """The production detector with the default PhoneConfig (phone class only)."""
    det = YoloOnnxDetector(model_file, manifest, PhoneConfig())
    det.load()
    yield det
    det.close()


@pytest.fixture()
def clean_phone_env(monkeypatch):
    """create_phone_analyzer reads QORGAU_PHONE_<FIELD>; make the defaults deterministic here."""
    for key in PHONE_CONFIG_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def no_camera_no_network(monkeypatch) -> list[str]:
    """Any camera open / outbound connection is recorded and refused. Tests assert the list stays empty
    (a broad ``except Exception`` in the code under test could otherwise swallow the refusal)."""
    calls: list[str] = []

    def deny(name: str):
        def _deny(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must never be used by the phone analyzer")

        return _deny

    monkeypatch.setattr(cv2, "VideoCapture", deny("cv2.VideoCapture"))
    monkeypatch.setattr(urllib.request, "urlopen", deny("urllib.request.urlopen"))
    monkeypatch.setattr(socket.socket, "connect", deny("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", deny("socket.create_connection"))
    return calls


@pytest.fixture()
def loaded_analyzer(real_models_dir, tmp_path, clean_phone_env):
    analyzer = create_phone_analyzer(_settings(real_models_dir, tmp_path))
    health = analyzer.load()
    assert health.status == HealthStatus.OK, health
    yield analyzer
    analyzer.close()


@pytest.fixture()
def raw_outputs(monkeypatch) -> list[np.ndarray]:
    """Records every raw ORT output handed to detector.decode (wraps, does not replace, the real one)."""
    captured: list[np.ndarray] = []
    real_decode = detector_mod.decode

    def recording(output, *args, **kwargs):
        captured.append(np.array(output, copy=True))
        return real_decode(output, *args, **kwargs)

    monkeypatch.setattr(detector_mod, "decode", recording)
    return captured


@pytest.fixture()
def net_inputs(monkeypatch) -> list[np.ndarray]:
    """Records every network input tensor produced by detector.preprocess (wraps the real one)."""
    captured: list[np.ndarray] = []
    real_preprocess = detector_mod.preprocess

    def recording(image, lb, cv2_module):
        tensor = real_preprocess(image, lb, cv2_module)
        captured.append(np.array(tensor, copy=True))
        return tensor

    monkeypatch.setattr(detector_mod, "preprocess", recording)
    return captured


# ============================================================================ load / health / manifest


@pytest.mark.real_model
def test_real_factory_load_reports_verified_model(
    real_models_dir, tmp_path, manifest, model_file, phone_index, clean_phone_env, no_camera_no_network
):
    files_before = sorted(p.relative_to(real_models_dir) for p in real_models_dir.rglob("*"))
    analyzer = create_phone_analyzer(_settings(real_models_dir, tmp_path))
    assert isinstance(analyzer, FrameAnalyzer) and analyzer.name == "phone"
    assert analyzer.health().status == HealthStatus.STARTING  # the factory is cheap: nothing loaded yet
    health = analyzer.load()
    try:
        assert health.component == Component.PHONE
        assert health.status == HealthStatus.OK and health.code == "model_loaded", health
        details = health.details
        assert details["provider"] == "CPUExecutionProvider"
        # index derived from the model's own names (read here with onnxruntime), not assumed
        assert phone_index == 67
        assert details["phone_class"] == f"{phone_index}:cell phone" == "67:cell phone"
        assert hashlib.sha256(model_file.read_bytes()).hexdigest() == manifest.sha256
        assert details["sha256_prefix"] == manifest.sha256[:12]
        assert details["model_id"] == manifest.model_id
        assert details["model_version"] == manifest.version
        assert details["license"] == manifest.license
        assert details["input_size"] == 640 and details["rect"] is True
        assert details["conf_threshold"] == PhoneConfig().conf_threshold
        assert details["config_version"] == analyzer.config.config_version == PhoneConfig().config_version
        assert "fake_detector" not in details and "TEST" not in health.message
        assert "CPUExecutionProvider" in health.message and "sha256 verified" in health.message
        _validate_health(health)
        dumped = json.dumps(health.model_dump(mode="json"))
        assert str(real_models_dir) not in dumped  # no absolute (home) path in health
        assert analyzer.health() == health  # before any frame, health() is the load result

        again = analyzer.load()  # load() is repeatable (e.g. a retry from A01)
        assert again.status == HealthStatus.OK and again.code == "model_loaded"
        assert again.details["sha256_prefix"] == manifest.sha256[:12]
    finally:
        analyzer.close()
    assert analyzer.health().status == HealthStatus.STOPPED
    assert no_camera_no_network == []
    assert not (tmp_path / "data").exists()  # loading the model writes nothing to the data dir
    assert sorted(p.relative_to(real_models_dir) for p in real_models_dir.rglob("*")) == files_before


@pytest.mark.real_model
def test_real_model_metadata_names_and_dynamic_hw(phone_detector, reference_session, model_names, manifest):
    info = phone_detector.info
    assert info is not None
    # the official export has dynamic H/W -> no fixed input size is forced on the detector
    shape = reference_session.get_inputs()[0].shape
    assert len(shape) == 4 and shape[1] == 3
    assert not isinstance(shape[2], int) and not isinstance(shape[3], int), shape
    assert info.static_input is None
    assert info.input_size == PhoneConfig().input_size == 640 and info.rect is True
    # names: model metadata == detector == manifest; the phone class is selected by NAME
    assert info.class_names == model_names == {int(k): v for k, v in manifest.class_names.items()}
    assert len(model_names) == 80
    assert info.phone_classes == {67: "cell phone"}
    assert info.provider == "CPUExecutionProvider"
    assert info.warmup_ms is not None and info.warmup_ms > 0
    assert "AGPL" in info.model_meta.get("license", "") and manifest.license == "AGPL-3.0"
    assert manifest.input_size == [640, 640] and "imgsz" in info.model_meta


# ===================================================================== no phone on synthetic frames


@pytest.mark.real_model
def test_real_uniform_frames_have_no_detections_and_are_unknown(phone_detector, loaded_analyzer):
    for value in (0, 114, 128, 255):
        dets, timings = phone_detector.detect(make_image(value=value))
        assert len(dets) == 0, (value, dets)
        assert set(timings) == {"pre_ms", "infer_ms", "post_ms"}
        assert all(math.isfinite(v) and v >= 0 for v in timings.values())

    loaded_analyzer.start_session(SESSION, SourceMode.REPLAY)
    (obs,) = loaded_analyzer.process(make_frame(0, image=make_image(value=128)))
    obs = _validate_observation(obs)
    assert obs.detections == []
    # a blank frame says nothing: unknown, never "absent" / all clear
    assert obs.status == ObservationStatus.UNKNOWN
    assert obs.quality == 0.0 and "blank_frame" in obs.quality_flags
    assert all(s.state == SignalState.UNKNOWN for s in obs.signals), obs.signals


@pytest.mark.real_model
def test_real_textured_noise_frames_have_no_phone_detections(phone_detector, loaded_analyzer):
    for seed in range(6):
        dets, _ = phone_detector.detect(make_image(seed=seed))
        assert len(dets) == 0, (seed, dets)

    loaded_analyzer.start_session(SESSION, SourceMode.REPLAY)
    for i in range(4):
        (obs,) = loaded_analyzer.process(make_frame(i, image=make_image(seed=100 + i)))
        obs = _validate_observation(obs)
        assert obs.status == ObservationStatus.OK and obs.quality_flags == []
        assert obs.detections == []
        visible = _signal(obs, PhoneSignalName.PHONE_VISIBLE)
        assert (visible.state, visible.reason) == (SignalState.ABSENT, "no_detection")
        raised = _signal(obs, PhoneSignalName.PHONE_RAISED)
        assert (raised.state, raised.reason) == (SignalState.ABSENT, "no_phone_in_view")
        capture = _signal(obs, PhoneSignalName.POSSIBLE_SCREEN_CAPTURE)
        # screen capture is never declared "absent" (the camera side is not observable)
        assert capture.state == SignalState.INSUFFICIENT_EVIDENCE
        assert capture.facts.get("camera_direction_observable") is False


@pytest.mark.real_model
def test_real_phone_only_config_does_not_report_other_objects(phone_detector, model_file, manifest):
    """The drawn disc is found by the class-agnostic config, but the production config (phone class
    by name) does not report it: the class filter works on real outputs."""
    any_class = _any_class_detector(model_file, manifest)
    try:
        for args in ((640, 480, 0.3, 0.65, 0.22), (1280, 720, 0.72, 0.3, 0.18), (480, 640, 0.5, 0.5, 0.25)):
            image, disc = _disc_scene(*args)
            found, _ = any_class.detect(image)
            assert found and max(_iou(_xyxy(d), disc) for d in found) >= 0.85, (args, found)
            assert all(d.class_name != "cell phone" for d in found)
            phones, _ = phone_detector.detect(image)
            assert phones == [], (args, phones)
    finally:
        any_class.close()


# ============================================================================= observation contract


@pytest.mark.real_model
def test_real_observation_producer_matches_manifest_and_contract(loaded_analyzer, manifest):
    loaded_analyzer.start_session(SESSION, SourceMode.REPLAY)
    frame = make_frame(3, image=make_image(seed=7))
    result = loaded_analyzer.process(frame)
    assert len(result) == 1
    obs = _validate_observation(result[0])
    producer = obs.producer
    assert producer.module == "phone" and producer.version == PHONE_MODULE_VERSION
    assert producer.model_id == manifest.model_id
    assert producer.model_sha256 == manifest.sha256
    assert producer.config_version == loaded_analyzer.config.config_version
    meta = frame.meta
    assert obs.observation_id == f"phone-{meta.frame_id}"
    assert (obs.session_id, obs.frame_id, obs.t_session_ms) == (meta.session_id, meta.frame_id, meta.t_session_ms)
    assert obs.wall_time == meta.wall_time and obs.source_mode == meta.source_mode
    assert obs.kind == "phone" and obs.latency_ms is not None and obs.latency_ms >= 0


@pytest.mark.real_model
def test_real_close_releases_the_model(loaded_analyzer):
    loaded_analyzer.start_session(SESSION, SourceMode.REPLAY)
    (ok,) = loaded_analyzer.process(make_frame(0))
    assert ok.status == ObservationStatus.OK
    loaded_analyzer.close()
    health = loaded_analyzer.health()
    assert health.status == HealthStatus.STOPPED and health.code == "closed"
    _validate_health(health)
    loaded_analyzer.start_session(SESSION, SourceMode.REPLAY)
    (obs,) = loaded_analyzer.process(make_frame(1))
    obs = _validate_observation(obs)
    assert obs.status == ObservationStatus.ERROR and obs.quality_flags == ["model_unavailable"]
    assert obs.detections == [] and all(s.state == SignalState.UNKNOWN for s in obs.signals)


# ========================================================================== inference mechanics


@pytest.mark.real_model
def test_real_inference_does_not_modify_the_frame(phone_detector, loaded_analyzer):
    # writable frames: the bytes must be identical afterwards (640x480 = no-resize path, others resize)
    for width, height in ((640, 480), (1280, 720), (333, 211)):
        image = np.array(make_image(width=width, height=height, seed=5))
        assert image.flags.writeable
        before = image.copy()
        phone_detector.detect(image)
        assert np.array_equal(image, before), (width, height)
        assert image.flags.writeable

    # read-only frames (the FramePacket contract): any write would raise -> an error observation
    loaded_analyzer.start_session(SESSION, SourceMode.REPLAY)
    for i, (width, height) in enumerate(((640, 480), (1280, 720))):
        image = make_image(width=width, height=height, seed=9)
        before = image.copy()
        (obs,) = loaded_analyzer.process(make_frame(i, image=image))
        assert obs.status != ObservationStatus.ERROR, obs
        assert np.array_equal(image, before) and not image.flags.writeable


@pytest.mark.real_model
def test_real_inference_is_deterministic(phone_detector, model_file, manifest, raw_outputs):
    image = make_image(seed=21)
    raw_outputs.clear()
    first, _ = phone_detector.detect(image)
    second, _ = phone_detector.detect(image)
    assert first == second
    assert len(raw_outputs) == 2 and np.array_equal(raw_outputs[0], raw_outputs[1])

    # non-empty boxes: class-agnostic config on a drawn disc (geometry/determinism only, not phones)
    scene, _ = _disc_scene(640, 480, 0.3, 0.65, 0.22)
    det_a = _any_class_detector(model_file, manifest)
    det_b = _any_class_detector(model_file, manifest)  # a second, independently created session
    try:
        raw_outputs.clear()
        a1, _ = det_a.detect(scene)
        a2, _ = det_a.detect(scene)
        b1, _ = det_b.detect(scene)
        assert a1, "expected at least one box on the drawn disc"
        assert a1 == a2  # same boxes, same confidences, same classes
        assert np.array_equal(raw_outputs[0], raw_outputs[1])
        assert np.allclose(raw_outputs[0], raw_outputs[2], rtol=1e-5, atol=1e-4)
        assert [d.class_index for d in a1] == [d.class_index for d in b1]
        for x, y in zip(a1, b1):
            assert np.allclose(_xyxy(x), _xyxy(y), atol=1e-5) and abs(x.confidence - y.confidence) <= 1e-5
    finally:
        det_a.close()
        det_b.close()


@pytest.mark.real_model
def test_real_preprocessing_and_outputs_match_an_independent_reference(
    model_file, manifest, reference_session, net_inputs, raw_outputs
):
    """Square letterbox (rect=False): the module's network input equals a letterbox written here
    (gray 114 padding, RGB, CHW, /255) and its ORT output equals a plain default ORT session's."""
    det = YoloOnnxDetector(model_file, manifest, dataclasses.replace(PhoneConfig(), rect=False))
    det.load()
    input_name = reference_session.get_inputs()[0].name
    try:
        for width, height in ((640, 480), (1280, 720), (480, 640)):
            image = make_image(width=width, height=height, seed=31)
            r = min(640 / height, 640 / width)
            new_w, new_h = round(width * r), round(height * r)
            resized = image if (new_w, new_h) == (width, height) else cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            top, left = (640 - new_h) // 2, (640 - new_w) // 2
            canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
            canvas[top : top + new_h, left : left + new_w] = resized
            reference = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1))[None].astype(np.float32) / np.float32(255.0)
            bgr_tensor = np.ascontiguousarray(canvas.transpose(2, 0, 1))[None].astype(np.float32) / np.float32(255.0)

            net_inputs.clear()
            raw_outputs.clear()
            det.detect(image)
            assert len(net_inputs) == 1 and net_inputs[0].shape == (1, 3, 640, 640)
            assert net_inputs[0].dtype == np.float32
            assert np.allclose(net_inputs[0], reference, atol=1e-6), (width, height)
            assert not np.allclose(net_inputs[0], bgr_tensor, atol=1e-3)  # the check is sensitive to channel order

            expected = reference_session.run(None, {input_name: reference})[0]
            got = raw_outputs[0]
            assert got.shape == expected.shape == (1, 84, 8400)
            assert np.allclose(got[:, :4], expected[:, :4], atol=1e-2)  # box channels, input pixels
            assert np.allclose(got[:, 4:], expected[:, 4:], atol=1e-4)  # class scores (already sigmoid)
    finally:
        det.close()


@pytest.mark.real_model
@pytest.mark.parametrize("rect", [True, False], ids=["rect", "square"])
@pytest.mark.parametrize("input_size", [320, 640])
def test_real_boxes_map_to_unmirrored_normalized_frame_coordinates(model_file, manifest, input_size, rect):
    """A real box for a drawn disc lands on that disc in normalized coords of the ORIGINAL, UNMIRRORED
    frame, for several frame sizes / aspect ratios (letterbox scale + offset undone correctly).
    Class-agnostic config: geometry of the pipeline only, not phone detection accuracy."""
    det = _any_class_detector(model_file, manifest, input_size=input_size, rect=rect)
    try:
        for args in (
            (640, 480, 0.3, 0.65, 0.22),
            (1280, 720, 0.72, 0.3, 0.18),
            (480, 640, 0.5, 0.5, 0.25),
            (333, 211, 0.3, 0.65, 0.22),
        ):
            image, disc = _disc_scene(*args)
            found, _ = det.detect(image)
            assert found, args
            best = max(found, key=lambda d: _iou(_xyxy(d), disc))
            assert _iou(_xyxy(best), disc) >= 0.85, (args, best, disc)
            # left stays left / top stays top: no mirroring, no axis swap (only for clearly off-centre discs)
            bx, by = best.center
            if abs(args[2] - 0.5) > 0.1:
                assert (bx < 0.5) == (args[2] < 0.5), (args, best.center)
            if abs(args[3] - 0.5) > 0.1:
                assert (by < 0.5) == (args[3] < 0.5), (args, best.center)

        # the mirrored frame gives the mirrored box (the module itself never mirrors)
        image, disc = _disc_scene(640, 480, 0.3, 0.65, 0.22)
        flipped = np.ascontiguousarray(image[:, ::-1])
        found, _ = det.detect(flipped)
        mirrored_disc = (1.0 - disc[2], disc[1], 1.0 - disc[0], disc[3])
        assert found and max(_iou(_xyxy(d), mirrored_disc) for d in found) >= 0.85
    finally:
        det.close()


@pytest.mark.real_model
def test_real_prepadded_frame_gives_identical_boxes(model_file, manifest):
    """Square letterbox at 640: a frame whose long side is 640 and the same frame pre-padded by hand
    with the letterbox gray (114) give a bit-identical network input, so the un-letterboxed boxes must
    agree exactly after mapping the padded coordinates back. Class-agnostic config, geometry only."""
    det = _any_class_detector(model_file, manifest, rect=False)
    try:
        compared = 0
        for width, height, cx, cy, r in ((640, 480, 0.3, 0.65, 0.22), (480, 640, 0.7, 0.35, 0.2), (640, 352, 0.6, 0.4, 0.3)):
            image, _ = _disc_scene(width, height, cx, cy, r)
            top, left = (640 - height) // 2, (640 - width) // 2
            padded = np.full((640, 640, 3), 114, dtype=np.uint8)
            padded[top : top + height, left : left + width] = image
            padded.flags.writeable = False
            plain, _ = det.detect(image)
            boxed, _ = det.detect(padded)
            assert plain, (width, height)
            assert len(plain) == len(boxed)
            for p, q in zip(plain, boxed):
                mapped = (
                    min(1.0, max(0.0, (q.x_min * 640 - left) / width)),
                    min(1.0, max(0.0, (q.y_min * 640 - top) / height)),
                    min(1.0, max(0.0, (q.x_max * 640 - left) / width)),
                    min(1.0, max(0.0, (q.y_max * 640 - top) / height)),
                )
                assert np.allclose(_xyxy(p), mapped, atol=1e-4), (width, height, p, q)
                assert p.class_index == q.class_index and abs(p.confidence - q.confidence) <= 1e-6
                compared += 1
        assert compared >= 3
    finally:
        det.close()


@pytest.mark.real_model
@pytest.mark.parametrize("rect", [True, False], ids=["rect", "square"])
@pytest.mark.parametrize("input_size", [320, 480, 640])
def test_real_input_size_and_rect_variants_load_and_run(real_models_dir, tmp_path, net_inputs, input_size, rect):
    cfg = dataclasses.replace(PhoneConfig(), input_size=input_size, rect=rect)
    analyzer = PhoneAnalyzer(_settings(real_models_dir, tmp_path), config=cfg)
    health = analyzer.load()
    try:
        assert health.status == HealthStatus.OK and health.code == "model_loaded", health
        assert health.details["input_size"] == input_size and health.details["rect"] is rect
        assert health.details["provider"] == "CPUExecutionProvider"
        analyzer.start_session(SESSION, SourceMode.REPLAY)
        net_inputs.clear()
        (obs,) = analyzer.process(make_frame(0, image=make_image(640, 480, seed=4)))
        obs = _validate_observation(obs)
        assert obs.status == ObservationStatus.OK and obs.detections == []
        assert obs.producer.config_version == cfg.config_version
        assert (cfg.config_version == PhoneConfig().config_version) == (input_size == 640 and rect)
        # the network really ran at the configured size (dynamic H/W model)
        expected_h = math.ceil(round(480 * input_size / 640) / 32) * 32 if rect else input_size
        assert len(net_inputs) == 1 and net_inputs[0].shape == (1, 3, expected_h, input_size)
    finally:
        analyzer.close()


@pytest.mark.real_model
def test_real_varied_frame_sizes_run_without_errors(loaded_analyzer):
    sizes = [(16, 16), (17, 23), (640, 480), (1280, 720), (1920, 1080), (480, 640), (1280, 32), (32, 1280), (333, 211)]
    loaded_analyzer.start_session(SESSION, SourceMode.REPLAY)
    for i, (width, height) in enumerate(sizes):
        (obs,) = loaded_analyzer.process(make_frame(i, image=make_image(width=width, height=height, seed=i)))
        obs = _validate_observation(obs)
        assert obs.status != ObservationStatus.ERROR, (width, height, obs.quality_flags)
        assert "size_mismatch" not in obs.quality_flags
    stats = loaded_analyzer.runtime_stats()
    assert stats["processed"] == len(sizes) and stats["errors"] == 0 and stats["invalid_frames"] == 0
    health = loaded_analyzer.health()
    assert health.status == HealthStatus.OK and health.code == "model_loaded"
    _validate_health(health)


@pytest.mark.real_model
def test_real_latency_p50_from_runtime_stats(loaded_analyzer):
    loaded_analyzer.start_session(SESSION, SourceMode.REPLAY)
    n = 10
    for i in range(n):
        (obs,) = loaded_analyzer.process(make_frame(i, image=make_image(seed=200 + i)))
        assert obs.status == ObservationStatus.OK
    stats = loaded_analyzer.runtime_stats()
    print(
        f"\n[A03 real model] infer_ms p50={stats['infer_ms_p50']} p95={stats['infer_ms_p95']} "
        f"process_ms p50={stats['process_ms_p50']} p95={stats['process_ms_p95']} over {n} frames 640x480 "
        f"(onnxruntime {ort.__version__} CPU, {PhoneConfig().intra_op_threads} intra-op threads)"
    )
    assert stats["processed"] == n and stats["errors"] == 0
    assert 0 < stats["infer_ms_p50"] < LATENCY_P50_LIMIT_MS
    assert stats["process_ms_p50"] >= stats["infer_ms_p50"]  # end-to-end includes the inference
    details = loaded_analyzer.health().details
    assert details["infer_ms_p50"] == stats["infer_ms_p50"] and details["processed"] == n


# ===================================================================== provider / env configuration


@pytest.mark.real_model
def test_real_auto_execution_provider_on_this_build(model_file, manifest, real_models_dir, tmp_path, clean_phone_env, monkeypatch):
    available = ort.get_available_providers()
    print(f"\n[A03 real model] onnxruntime {ort.__version__} providers: {available}")
    det = YoloOnnxDetector(model_file, manifest, dataclasses.replace(PhoneConfig(), execution_provider="auto"))
    info = det.load()
    try:
        assert info.provider in available
        if [p for p in available if p != "AzureExecutionProvider"] == ["CPUExecutionProvider"]:
            assert info.provider == "CPUExecutionProvider"
        dets, _ = det.detect(make_image(seed=1))
        assert dets == []
    finally:
        det.close()

    monkeypatch.setenv("QORGAU_PHONE_EXECUTION_PROVIDER", "auto")
    analyzer = create_phone_analyzer(_settings(real_models_dir, tmp_path))
    health = analyzer.load()
    try:
        assert health.status == HealthStatus.OK and health.code == "model_loaded", health
        assert health.details["provider"] == info.provider
        assert analyzer.config.execution_provider == "auto"
        assert health.details["config_version"] != PhoneConfig().config_version
    finally:
        analyzer.close()


@pytest.mark.real_model
def test_real_env_input_size_override_reaches_the_session(real_models_dir, tmp_path, clean_phone_env, monkeypatch, net_inputs):
    monkeypatch.setenv("QORGAU_PHONE_INPUT_SIZE", "320")
    analyzer = create_phone_analyzer(_settings(real_models_dir, tmp_path))
    health = analyzer.load()
    try:
        assert health.status == HealthStatus.OK and health.details["input_size"] == 320
        analyzer.start_session(SESSION, SourceMode.REPLAY)
        net_inputs.clear()
        (obs,) = analyzer.process(make_frame(0, image=make_image(640, 480, seed=2)))
        assert obs.status == ObservationStatus.OK
        assert net_inputs[0].shape == (1, 3, 256, 320)  # 640x480 -> 320x240 -> stride-32 pad to 320x256
    finally:
        analyzer.close()


# ============================================== optional: labelled still images (QORGAU_PHONE_EVAL_IMAGES)


def _eval_pairs(root: Path) -> list[tuple[Path, Path]]:
    """(image, label) pairs from <root>/images + <root>/labels, with optional split subfolders
    (images/<split>/x.jpg <-> labels/<split>/x.txt)."""
    images_dir, labels_dir = root / "images", root / "labels"
    pairs: list[tuple[Path, Path]] = []
    for entry in sorted(images_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXT:
            pairs.append((entry, labels_dir / f"{entry.stem}.txt"))
        elif entry.is_dir():
            for item in sorted(entry.iterdir()):
                if item.is_file() and item.suffix.lower() in IMAGE_EXT:
                    pairs.append((item, labels_dir / entry.name / f"{item.stem}.txt"))
    return pairs


def _read_yolo_labels(path: Path) -> list[tuple[int, tuple[float, float, float, float]]]:
    items: list[tuple[int, tuple[float, float, float, float]]] = []
    if not path.is_file():
        return items
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cls = int(float(parts[0]))
        cx, cy, w, h = (float(v) for v in parts[1:5])
        items.append((cls, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
    return items


def _count_matched(preds: list[tuple[tuple[float, float, float, float], float]], gts: list[tuple[float, float, float, float]]) -> int:
    """Greedy one-to-one matching, highest score first; returns matched ground-truth boxes (IoU >= 0.5)."""
    used = [False] * len(gts)
    for box, _ in sorted(preds, key=lambda p: -p[1]):
        best, best_j = EVAL_IOU, -1
        for j, gt in enumerate(gts):
            if not used[j] and (value := _iou(box, gt)) >= best:
                best, best_j = value, j
        if best_j >= 0:
            used[best_j] = True
    return sum(used)


@pytest.fixture(scope="module")
def eval_set() -> tuple[str, list[tuple[Path, list[tuple[float, float, float, float]]]]]:
    raw = os.environ.get(EVAL_ENV)
    if not raw:
        pytest.skip(f"{EVAL_ENV} not set (optional labelled-image pipeline check)")
    root = Path(raw).expanduser()
    if not (root / "images").is_dir() or not (root / "labels").is_dir():
        pytest.skip(f"{EVAL_ENV}={raw} has no images/ and labels/ directories")
    limit = int(os.environ.get(EVAL_LIMIT_ENV, EVAL_DEFAULT_LIMIT))
    phone_imgs, other_imgs = [], []
    for image_path, label_path in _eval_pairs(root):
        labels = _read_yolo_labels(label_path)
        gts = [b for c, b in labels if c == LABEL_PHONE_CLASS]
        (phone_imgs if gts else other_imgs).append((image_path, gts))
    selected = (phone_imgs + other_imgs)[:limit]  # phone-labelled images first, deterministic order
    if not selected:
        pytest.skip(f"{EVAL_ENV}={raw}: no images found")
    return root.name, selected


@pytest.mark.real_model
def test_real_eval_images_recall_is_reported_not_asserted(
    eval_set, real_models_dir, tmp_path, model_file, manifest, phone_index, clean_phone_env, no_camera_no_network
):
    """Pipeline sanity on labelled still photos: prints recall WITH denominators; asserts only that the
    pipeline runs and that single stills never produce a temporal phone signal."""
    assert phone_index == LABEL_PHONE_CLASS  # labels and model share the COCO-80 order
    name, items = eval_set
    analyzer = create_phone_analyzer(_settings(real_models_dir, tmp_path))
    assert analyzer.load().status == HealthStatus.OK
    floor = YoloOnnxDetector(model_file, manifest, dataclasses.replace(PhoneConfig(), conf_threshold=EVAL_FLOOR_CONF))
    floor.load()
    default_conf = analyzer.config.conf_threshold
    n_imgs = n_phone_imgs = n_neg = unreadable = oversize = 0
    n_gt = tp_default = tp_floor = neg_with_box = 0
    visible_present = 0
    try:
        for i, (path, gts) in enumerate(items):
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)  # an image FILE, never a camera
            if image is None:
                unreadable += 1
                continue
            if image.shape[1] > 7680 or image.shape[0] > 4320:
                oversize += 1
                continue
            image.flags.writeable = False
            session = f"s-eval-{i}"
            analyzer.start_session(session, SourceMode.REPLAY)  # fresh tracker per still image
            result = analyzer.process(make_frame(0, image=image, session_id=session))
            assert len(result) == 1
            obs = _validate_observation(result[0])
            assert obs.status != ObservationStatus.ERROR, (path.name, obs.quality_flags)
            assert all(d.class_name == "cell phone" and d.class_index == phone_index for d in obs.detections)
            assert all(d.confidence >= default_conf for d in obs.detections)
            # one still frame can never establish a temporal pattern; a phone box is not a photo
            assert _signal(obs, PhoneSignalName.PHONE_RAISED).state != SignalState.PRESENT
            capture = _signal(obs, PhoneSignalName.POSSIBLE_SCREEN_CAPTURE)
            assert capture.state != SignalState.PRESENT and capture.facts.get("camera_direction_observable") is False
            visible = _signal(obs, PhoneSignalName.PHONE_VISIBLE)
            if visible.state == SignalState.PRESENT:
                visible_present += 1
                assert visible.reason == "detected_high_confidence"  # no confirmed track from one frame
            preds = [((d.bbox.x_min, d.bbox.y_min, d.bbox.x_max, d.bbox.y_max), d.confidence) for d in obs.detections]
            n_imgs += 1
            if gts:
                floor_dets, _ = floor.detect(image)  # floor recall needs phone-labelled images only
                floor_preds = [(_xyxy(d), d.confidence) for d in floor_dets]
                n_phone_imgs += 1
                n_gt += len(gts)
                tp_default += _count_matched(preds, gts)
                tp_floor += _count_matched(floor_preds, gts)
            else:
                n_neg += 1
                neg_with_box += bool(preds)
        stats = analyzer.runtime_stats()  # last session only (counters reset per session)
        assert analyzer.health().status == HealthStatus.OK
    finally:
        analyzer.close()
        floor.close()

    print(
        f"\n[A03 eval, PIPELINE SANITY ONLY] dataset={name}: images={n_imgs} (phone-labelled {n_phone_imgs}, "
        f"other {n_neg}, unreadable {unreadable}, oversize {oversize}); labelled phones={n_gt}\n"
        f"  recall@conf>={default_conf} (production): {tp_default}/{n_gt}   "
        f"recall@conf>={EVAL_FLOOR_CONF} (floor): {tp_floor}/{n_gt}   (IoU>={EVAL_IOU})\n"
        f"  images without phone labels that got a phone box @conf>={default_conf}: {neg_with_box}/{n_neg}\n"
        f"  phone_visible=present (single still, high-confidence path): {visible_present}/{n_imgs}; "
        f"last infer_ms={stats.get('infer_ms_p50')}\n"
        f"  caveats: still photos, not the exam webcam view; COCO train images were seen in training; "
        f"a matched box is 'a phone-shaped object', never 'something was photographed'."
    )
    assert n_imgs > 0 and n_imgs + unreadable + oversize == len(items)
    assert 0 <= tp_default <= tp_floor <= n_gt  # a lower threshold can only add matches
    assert no_camera_no_network == []


@pytest.mark.real_model
def test_real_eval_images_prepadding_geometry_is_exact(eval_set, model_file, manifest):
    """On labelled phone photos: resize so the long side is 640, then compare detections on the frame
    vs the same frame pre-padded with letterbox gray -> identical network input under the square
    letterbox, so mapped boxes must agree exactly. Geometry check; not an accuracy number."""
    _, items = eval_set
    phone_items = [(path, gts) for path, gts in items if gts]
    if not phone_items:
        pytest.skip("no phone-labelled images in the eval set")
    det = YoloOnnxDetector(
        model_file, manifest, dataclasses.replace(PhoneConfig(), rect=False, conf_threshold=EVAL_FLOOR_CONF, min_box_area=0.0)
    )
    det.load()
    compared = images = 0
    try:
        for path, _ in phone_items[:20]:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            h, w = image.shape[:2]
            if max(h, w) != 640:
                s = 640 / max(h, w)
                image = cv2.resize(image, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
                h, w = image.shape[:2]
            top, left = (640 - h) // 2, (640 - w) // 2
            padded = np.full((640, 640, 3), 114, dtype=np.uint8)
            padded[top : top + h, left : left + w] = image
            plain, _ = det.detect(image)
            boxed, _ = det.detect(padded)
            images += 1
            assert len(plain) == len(boxed), path.name
            for p, q in zip(plain, boxed):
                mapped = (
                    min(1.0, max(0.0, (q.x_min * 640 - left) / w)),
                    min(1.0, max(0.0, (q.y_min * 640 - top) / h)),
                    min(1.0, max(0.0, (q.x_max * 640 - left) / w)),
                    min(1.0, max(0.0, (q.y_max * 640 - top) / h)),
                )
                assert np.allclose(_xyxy(p), mapped, atol=1e-4), (path.name, p, q)
                assert abs(p.confidence - q.confidence) <= 1e-6
                compared += 1
    finally:
        det.close()
    print(f"\n[A03 eval geometry] {images} phone-labelled images, {compared} phone boxes (conf>={EVAL_FLOOR_CONF}) compared exactly")
    assert images > 0
