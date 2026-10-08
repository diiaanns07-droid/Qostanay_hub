"""A03: PhoneConfig, model manifest + local weight verification, prepare CLI, missing-model health.

These tests check configuration/integrity/lifecycle LOGIC only. No test here runs the detector on an
image and nothing here says anything about CV accuracy (recall/precision of phone detection). The two
``real_model`` tests only check that the locally installed file passes size/SHA-256 verification and
that an ONNX Runtime session can be created from it; they are skipped when the weights are absent.

A phone in the frame is not evidence that anything was photographed; error observations here carry
``unknown`` signals, never ``absent``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import re
import socket
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import jsonschema
import pytest
from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.phone import PhoneAnalyzer, PhoneConfig, create_phone_analyzer, prepare
from proctor.phone.config import CONFIG_SCHEMA
from proctor.phone.manifest import (
    MANIFEST_PATH,
    display_path,
    load_manifest,
    model_path,
    verify_model_file,
)
from proctor.phone.tests.helpers import SESSION, make_frame
from proctor.settings import PROCTORING_ROOT, Settings
from proctor_contracts.v1 import (
    Component,
    Health,
    HealthReport,
    HealthStatus,
    ModelManifest,
    ObservationStatus,
    PhoneObservation,
    PhoneSignalName,
    Producer,
    SignalState,
    SourceMode,
)

SCHEMA = json.loads((PROCTORING_ROOT / "contracts" / "schema" / "v1" / "qorgau.v1.schema.json").read_text(encoding="utf-8"))
TOKEN = "t" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PREPARE_CMD = "python -m proctor.phone.prepare"


def _schema_validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"})


def _files_under(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _sparse_zeros(path: Path, size: int) -> None:
    """A file of ``size`` zero bytes without writing them (sparse where supported)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size)


# --------------------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _no_phone_env(monkeypatch):
    """Tests must not depend on QORGAU_PHONE_* overrides from the developer's shell."""
    for key in list(os.environ):
        if key.startswith("QORGAU_PHONE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def no_network(monkeypatch) -> list[str]:
    """Any outbound connection attempt is recorded (and refused). Tests assert the list stays empty,
    because a broad ``except Exception`` in the code under test could swallow the refusal."""
    calls: list[str] = []

    def deny(name: str):
        def _deny(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"network access attempted: {name}")

        return _deny

    monkeypatch.setattr(urllib.request, "urlopen", deny("urllib.request.urlopen"))
    monkeypatch.setattr(socket.socket, "connect", deny("socket.connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", deny("socket.connect_ex"))
    monkeypatch.setattr(socket, "create_connection", deny("socket.create_connection"))
    return calls


@pytest.fixture()
def no_camera(monkeypatch) -> list[str]:
    cv2 = pytest.importorskip("cv2")
    calls: list[str] = []

    def _deny(*args, **kwargs):
        calls.append("cv2.VideoCapture")
        raise AssertionError("phone analyzer must never open a camera")

    monkeypatch.setattr(cv2, "VideoCapture", _deny)
    return calls


@pytest.fixture(scope="module")
def manifest() -> ModelManifest:
    return load_manifest()


# ===================================================================================== PhoneConfig


def test_default_config_is_valid_and_fits_contract_limits():
    cfg = PhoneConfig()
    assert cfg.input_size % 32 == 0 and 160 <= cfg.input_size <= 1280
    assert 1 <= cfg.max_detections <= 16  # PhoneObservation.detections max_length
    assert cfg.execution_provider == "cpu"
    assert cfg.phone_class_names == ("cell phone",)
    assert cfg.raise_exit_y >= cfg.raise_zone_y_max
    assert cfg.track_history_ms >= max(cfg.raise_window_ms, cfg.capture_steady_ms)
    # config_version fits Producer.config_version (max_length 64) and names the schema
    assert re.fullmatch(rf"{re.escape(CONFIG_SCHEMA)}\.[0-9a-f]{{12}}", cfg.config_version)
    Producer(module="phone", version="0.1.0", config_version=cfg.config_version)
    data = cfg.as_dict()
    assert data["phone_class_names"] == ["cell phone"]
    json.dumps(data)  # serializable for reports


def test_config_is_frozen():
    cfg = PhoneConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.conf_threshold = 0.9  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"input_size": 500}, "input_size"),  # not a multiple of 32
        ({"input_size": 128}, "input_size"),  # multiple of 32 but below 160
        ({"input_size": 1312}, "input_size"),  # above 1280
        ({"conf_threshold": 0.0}, "conf_threshold"),
        ({"conf_threshold": 1.5}, "conf_threshold"),
        ({"conf_threshold": float("nan")}, "conf_threshold"),
        ({"nms_iou": -0.1}, "nms_iou"),
        ({"high_conf_single": 2.0}, "high_conf_single"),
        ({"capture_min_hit_ratio": 0.0}, "capture_min_hit_ratio"),
        ({"edge_margin": 1.5}, "edge_margin"),
        ({"raise_zone_y_max": 0.70, "raise_exit_y": 0.65}, "raise_exit_y"),  # hysteresis inverted
        ({"raise_exit_y": 0.50}, "raise_exit_y"),  # below the default zone 0.60
        ({"track_history_ms": 1000.0}, "track_history_ms"),  # shorter than raise_window_ms 1500
        ({"capture_steady_ms": 5000.0}, "track_history_ms"),  # history 3000 cannot cover 5000
        ({"track_recent_len": 0}, ">= 1"),
        ({"track_confirm_hits": 0}, ">= 1"),
        ({"intra_op_threads": 0}, ">= 1"),
        ({"max_detections": 0}, "max_detections"),
        ({"max_detections": 17}, "max_detections"),
        ({"execution_provider": "cuda"}, "execution_provider"),
        ({"execution_provider": "CPU"}, "execution_provider"),
        ({"capture_zone_x_min": 0.8, "capture_zone_x_max": 0.2}, "capture_zone_x_min"),
        ({"phone_class_names": ()}, "phone_class_names"),
        ({"stale_ms": -1.0}, "stale_ms"),
        ({"min_interval_ms": -5.0}, "min_interval_ms"),
    ],
)
def test_config_rejects_bad_values(kwargs, fragment):
    with pytest.raises(ValueError, match=re.escape(fragment)):
        PhoneConfig(**kwargs)


