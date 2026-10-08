# QA run candidate @ `de7290509bf558d6488be84d2e0730b2b9ab104a`

Scope: SYNTHETIC/bootstrap pipeline unless stated; not CV accuracy, not Windows environment protection.

| Suite | Status | Details |
|---|---|---|
| a01_pytest | FAIL | {"exit_code": 1} |
| a01_generate_check | PASS | {"exit_code": 0} |
| a01_smoke | PASS | {"exit_code": 0} |
| ownership_self_test | PASS | {"exit_code": 0} |
| a09_qa | FAIL | {"PASS": 351, "FAIL": 11, "SKIP": 1, "XFAIL": 6} |

Known issues (XFAIL, tracked in qa/BUGS.md): QA-OBS-003, QA-OBS-004, QA-OBS-005, QA-OBS-006
Token-like strings in logs: none

## FAIL
- `test_fault_injection::test_storage_write_failure_does_not_silence_episode_detection` — [XPASS(strict)] QA-BUG-004 (A01): in the fusion loop store.record_observation() runs before engine.consume() in one try-block, so a failing store drops EVERY observation from fusion: no episodes and no visible signal
- `test_fault_injection::test_pipeline_errors_are_visible_in_health[store_writes_fail]` — [XPASS(strict)] QA-BUG-005 (A01): fusion/store exceptions are only counted internally; /health keeps fusion+evidence 'ok', so a broken pipeline looks like 'no violations' (unknown ≠ all-clear)
- `test_fault_injection::test_pipeline_errors_are_visible_in_health[engine_consume_fails]` — [XPASS(strict)] QA-BUG-005 (A01): fusion/store exceptions are only counted internally; /health keeps fusion+evidence 'ok', so a broken pipeline looks like 'no violations' (unknown ≠ all-clear)
- `test_offline::test_no_download_calls_in_backend_runtime_source` — AssertionError: phone/prepare.py:18: import urllib.request
  phone/prepare.py:60: with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - https only, explicit user action
assert not ['phone/prepare.py:18: import urllib.request', 'phone/prepare.py:60: with urllib.request.urlopen(url, ti
- `test_payload_negative::test_error_status_matches_contract[consent_not_accepted]` — [XPASS(strict)] QA-BUG-003 (A01): ProctorError raised with a 4xx ErrorCode keeps the base http_status 500 (consent/unknown exam → 500 INVALID_ARGUMENT, SESSION_MISMATCH → 500)
- `test_payload_negative::test_error_status_matches_contract[unknown_exam]` — [XPASS(strict)] QA-BUG-003 (A01): ProctorError raised with a 4xx ErrorCode keeps the base http_status 500 (consent/unknown exam → 500 INVALID_ARGUMENT, SESSION_MISMATCH → 500)
- `test_payload_negative::test_error_status_matches_contract[session_mismatch]` — [XPASS(strict)] QA-BUG-003 (A01): ProctorError raised with a 4xx ErrorCode keeps the base http_status 500 (consent/unknown exam → 500 INVALID_ARGUMENT, SESSION_MISMATCH → 500)
- `test_payload_negative::test_overlong_session_id_is_rejected_cleanly` — [XPASS(strict)] QA-BUG-002 (A01): ids longer than the contract Id (128) are not rejected; >~990 chars the error message overflows ApiErrorBody → 500 INTERNAL + keep-alive connection reset
- `test_payload_negative::test_overlong_question_id_is_rejected_cleanly` — [XPASS(strict)] QA-BUG-002 (A01 bootstrap router; same rule for A08): question_id > 128 chars → AnswerRecord validation → 500 INTERNAL
- `test_payload_negative::test_overlong_incident_id_is_rejected_cleanly` — [XPASS(strict)] QA-BUG-002 (A01 bootstrap router; same rule for A08): incident_id ~1000 chars → 500 INTERNAL
- `test_security_negative::test_token_misplaced_in_ws_query_is_not_logged` — [XPASS(strict)] QA-BUG-001 (A01, low): uvicorn.error logs the full WS URL incl. query, so a token a client wrongly puts in ?token= is written to backend stderr

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
