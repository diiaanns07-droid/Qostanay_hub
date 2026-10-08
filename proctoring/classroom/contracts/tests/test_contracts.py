"""Contract checks (owner: T01): generated files in sync, fixtures valid in JSON Schema AND Pydantic,
backward compatibility with plain qorgau.class.v1 peers."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import TypeAdapter, ValidationError

from classroom.contracts import generate
from classroom.contracts import models as m

HERE = Path(__file__).resolve().parents[1]
FIXTURES = sorted((HERE / "fixtures" / "v1").glob("*.json"))
SCHEMA = json.loads((HERE / "schema" / "classroom.v1.schema.json").read_text(encoding="utf-8"))
MODELS = {cls.__name__: cls for cls in m.SCHEMA_MODELS}


def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"})


def test_generated_files_and_fixtures_are_up_to_date():
    assert generate.main(["--check"]) == 0


def test_required_contracts_have_fixtures():
    required = {"Student", "DeviceStatus", "Session", "ObservationEvent", "Incident", "ClipMetadata", "ExamPolicy", "Command", "CommandAck", "AudioSession"}
    covered = {p.stem.split(".")[0] for p in FIXTURES}
    assert required <= covered, required - covered


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_valid_in_schema_and_pydantic(path):
    name = path.stem.split(".")[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    _validator(name).validate(data)
    parsed = MODELS[name].model_validate(data)
    MODELS[name].model_validate(parsed.model_dump(mode="json"))  # round trip


@pytest.mark.parametrize("path", [p for p in FIXTURES if p.stem.split(".")[0] in {"Hello", "Status", "IncidentMsg", "Preview", "Ack", "CommandProgress", "AudioSignalIn", "Pong"}], ids=lambda p: p.stem)
def test_student_messages_parse_through_the_union(path):
    msg = TypeAdapter(m.StudentMessage).validate_python(json.loads(path.read_text(encoding="utf-8")))
    assert msg.type == path.stem.split(".")[0].lower().replace("incidentmsg", "incident").replace("audiosignalin", "audio_signal").replace("commandprogress", "command_progress")


@pytest.mark.parametrize("path", [p for p in FIXTURES if p.stem.startswith("T") and not p.stem.startswith("Teacher")], ids=lambda p: p.stem)
def test_teacher_stream_union(path):
    TypeAdapter(m.TeacherStreamMessage).validate_python(json.loads(path.read_text(encoding="utf-8")))


def test_inbound_ignores_unknown_fields_but_outbound_and_bodies_are_strict():
    hello = json.loads((HERE / "fixtures" / "v1" / "Hello.join_code_v1.json").read_text(encoding="utf-8"))
    assert m.Hello.model_validate({**hello, "future_field": 1})  # additive evolution, v1 §3
    with pytest.raises(ValidationError):
        m.CommandCreate.model_validate({"kind": "lock", "payload": {}, "admin": True})
    with pytest.raises(ValidationError):
        m.Command.model_validate({**json.loads((HERE / "fixtures" / "v1" / "Command.succeeded.json").read_text(encoding="utf-8")), "extra": 1})


def test_plain_v1_messages_remain_valid():
    """Every v1.1 field is optional: messages written exactly as PROTOCOL_v1.md §3 still parse."""
    base = {"v": 1, "msg_id": "x", "sent_at": "2026-10-08T09:00:00Z"}
    m.Hello.model_validate({**base, "type": "hello", "protocol": "qorgau.class.v1", "join_code": "123456", "computer_name": "PC", "student_label": "S", "app_version": "1"})
    m.Ack.model_validate({**base, "type": "ack", "command_id": "c-1", "ok": False, "error_ru": "нет"})
    m.CommandMsg.model_validate({**base, "type": "command", "command_id": "c-1", "kind": "unlock", "payload": {}})
    m.AudioSignalIn.model_validate({**base, "type": "audio_signal", "command_id": "c-1", "ice": {"candidate": "x"}})


def test_provenance_addition_is_optional_but_validated_when_present():
    hello = json.loads((HERE / "fixtures" / "v1" / "Hello.join_code_v1.json").read_text(encoding="utf-8"))
    assert m.Hello.model_validate(hello).source_mode is None
    for mode in ("unknown", "live", "synthetic", "replay"):
        parsed = m.Hello.model_validate({**hello, "source_mode": mode, "source_session_id": "local-1"})
        assert parsed.source_mode.value == mode and parsed.source_session_id == "local-1"
    for invalid in ({"source_mode": "camera-trusted"}, {"source_session_id": "../file"}):
        with pytest.raises(ValidationError):
            m.Hello.model_validate({**hello, **invalid})


@pytest.mark.parametrize(
    "bad",
    [
        {"join_code": "12345"},  # 5 digits
        {"join_code": "123456", "resume_token": "a" * 64},  # both credentials
        {},  # none
        {"join_code": "123456", "protocol": "qorgau.class.v2"},
        {"join_code": "123456", "sent_at": "2026-10-08T09:00:00"},  # naive datetime
    ],
)
def test_hello_rejections(bad):
    base = {"v": 1, "msg_id": "x", "sent_at": "2026-10-08T09:00:00Z", "type": "hello", "protocol": "qorgau.class.v1"}
    with pytest.raises(ValidationError):
        m.Hello.model_validate({**base, **bad})


def test_video_never_travels_as_json():
    """Previews are capped at 30 KB raw; there is no message type carrying a clip; ClipMetadata has a URL, no bytes."""
    assert m.MAX_PREVIEW_JPEG_BYTES == 30 * 1024
    too_big = "A" * (4 * ((m.MAX_PREVIEW_JPEG_BYTES + 2) // 3) + 4)
    with pytest.raises(ValidationError):
        m.Preview.model_validate({"v": 1, "msg_id": "x", "sent_at": "2026-10-08T09:00:00Z", "type": "preview", "jpeg_b64": too_big, "frame_wall": "2026-10-08T09:00:00Z"})
    assert "url" in m.ClipMetadata.model_fields and not any("b64" in f or "bytes" == f for f in m.ClipMetadata.model_fields)


def test_command_status_semantics():
    assert m.CommandStatus.SENT not in m.TERMINAL_COMMAND_STATUSES  # sent != executed
    assert {m.CommandStatus.SUCCEEDED, m.CommandStatus.FAILED, m.CommandStatus.EXPIRED, m.CommandStatus.CANCELLED} == set(m.TERMINAL_COMMAND_STATUSES)
    assert m.CommandKind.APPLY_POLICY not in m.V1_COMMAND_KINDS  # never sent to a plain v1 client