def test_config_hysteresis_boundary_is_allowed():
    cfg = PhoneConfig(raise_zone_y_max=0.6, raise_exit_y=0.6)
    assert cfg.raise_exit_y == cfg.raise_zone_y_max


def test_config_reports_all_problems_at_once():
    with pytest.raises(ValueError) as info:
        PhoneConfig(input_size=500, execution_provider="gpu", conf_threshold=0.0)
    text = str(info.value)
    assert "input_size" in text and "execution_provider" in text and "conf_threshold" in text
    assert text.count(";") >= 2


def test_config_version_is_stable_across_instances():
    a, b = PhoneConfig(), PhoneConfig()
    assert a is not b and a.config_version == b.config_version
    assert PhoneConfig.from_env({}).config_version == a.config_version
    # the same values reached via env or kwargs give the same version
    via_env = PhoneConfig.from_env({"QORGAU_PHONE_CONF_THRESHOLD": "0.3"})
    assert via_env.config_version == PhoneConfig(conf_threshold=0.3).config_version


def test_config_version_changes_when_any_value_changes():
    variants = [
        PhoneConfig(),
        PhoneConfig(conf_threshold=0.3),
        PhoneConfig(input_size=480),
        PhoneConfig(rect=False),
        PhoneConfig(execution_provider="auto"),
        PhoneConfig(phone_class_names=("cell phone", "remote")),
        PhoneConfig(raise_zone_y_max=0.5),
        PhoneConfig(capture_steady_ms=900.0),
        PhoneConfig(track_confirm_hits=3),
    ]
    versions = [v.config_version for v in variants]
    assert len(set(versions)) == len(versions), versions


# ---------------------------------------------------------------------------------- from_env


