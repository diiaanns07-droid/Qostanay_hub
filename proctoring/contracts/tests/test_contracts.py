"""Contract v1 compatibility tests (owner: A01). Run: python -m pytest contracts/tests"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from proctor_contracts import v1

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "fixtures" / "v1").glob("*.json"))
SCHEMA = json.loads((ROOT / "schema" / "v1" / "qorgau.v1.schema.json").read_text(encoding="utf-8"))
MODELS = {m.__name__: m for m in v1.WIRE_MODELS}

# Types every team builds against must have at least one fixture.
REQUIRED_FIXTURE_TYPES = {
    "FramePacketMeta", "PreviewFrameMeta", "PhoneObservation", "AttentionObservation",
    "EnvironmentObservation", "HealthObservation", "Incident", "IncidentChange", "IncidentDetail",
    "HumanReview", "HumanReviewCreate", "ModelManifest", "SessionCreate", "SessionInfo",
    "PreflightReport", "CalibrationState", "HealthReport", "EnvironmentEventBatch",
    "EnvironmentCapabilities", "ExamDefinition", "AnswerUpsert", "RuntimeMetrics",
    "StreamEnvelope", "ApiError", "SessionSummary", "ExportManifest",
}


def _validator(name: str) -> jsonschema.Draft202012Validator:
    schema = {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"}
    return jsonschema.Draft202012Validator(schema)


def test_generated_files_are_up_to_date() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "generate.py"), "--check"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_every_required_type_has_a_fixture() -> None:
    covered = {p.stem.split(".")[0] for p in FIXTURES}
    assert REQUIRED_FIXTURE_TYPES <= covered, REQUIRED_FIXTURE_TYPES - covered


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_valid_in_pydantic_and_json_schema(path: Path) -> None:
    model_name = path.stem.split(".")[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    parsed = MODELS[model_name].model_validate(data)
    dumped = parsed.model_dump(mode="json")
    _validator(model_name).validate(data)
    _validator(model_name).validate(dumped)
    assert MODELS[model_name].model_validate(dumped) == parsed  # round-trip stable


def test_fixtures_are_never_labelled_live() -> None:
    """Fixtures are examples; a fixture must never look like a LIVE observation."""
    for path in FIXTURES:
        assert '"live"' not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize(
    "model,payload",
    [
        (v1.BBox, {"x_min": 0.6, "y_min": 0.1, "x_max": 0.5, "y_max": 0.2}),  # inverted
        (v1.BBox, {"x_min": -0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.2}),  # not normalized
        (v1.SourceConfig, {"mode": "replay"}),  # replay requires replay_id
        (v1.SourceConfig, {"mode": "replay", "replay_id": "../../etc/passwd"}),  # ids are not paths
        (v1.HumanReviewCreate, {"decision": "guilty", "operator": "t"}),  # no such decision
        (v1.EnvironmentDetail, {"window_title": "secret"}),  # extra fields rejected
        (v1.EnvironmentDetail, {"process_name": "C:\\Users\\x\\chrome.exe"}),  # basename only
        (v1.ModelManifest, {"model_id": "m", "module": "phone", "task": "t", "file": "../w.onnx", "format": "onnx",
                            "version": "1", "source_url": "https://x", "license": "x", "sha256": "0" * 64,
                            "size_bytes": 1, "prepared_at": "2026-10-08T00:00:00Z"}),  # traversal in file
    ],
)
def test_invalid_payloads_rejected(model: type, payload: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.ConsentRecord.model_validate({"accepted": True, "text_version": "x", "accepted_at": "2026-10-08T09:00:00"})


def test_observation_union_discriminates_by_kind() -> None:
    from pydantic import TypeAdapter

    adapter = TypeAdapter(v1.Observation)
    data = json.loads((ROOT / "fixtures" / "v1" / "AttentionObservation.gaze_down.json").read_text(encoding="utf-8"))
    assert isinstance(adapter.validate_python(data), v1.AttentionObservation)
    data["kind"] = "phone"
    with pytest.raises(ValidationError):
        adapter.validate_python(data)


def test_unknown_is_not_absent() -> None:
    """face_count=None (unknown) must stay distinguishable from 0 (no face)."""
    data = json.loads((ROOT / "fixtures" / "v1" / "AttentionObservation.unknown_low_light.json").read_text(encoding="utf-8"))
    obs = v1.AttentionObservation.model_validate(data)
    assert obs.face_count is None and obs.primary_face_present is None and obs.status == v1.ObservationStatus.UNKNOWN


def test_contract_version_exposed_everywhere() -> None:
    ts = (ROOT / "ts" / "qorgau-v1.generated.ts").read_text(encoding="utf-8")
    assert f'CONTRACT_VERSION = "{v1.CONTRACT_VERSION}"' in ts
    assert SCHEMA["x-contract-version"] == v1.CONTRACT_VERSION
