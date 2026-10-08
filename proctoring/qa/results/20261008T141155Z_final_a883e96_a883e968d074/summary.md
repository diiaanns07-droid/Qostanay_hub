# QA run final_a883e96 @ `a883e968d074adfb85601b599c801b7f65de5c1f` (DIRTY product tree)

Scope: SYNTHETIC/bootstrap pipeline unless stated; not CV accuracy, not Windows environment protection.

| Suite | Status | Details |
|---|---|---|
| a09_qa | FAIL | {"FAIL": 12, "PASS": 356, "SKIP": 1, "XFAIL": 4} |

Known issues (XFAIL, tracked in qa/BUGS.md): QA-OBS-005, QA-OBS-006
Token-like strings in logs: none

## FAIL / strict XPASS
- `test_e2e_synthetic::test_full_synthetic_flow` — AssertionError: FAIL ready_line_contract: QORGAU_READY {"contract": "qorgau.v1", "contract_version": "1.2.0", "backend_version": "0.1.0", "port": 54036, "pid": 28140}
  FAIL summary: ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_ve
- `test_fault_injection::test_camera_unplugged_mid_exam_is_visible_and_session_survives` — AssertionError: assert ('noise_calibration' == 'camera_disconnected'

  - camera_disconnected
  + noise_calibration)
- `test_lifecycle_matrix::test_read_routes_valid_in_every_state[created]` — jsonschema.exceptions.ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_version' were unexpected)

Failed validating 'additionalProperties' in schema:
    {'additionalProperties': False,
     'properties': {'session': {'$ref': '#/$defs
- `test_lifecycle_matrix::test_read_routes_valid_in_every_state[preflight]` — jsonschema.exceptions.ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_version' were unexpected)

Failed validating 'additionalProperties' in schema:
    {'additionalProperties': False,
     'properties': {'session': {'$ref': '#/$defs
- `test_lifecycle_matrix::test_read_routes_valid_in_every_state[calibrating]` — jsonschema.exceptions.ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_version' were unexpected)

Failed validating 'additionalProperties' in schema:
    {'additionalProperties': False,
     'properties': {'session': {'$ref': '#/$defs
- `test_lifecycle_matrix::test_read_routes_valid_in_every_state[ready]` — jsonschema.exceptions.ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_version' were unexpected)

Failed validating 'additionalProperties' in schema:
    {'additionalProperties': False,
     'properties': {'session': {'$ref': '#/$defs
- `test_lifecycle_matrix::test_read_routes_valid_in_every_state[running]` — jsonschema.exceptions.ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_version' were unexpected)

Failed validating 'additionalProperties' in schema:
    {'additionalProperties': False,
     'properties': {'session': {'$ref': '#/$defs
- `test_lifecycle_matrix::test_read_routes_valid_in_every_state[paused]` — jsonschema.exceptions.ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_version' were unexpected)

Failed validating 'additionalProperties' in schema:
    {'additionalProperties': False,
     'properties': {'session': {'$ref': '#/$defs
- `test_lifecycle_matrix::test_read_routes_valid_in_every_state[finished]` — jsonschema.exceptions.ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_version' were unexpected)

Failed validating 'additionalProperties' in schema:
    {'additionalProperties': False,
     'properties': {'session': {'$ref': '#/$defs
- `test_lifecycle_matrix::test_read_routes_valid_in_every_state[aborted]` — jsonschema.exceptions.ValidationError: Additional properties are not allowed ('review_zone', 'review_zone_reasons_ru', 'review_zone_rule_version' were unexpected)

Failed validating 'additionalProperties' in schema:
    {'additionalProperties': False,
     'properties': {'session': {'$ref': '#/$defs
- `test_offline::test_full_flow_offline_audit_guard` — AssertionError: [{'name': 'ready_line_contract', 'status': 'FAIL', 'detail': 'QORGAU_READY {"contract": "qorgau.v1", "contract_version...chema:
      {'additionalProperties': False,
       'properties': {'session': {'$ref': '#/$defs/SessionInfo'},
        "}]
assert not [{'name': 'ready_line_contrac
- `test_offline::test_no_download_calls_in_backend_runtime_source` — AssertionError: audio/prepare.py:7: import urllib.request
  audio/prepare.py:26: with urllib.request.urlopen(yamnet.MODEL_URL, timeout=60) as response:
  audio/prepare.py:29: with urllib.request.urlopen(yamnet.LICENSE_URL, timeout=30) as response:
  audio/prepare.py:49: with urllib.request.urlopen(M

## Environment
- python: 3.12.14
- platform: Windows-11-10.0.26200-SP0
- machine: AMD64
- fastapi: 0.141.1
- uvicorn: 0.53.0
- websockets: 17.1
- pydantic: 2.13.5
- numpy: 2.4.6
- cv2: 4.13.0
- mediapipe: 0.10.35
- onnxruntime: 1.29.0
- httpx: 0.28.1
- pytest: 9.1.1