def test_from_env_coerces_int_float_bool_tuple_and_str():
    cfg = PhoneConfig.from_env(
        {
            "QORGAU_PHONE_INPUT_SIZE": " 480 ",
            "QORGAU_PHONE_CONF_THRESHOLD": "0.3",
            "QORGAU_PHONE_MIN_INTERVAL_MS": "50",
            "QORGAU_PHONE_RECT": "false",
            "QORGAU_PHONE_PHONE_CLASS_NAMES": "cell phone, remote ,",
            "QORGAU_PHONE_EXECUTION_PROVIDER": " auto ",
            "QORGAU_PHONE_WARMUP_RUNS": "0",
        }
    )
    assert cfg.input_size == 480 and type(cfg.input_size) is int
    assert cfg.conf_threshold == pytest.approx(0.3) and type(cfg.conf_threshold) is float
    assert cfg.min_interval_ms == 50.0 and type(cfg.min_interval_ms) is float
    assert cfg.rect is False
    assert cfg.phone_class_names == ("cell phone", "remote")
    assert cfg.execution_provider == "auto"
    assert cfg.warmup_runs == 0
    # untouched fields keep their defaults
    assert cfg.nms_iou == PhoneConfig().nms_iou


@pytest.mark.parametrize(
    "raw, expected",
    [("1", True), ("true", True), ("YES", True), (" On ", True), ("0", False), ("False", False), ("no", False), ("OFF", False)],
)
def test_from_env_bool_spellings(raw, expected):
    assert PhoneConfig.from_env({"QORGAU_PHONE_RECT": raw}).rect is expected


@pytest.mark.parametrize(
    "var, raw",
    [
        ("QORGAU_PHONE_INPUT_SIZE", "abc"),
        ("QORGAU_PHONE_INPUT_SIZE", "12.5"),
        ("QORGAU_PHONE_MAX_DETECTIONS", ""),
        ("QORGAU_PHONE_CONF_THRESHOLD", "high"),
        ("QORGAU_PHONE_RECT", "maybe"),
        ("QORGAU_PHONE_PHONE_CLASS_NAMES", " , ,"),
    ],
)
def test_from_env_garbage_raises_value_error_naming_the_variable(var, raw):
    with pytest.raises(ValueError) as info:
        PhoneConfig.from_env({var: raw})
    assert var in str(info.value)
    assert repr(raw) in str(info.value)


def test_from_env_well_typed_but_invalid_value_is_rejected():
    with pytest.raises(ValueError, match="input_size"):
        PhoneConfig.from_env({"QORGAU_PHONE_INPUT_SIZE": "500"})


def test_from_env_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("QORGAU_PHONE_INPUT_SIZE", "320")
    assert PhoneConfig.from_env().input_size == 320


def test_from_env_explicit_overrides_win_over_env():
    cfg = PhoneConfig.from_env({"QORGAU_PHONE_CONF_THRESHOLD": "0.3"}, conf_threshold=0.4)
    assert cfg.conf_threshold == 0.4


@pytest.mark.parametrize(
    "var",
    [
        "QORGAU_PHONE_MIN_INTERVAL_MS",
        "QORGAU_PHONE_STALE_MS",
        "QORGAU_PHONE_TRACK_MAX_MISS_MS",
        "QORGAU_PHONE_TRACK_HISTORY_MS",
        "QORGAU_PHONE_CAPTURE_STEADY_MS",
    ],
)
def test_from_env_rejects_nan_durations(var):
    with pytest.raises(ValueError):
        PhoneConfig.from_env({var: "nan"})


# =========================================================================== factory + config_invalid


def test_factory_is_cheap_and_applies_valid_env(monkeypatch, settings, no_network, no_camera):
    monkeypatch.setenv("QORGAU_PHONE_CONF_THRESHOLD", "0.35")
    analyzer = create_phone_analyzer(settings)
    assert isinstance(analyzer, PhoneAnalyzer) and analyzer.name == "phone"
    assert analyzer.config.conf_threshold == 0.35
    before = analyzer.health()  # no I/O happened yet
    assert before.status == HealthStatus.STARTING and before.code == "not_loaded"
    assert no_network == [] and no_camera == []


