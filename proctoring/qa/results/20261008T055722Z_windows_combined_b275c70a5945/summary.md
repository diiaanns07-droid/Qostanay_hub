# QA run windows_combined @ `b275c70a59450e00bb887d949b98d4ca1680c9c8`

Scope: SYNTHETIC/bootstrap pipeline unless stated; not CV accuracy, not Windows environment protection.

Product under test: `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` — product tree at HEAD IDENTICAL to it.

| Suite | Status | Details |
|---|---|---|
| a09_qa | PASS | {"PASS": 352, "XFAIL": 16, "SKIP": 1} |

Known issues (XFAIL, tracked in qa/BUGS.md): QA-BUG-001, QA-BUG-002, QA-BUG-003, QA-BUG-004, QA-BUG-005, QA-OBS-003, QA-OBS-004, QA-OBS-005, QA-OBS-006
Token-like strings in logs: none

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
