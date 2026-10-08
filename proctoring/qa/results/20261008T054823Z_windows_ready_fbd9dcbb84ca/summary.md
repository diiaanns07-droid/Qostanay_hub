# QA run windows_ready @ `fbd9dcbb84ca981bc7632260f589d34f06400ee3`

Scope: SYNTHETIC/bootstrap pipeline unless stated; not CV accuracy, not Windows environment protection.

| Suite | Status | Details |
|---|---|---|
| a01_pytest | PASS | {"exit_code": 0} |
| a01_generate_check | PASS | {"exit_code": 0} |
| a01_smoke | PASS | {"exit_code": 0} |
| ownership_self_test | PASS | {"exit_code": 0} |
| a09_qa | FAIL | {"FAIL": 2, "PASS": 333, "SKIP": 1, "XFAIL": 13} |

Known issues (XFAIL, tracked in qa/BUGS.md): QA-BUG-001, QA-BUG-002, QA-BUG-003, QA-OBS-003, QA-OBS-004, QA-OBS-005, QA-OBS-006
Token-like strings in logs: none

## FAIL
- `test_e2e_synthetic::test_full_synthetic_flow` — AssertionError: FAIL preflight_bootstrap_parts_labelled_not_pass: bootstrap parts must be WARN + impl=bootstrap, never PASS
assert not [('preflight_bootstrap_parts_labelled_not_pass', 'FAIL', 'bootstrap parts must be WARN + impl=bootstrap, never PASS')]
 +  where [('preflight_bootstrap_parts_labelle
- `test_offline::test_full_flow_offline_audit_guard` — AssertionError: [{'name': 'preflight_bootstrap_parts_labelled_not_pass', 'status': 'FAIL', 'detail': 'bootstrap parts must be WARN + impl=bootstrap, never PASS'}]
assert not [{'name': 'preflight_bootstrap_parts_labelled_not_pass', 'status': 'FAIL', 'detail': 'bootstrap parts must be WARN + impl=boot

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