@pytest.mark.parametrize(
    "var, raw, fragment",
    [
        ("QORGAU_PHONE_INPUT_SIZE", "abc", "QORGAU_PHONE_INPUT_SIZE"),
        ("QORGAU_PHONE_RECT", "maybe", "QORGAU_PHONE_RECT"),
        ("QORGAU_PHONE_INPUT_SIZE", "500", "input_size"),
        ("QORGAU_PHONE_EXECUTION_PROVIDER", "cuda", "execution_provider"),
    ],
)
def test_factory_invalid_env_reports_config_invalid(monkeypatch, settings, no_network, no_camera, var, raw, fragment):
    monkeypatch.setenv(var, raw)
    analyzer = create_phone_analyzer(settings)  # must not raise
    assert analyzer.config == PhoneConfig()  # falls back to defaults, but stays unavailable
    health = analyzer.load()
    assert health.component == Component.PHONE
    assert health.status == HealthStatus.UNAVAILABLE
    assert health.code == "config_invalid"
    assert fragment in health.message
    assert analyzer.health() == health
    Health.model_validate(health.model_dump(mode="json"))
    _schema_validator("Health").validate(health.model_dump(mode="json"))
    assert no_network == [] and no_camera == []
    assert not settings.models_dir.exists()

    # frames still produce an honest error observation (gap stays visible, never "absent")
    analyzer.start_session(SESSION, SourceMode.REPLAY)
    (obs,) = analyzer.process(make_frame(0))
    assert obs.status == ObservationStatus.ERROR
    assert {s.state for s in obs.signals} == {SignalState.UNKNOWN}
    assert {s.reason for s in obs.signals} == {"model_unavailable"}
    assert obs.signals[0].facts["detail"] == "config_invalid"
    PhoneObservation.model_validate(obs.model_dump(mode="json"))


# ============================================================================ committed manifest


def test_committed_manifest_is_a_valid_contract_model_manifest(manifest):
    assert MANIFEST_PATH.name == "models.manifest.json"
    assert MANIFEST_PATH.parent == Path(__file__).resolve().parents[1]  # committed next to the module code
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    parsed = ModelManifest.model_validate(raw)
    _schema_validator("ModelManifest").validate(raw)
    assert parsed == manifest

    assert manifest.module == "phone"
    assert manifest.format == "onnx"
    assert manifest.task == "object_detection"
    url = urlparse(manifest.source_url)
    assert url.scheme == "https" and url.netloc == "github.com"
    assert url.path.startswith("/ultralytics/assets/")
    assert manifest.source_url.endswith(".onnx")
    assert manifest.license == "AGPL-3.0"
    assert re.fullmatch(r"[0-9a-f]{64}", manifest.sha256)
    assert manifest.size_bytes > 0
    assert manifest.class_names is not None and "cell phone" in manifest.class_names.values()
    assert all(k.isdigit() for k in manifest.class_names)
    # weights live under models_dir, never inside the package; relative path without dot segments
    assert manifest.file == "phone/yolo11n.onnx"
    assert not Path(manifest.file).is_absolute()
    assert all(part not in (".", "..") for part in manifest.file.split("/"))


def test_display_path_hides_parent_directories(tmp_path, manifest):
    models_dir = tmp_path / "models"
    assert display_path(models_dir, manifest) == "models/phone/yolo11n.onnx"
    assert str(tmp_path) not in display_path(models_dir, manifest)
    assert model_path(models_dir, manifest) == models_dir / "phone" / "yolo11n.onnx"


# ============================================================================== verify_model_file


def test_verify_missing_model(tmp_path, manifest):
    models_dir = tmp_path / "models"
    check = verify_model_file(models_dir, manifest)
    assert not check.ok and check.code == "model_missing"
    assert "models/phone/yolo11n.onnx" in check.message
    assert PREPARE_CMD in check.message
    assert manifest.model_id in check.message
    assert str(tmp_path) not in check.message
    assert check.path == models_dir / manifest.file
    assert check.actual_sha256 is None
    assert not models_dir.exists()  # verification never creates anything


def test_verify_directory_in_place_of_model_is_missing(tmp_path, manifest):
    models_dir = tmp_path / "models"
    (models_dir / manifest.file).mkdir(parents=True)
    check = verify_model_file(models_dir, manifest)
    assert not check.ok and check.code == "model_missing"


