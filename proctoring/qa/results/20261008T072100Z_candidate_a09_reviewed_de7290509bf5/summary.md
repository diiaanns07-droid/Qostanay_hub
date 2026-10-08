# QA run candidate_a09_reviewed @ `de7290509bf558d6488be84d2e0730b2b9ab104a`

Scope: SYNTHETIC/bootstrap pipeline unless stated; not CV accuracy, not Windows environment protection.

| Suite | Status | Details |
|---|---|---|
| a09_qa | PASS | {"PASS": 363, "SKIP": 1, "XFAIL": 6} |

Known issues (XFAIL, tracked in qa/BUGS.md): QA-OBS-003, QA-OBS-004, QA-OBS-005, QA-OBS-006
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
