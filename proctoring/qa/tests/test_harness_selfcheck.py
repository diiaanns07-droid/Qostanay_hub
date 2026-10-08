"""Self-check of the A09 harness (kept separate from product results, prompt: "document the check of
your test harness separately"). If these fail, no product verdict from qa/ can be trusted."""

from __future__ import annotations

import copy
import json
import struct

import httpx
import jsonschema
import pytest

from qorgau_qa import contract
from qorgau_qa.backend import PROCTORING_ROOT, BackendProcess, BackendStartError, session_create_body, CONSENT
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