def test_verify_wrong_size_is_invalid(tmp_path, manifest):
    models_dir = tmp_path / "models"
    target = models_dir / manifest.file
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * 100)
    check = verify_model_file(models_dir, manifest)
    assert not check.ok and check.code == "model_invalid"
    assert "size 100" in check.message and str(manifest.size_bytes) in check.message
    assert str(tmp_path) not in check.message
    assert check.actual_sha256 is None  # size mismatch short-circuits hashing


def test_verify_correct_size_wrong_bytes_is_invalid(tmp_path, manifest):
    models_dir = tmp_path / "models"
    target = models_dir / manifest.file
    _sparse_zeros(target, manifest.size_bytes)
    assert target.stat().st_size == manifest.size_bytes
    check = verify_model_file(models_dir, manifest)
    assert not check.ok and check.code == "model_invalid"
    assert "sha256" in check.message
    assert check.actual_sha256 == hashlib.sha256(bytes(manifest.size_bytes)).hexdigest()
    assert check.actual_sha256 != manifest.sha256
    assert str(tmp_path) not in check.message


def test_model_path_rejects_absolute_file_in_unvalidated_manifest(tmp_path, manifest):
    rogue = manifest.model_copy(update={"file": "/etc/passwd"})  # model_copy skips validation
    with pytest.raises(ValueError):
        model_path(tmp_path / "models", rogue)
    assert verify_model_file(tmp_path / "models", rogue).code == "manifest_invalid"


def test_model_path_rejects_parent_traversal_in_unvalidated_manifest(tmp_path, manifest):
    rogue = manifest.model_copy(update={"file": "../outside/yolo11n.onnx"})  # bypasses the contract validator
    assert verify_model_file(tmp_path / "models", rogue).code == "manifest_invalid"


# ===================================================================== analyzer load(): missing model


def test_load_with_empty_models_dir_is_unavailable_offline(settings, tmp_path, no_network, no_camera):
    analyzer = create_phone_analyzer(settings)
    health = analyzer.load()  # never raises
    assert health.component == Component.PHONE
    assert health.status == HealthStatus.UNAVAILABLE
    assert health.code == "model_missing"
    assert "models/phone/yolo11n.onnx" in health.message
    assert PREPARE_CMD in health.message
    assert str(tmp_path) not in health.message
    assert health.details.get("model_id") == "yolo11n-coco-onnx"
    assert health.details.get("license") == "AGPL-3.0"
    assert analyzer.health() == health
    _schema_validator("Health").validate(health.model_dump(mode="json"))

    # nothing was downloaded, written or opened
    assert no_network == []
    assert no_camera == []
    assert not settings.models_dir.exists()

    # idempotent and still offline on a second call
    assert analyzer.load() == health
    assert no_network == []

    analyzer.close()
    assert analyzer.health().status == HealthStatus.STOPPED


def _write_manifest(tmp_path: Path, **changes) -> Path:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    data.update(changes)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "make_manifest",
    [
        pytest.param(lambda tmp: (tmp / "bad.json", (tmp / "bad.json").write_text("{not json", encoding="utf-8"))[0], id="broken_json"),
        pytest.param(lambda tmp: (tmp / "empty.json", (tmp / "empty.json").write_text("", encoding="utf-8"))[0], id="empty_file"),
        pytest.param(lambda tmp: tmp / "does-not-exist.json", id="missing_file"),
        pytest.param(lambda tmp: _write_manifest(tmp, file="../outside/yolo11n.onnx"), id="dotdot_file"),
        pytest.param(lambda tmp: _write_manifest(tmp, file="phone/../../yolo11n.onnx"), id="dotdot_inside"),
        pytest.param(lambda tmp: _write_manifest(tmp, file="/abs/yolo11n.onnx"), id="absolute_file"),
        pytest.param(lambda tmp: _write_manifest(tmp, module="attention"), id="wrong_module"),
        pytest.param(lambda tmp: _write_manifest(tmp, format="pt"), id="wrong_format"),
        pytest.param(lambda tmp: _write_manifest(tmp, sha256="XYZ"), id="bad_sha"),
        pytest.param(lambda tmp: _write_manifest(tmp, source_url="http://example.com/yolo11n.onnx"), id="http_source"),
        pytest.param(lambda tmp: _write_manifest(tmp, unexpected_key=1), id="extra_key"),
    ],
)
def test_load_with_invalid_manifest_is_manifest_invalid(settings, tmp_path, no_network, make_manifest):
    manifest_path = make_manifest(tmp_path)
    # plant a file where a '..' manifest would point, to show it is never used
    outside = tmp_path / "outside" / "yolo11n.onnx"
    outside.parent.mkdir(exist_ok=True)
    outside.write_bytes(b"not a model")
    analyzer = PhoneAnalyzer(settings, manifest_path=manifest_path)
    health = analyzer.load()  # never raises
    assert health.status == HealthStatus.UNAVAILABLE
    assert health.code == "manifest_invalid"
    assert analyzer.manifest is None
    assert analyzer.health() == health
    assert no_network == []
    Health.model_validate(health.model_dump(mode="json"))


