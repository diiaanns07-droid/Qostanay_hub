"""Self-check of the A09 harness (kept separate from product results, prompt: "document the check of
your test harness separately"). If these fail, no product verdict from qa/ can be trusted."""

from __future__ import annotations

import copy
import json
import struct
import subprocess
import sys

import httpx
import jsonschema
import pytest

from qorgau_qa import contract
from qorgau_qa.backend import PROCTORING_ROOT, BackendProcess, BackendStartError, backend_env, session_create_body, CONSENT
from qorgau_qa.stream import parse_preview_frame

FIXTURES = PROCTORING_ROOT / "contracts" / "fixtures" / "v1"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_validator_accepts_every_contract_fixture(path):
    contract.validate(path.stem.split(".")[0], json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(unexpected=1),
        lambda d: d.update(state="hacked"),
        lambda d: d.update(session_id="../etc"),
        lambda d: d.update(created_at="2026-10-08T09:00:00"),
        lambda d: d.pop("source_mode"),
    ],
    ids=["extra", "bad_enum", "traversal_id", "naive_datetime", "missing_field"],
)
def test_validator_rejects_mutated_payloads(mutate):
    data = copy.deepcopy(_fixture("SessionInfo.running.json"))
    mutate(data)
    with pytest.raises((jsonschema.ValidationError, ValueError)):
        contract.validate("SessionInfo", data)


def test_both_validators_are_independent():
    """JSON Schema alone must reject what Pydantic rejects (and vice versa) for a strict case."""
    data = copy.deepcopy(_fixture("SessionInfo.running.json"))
    data["unexpected"] = 1
    with pytest.raises(jsonschema.ValidationError):
        contract._validator("SessionInfo").validate(data)
    with pytest.raises(ValueError):
        contract.models()["SessionInfo"].model_validate(data)


@pytest.mark.parametrize("zone", ["green", "yellow", "red", "grey", None])
def test_review_summary_extension_is_explicit_and_nullable(zone):
    data = _fixture("SessionSummary.finished.json")
    data.update(review_zone=zone, review_zone_reasons_ru=["QA: synthetic coverage"],
                review_zone_rule_version="zone-rule-1" if zone else None)
    parsed = contract.validate("SessionSummary", data)
    assert parsed.model_dump(mode="json")["review_zone"] == zone


@pytest.mark.parametrize("overrides", [
    {"review_zone": "guilty"}, {"review_zone": 1}, {"review_zone_reasons_ru": ["x"] * 4},
    {"review_zone_reasons_ru": "no list"}, {"review_zone_rule_version": {}}, {"auto_sanction": True},
])
def test_review_summary_remains_strict_in_both_validators(overrides):
    data = _fixture("SessionSummary.finished.json")
    data.update(overrides)
    with pytest.raises(jsonschema.ValidationError):
        contract._validator("SessionSummary").validate(data)
    with pytest.raises(ValueError):
        contract.models()["SessionSummary"].model_validate(data)


def test_ready_handshake_requires_schema_and_python_agreement(monkeypatch):
    ready = {"contract": contract.v1.CONTRACT_ID, "contract_version": contract.v1.CONTRACT_VERSION}
    assert contract.ready_matches_contract(ready)
    assert not contract.ready_matches_contract({**ready, "contract_version": "999.0.0"})
    assert not contract.ready_matches_contract({**ready, "contract": "unrelated.v1"})
    altered = {**contract.schema(), "x-contract-version": "0.0.0-stale-schema"}
    monkeypatch.setattr(contract, "schema", lambda: altered)
    assert not contract.ready_matches_contract(ready)


def test_fault_fixture_replaces_audio_before_model_or_device_import():
    code = '''
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from qorgau_qa.fakes import FakeAudioMonitor, install
install({})
from proctor.audio.monitor import AudioMonitor
assert AudioMonitor is FakeAudioMonitor
observations = []
clock = SimpleNamespace(now_ms=lambda: 0, wall_at=lambda t: datetime.now(timezone.utc))
monitor = AudioMonitor("qa-isolation", "live", clock, observations.append)
monitor.start()
monitor.start()
monitor.stop()
monitor.start()
monitor.stop()
assert len(observations) == 2
assert all(o.producer.module == "qa.fake_audio" and o.health.code == "qa_audio_isolated" for o in observations)
assert not any(name == "sounddevice" or name.startswith("proctor.audio.vad") for name in sys.modules)
print("audio fixture isolated")
'''
    result = subprocess.run([sys.executable, "-c", code], env=backend_env(), capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0 and result.stdout.strip() == "audio fixture isolated", result.stderr


def _resp(status: int, body: bytes, ctype: str = "application/json") -> httpx.Response:
    return httpx.Response(status, content=body, headers={"content-type": ctype}, request=httpx.Request("GET", "http://127.0.0.1/v1/x"))


def test_api_error_helper_rejects_non_contract_errors():
    with pytest.raises(AssertionError):
        contract.api_error(_resp(500, b"Internal Server Error", "text/plain"), 500)
    with pytest.raises(jsonschema.ValidationError):
        contract.api_error(_resp(404, b'{"detail":"Not Found"}'), 404)
    trace = json.dumps({"error": {"code": "INTERNAL", "message": 'Traceback (most recent call last): File "x.py", line 1', "retryable": False, "details": {}}}).encode()
    with pytest.raises(AssertionError):
        contract.api_error(_resp(500, trace), 500)
    good = json.dumps({"error": {"code": "NOT_FOUND", "message": "x", "retryable": False, "details": {}}}).encode()
    contract.api_error(_resp(404, good), 404, "NOT_FOUND")
    with pytest.raises(AssertionError):
        contract.api_error(_resp(404, good), 404, "INVALID_STATE")


def test_preview_parser_rejects_malformed_frames():
    meta = b'{"a":1}'
    assert parse_preview_frame(struct.pack(">I", len(meta)) + meta + b"\xff\xd8\xff\xd9")[0] == {"a": 1}
    for bad in (b"", b"\x00\x00", struct.pack(">I", 999) + meta, struct.pack(">I", 0) + meta):
        with pytest.raises((AssertionError, ValueError)):
            parse_preview_frame(bad)


def test_backend_start_reports_failure_instead_of_hanging(tmp_path):
    with pytest.raises(BackendStartError) as exc:
        BackendProcess.start(tmp_path, token="short", ready_timeout=30)
    assert exc.value.returncode == 2


def test_session_body_helper_returns_independent_copies():
    a = session_create_body()
    a["consent"]["text_version"] = ""
    assert session_create_body()["consent"]["text_version"] == CONSENT["text_version"] != ""