def test_process_after_missing_model_returns_one_error_observation(settings, no_network, no_camera):
    analyzer = create_phone_analyzer(settings)
    assert analyzer.load().code == "model_missing"
    analyzer.start_session(SESSION, SourceMode.REPLAY)

    out = analyzer.process(make_frame(0))
    assert len(out) == 1
    obs = out[0]
    assert isinstance(obs, PhoneObservation)
    assert obs.kind == "phone"
    assert obs.observation_id == "phone-0"
    assert obs.session_id == SESSION and obs.frame_id == 0
    assert obs.status == ObservationStatus.ERROR
    assert obs.detections == []
    assert obs.quality is None
    assert obs.quality_flags == ["model_unavailable"]
    assert [s.name for s in obs.signals] == [
        PhoneSignalName.PHONE_VISIBLE,
        PhoneSignalName.PHONE_RAISED,
        PhoneSignalName.POSSIBLE_SCREEN_CAPTURE,
    ]
    for s in obs.signals:
        assert s.state == SignalState.UNKNOWN  # unknown != absent
        assert s.reason == "model_unavailable"
        assert s.confidence is None
        assert s.facts.get("detail") == "model_missing"
    # no model was involved: the producer must not claim one
    assert obs.producer.module == "phone"
    assert obs.producer.model_id is None and obs.producer.model_sha256 is None
    assert obs.producer.config_version == analyzer.config.config_version

    wire = obs.model_dump(mode="json")
    assert PhoneObservation.model_validate(wire) == obs
    _schema_validator("PhoneObservation").validate(wire)

    # every later frame is reported the same way; health keeps telling the truth
    (obs1,) = analyzer.process(make_frame(1))
    assert obs1.observation_id == "phone-1" and obs1.status == ObservationStatus.ERROR
    health = analyzer.health()
    assert health.status == HealthStatus.UNAVAILABLE and health.code == "model_missing"
    assert no_network == [] and no_camera == []


# ======================================================================================= prepare


def _chunks(*parts: bytes, consumed: list[int] | None = None):
    for i, part in enumerate(parts):
        if consumed is not None:
            consumed.append(i)
        yield part


def test_install_success_writes_atomically(tmp_path):
    data = b"abcdefg"
    target = tmp_path / "models" / "phone" / "m.onnx"
    sha = hashlib.sha256(data).hexdigest()
    actual = prepare._install(_chunks(b"abc", b"", b"defg"), target, sha, len(data))
    assert actual == sha
    assert target.read_bytes() == data
    assert _files_under(tmp_path / "models") == ["phone/m.onnx"]  # no .part left behind


def test_install_replaces_a_stale_part_file(tmp_path):
    data = b"payload"
    target = tmp_path / "models" / "phone" / "m.onnx"
    stale = target.with_name(target.name + ".part")
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"left over from a crashed run" * 10)
    prepare._install(_chunks(data), target, hashlib.sha256(data).hexdigest(), len(data))
    assert target.read_bytes() == data
    assert not stale.exists()


def test_install_size_overflow_stops_early_and_leaves_nothing(tmp_path):
    target = tmp_path / "models" / "phone" / "m.onnx"
    consumed: list[int] = []
    expected = b"abcde"
    with pytest.raises(ValueError, match="larger"):
        prepare._install(_chunks(b"abc", b"defg", b"never read", consumed=consumed), target, hashlib.sha256(expected).hexdigest(), 5)
    assert consumed == [0, 1]  # the oversized source is not read to the end
    assert not target.exists()
    assert _files_under(tmp_path / "models") == []


def test_install_sha_mismatch_leaves_nothing(tmp_path):
    target = tmp_path / "models" / "phone" / "m.onnx"
    wrong_sha = hashlib.sha256(b"abcdefX").hexdigest()
    with pytest.raises(ValueError, match="verification failed"):
        prepare._install(_chunks(b"abc", b"defg"), target, wrong_sha, 7)
    assert not target.exists()
    assert _files_under(tmp_path / "models") == []


def test_install_short_source_leaves_nothing(tmp_path):
    target = tmp_path / "models" / "phone" / "m.onnx"
    with pytest.raises(ValueError, match="verification failed"):
        prepare._install(_chunks(b"abc"), target, hashlib.sha256(b"abcdefg").hexdigest(), 7)
    assert _files_under(tmp_path / "models") == []


def test_install_source_error_leaves_nothing_and_keeps_existing_target(tmp_path):
    target = tmp_path / "models" / "phone" / "m.onnx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous file")

    def broken():
        yield b"abc"
        raise OSError("connection reset")

    with pytest.raises(OSError, match="connection reset"):
        prepare._install(broken(), target, hashlib.sha256(b"abcdefg").hexdigest(), 7)
    assert target.read_bytes() == b"previous file"  # failed install never touches the target
    assert _files_under(tmp_path / "models") == ["phone/m.onnx"]


def test_prepare_from_wrong_file_returns_1_and_leaves_nothing(tmp_path, capsys, no_network):
    src = tmp_path / "usb" / "yolo11n.onnx"
    src.parent.mkdir()
    src.write_bytes(b"definitely not the model" * 4)
    models_dir = tmp_path / "models"
    rc = prepare.main(["--from-file", str(src), "--models-dir", str(models_dir)])
    assert rc == 1
    assert "[error]" in capsys.readouterr().out
    assert _files_under(models_dir) == []
    assert src.read_bytes() == b"definitely not the model" * 4  # source untouched
    assert no_network == []


def test_prepare_from_full_size_wrong_file_returns_1_and_leaves_nothing(tmp_path, capsys, manifest, no_network):
    src = tmp_path / "usb" / "yolo11n.onnx"
    _sparse_zeros(src, manifest.size_bytes)
    models_dir = tmp_path / "models"
    assert prepare.main(["--from-file", str(src), "--models-dir", str(models_dir)]) == 1
    out = capsys.readouterr().out
    assert "[error]" in out and "verification failed" in out
    assert _files_under(models_dir) == []
    assert no_network == []


def test_prepare_from_missing_file_returns_1(tmp_path, capsys, no_network):
    models_dir = tmp_path / "models"
    rc = prepare.main(["--from-file", str(tmp_path / "nope.onnx"), "--models-dir", str(models_dir)])
    assert rc == 1
    assert "not a file" in capsys.readouterr().out
    assert _files_under(models_dir) == []


def test_prepare_check_missing_returns_1(tmp_path, capsys, no_network):
    models_dir = tmp_path / "models"
    assert prepare.main(["--check", "--models-dir", str(models_dir)]) == 1
    out = capsys.readouterr().out
    assert "[model_missing]" in out
    assert not models_dir.exists()  # --check is read-only
    assert no_network == []


def test_prepare_check_wrong_file_returns_1(tmp_path, capsys, manifest, no_network):
    models_dir = tmp_path / "models"
    target = models_dir / manifest.file
    target.parent.mkdir(parents=True)
    target.write_bytes(b"junk")
    assert prepare.main(["--check", "--models-dir", str(models_dir)]) == 1
    assert "[model_invalid]" in capsys.readouterr().out
    assert target.read_bytes() == b"junk"  # --check never deletes or rewrites


def test_prepare_download_with_wrong_bytes_returns_1_and_leaves_nothing(tmp_path, capsys, monkeypatch, manifest):
    """--download is the only path that opens a URL; simulated here (no network) with a wrong payload."""
    opened: list[tuple[str, object]] = []

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    def fake_urlopen(url, timeout=None):
        opened.append((url, timeout))
        return FakeResponse(b"this is not the yolo11n model")

    monkeypatch.setattr(prepare.urllib.request, "urlopen", fake_urlopen)
    models_dir = tmp_path / "models"
    assert prepare.main(["--download", "--models-dir", str(models_dir)]) == 1
    assert opened == [(manifest.source_url, 60)]
    assert "[error]" in capsys.readouterr().out
    assert _files_under(models_dir) == []


@pytest.mark.parametrize("argv", [[], ["--check", "--download"], ["--check", "--from-file", "x"]])
def test_prepare_requires_exactly_one_mode(argv, tmp_path, no_network):
    with pytest.raises(SystemExit) as info:
        prepare.main([*argv, "--models-dir", str(tmp_path / "models")])
    assert info.value.code == 2
    assert no_network == []
    assert not (tmp_path / "models").exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.onnx",
        "file:///etc/passwd",
        "ftp://example.com/yolo11n.onnx",
        "//github.com/ultralytics/assets/yolo11n.onnx",
        "HTTP://example.com/x.onnx",
        "",
    ],
)
def test_iter_url_rejects_non_https(url, no_network):
    with pytest.raises(ValueError, match="https"):
        next(prepare._iter_url(url))
    assert no_network == []


# ================================================================= real installed weights (optional)


@pytest.mark.real_model
def test_real_installed_model_verifies(real_models_dir, manifest):
    """Integrity only (size + SHA-256). Says nothing about detection accuracy."""
    check = verify_model_file(real_models_dir, manifest)
    assert check.ok and check.code == "model_ok"
    assert check.actual_sha256 == manifest.sha256
    assert str(real_models_dir.parent) not in check.message


@pytest.mark.real_model
def test_real_prepare_from_file_installs_and_check_loads(real_models_dir, manifest, tmp_path, capsys, no_network, no_camera):
    """Offline install of the verified file into an empty models dir, then --check builds an ORT CPU session.
    Integrity/loadability only; no image is inferred and no accuracy is claimed."""
    models_dir = tmp_path / "models"
    src = real_models_dir / manifest.file
    assert prepare.main(["--from-file", str(src), "--models-dir", str(models_dir)]) == 0
    assert _files_under(models_dir) == [manifest.file]
    assert verify_model_file(models_dir, manifest).ok
    capsys.readouterr()
    assert prepare.main(["--check", "--models-dir", str(models_dir)]) == 0
    out = capsys.readouterr().out
    assert "[model_ok]" in out and "[model_loaded]" in out
    assert no_network == [] and no_camera == []


# ============================================================= integration through A01's app factory


def _app_settings(tmp_path: Path) -> Settings:
    return Settings(models_dir=tmp_path / "models", data_dir=tmp_path / "data", exam_path=tmp_path / "missing.json")


def _phone_component(body: dict) -> dict:
    phone = [c for c in body["components"] if c["component"] == "phone"]
    assert len(phone) == 1, body["components"]
    return phone[0]


def test_app_health_lists_phone_model_missing(tmp_path, no_network):
    app = create_app(_app_settings(tmp_path), TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        r = client.get("/v1/health")
    assert r.status_code == 200, r.text
    body = r.json()
    HealthReport.model_validate(body)
    phone = _phone_component(body)
    assert phone["status"] == "unavailable"
    assert phone["code"] == "model_missing"
    assert "models/phone/yolo11n.onnx" in phone["message"]
    assert str(tmp_path) not in phone["message"]
    assert body["overall"] != "ok"  # a missing model never reads as "all clear"
    assert no_network == []
    assert not (tmp_path / "models").exists()


def test_app_health_lists_phone_config_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("QORGAU_PHONE_INPUT_SIZE", "abc")
    app = create_app(_app_settings(tmp_path), TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        body = client.get("/v1/health").json()
    phone = _phone_component(body)
    assert phone["status"] == "unavailable"
    assert phone["code"] == "config_invalid"
    assert "QORGAU_PHONE_INPUT_SIZE" in phone["message"]
