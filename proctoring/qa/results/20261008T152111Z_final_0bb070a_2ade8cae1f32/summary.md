# QA run final_0bb070a @ `2ade8cae1f324ce9920b1b9c1fba147e8eba3234`

Scope: SYNTHETIC/bootstrap pipeline unless stated; not CV accuracy, not Windows environment protection.

Product under test: `0bb070a92ed9a141d0c80a4c585bf01c88e2414a` — product tree at HEAD DIFFERS: proctoring/handoffs/FINAL_QA.md.

| Suite | Status | Details |
|---|---|---|
| a09_qa | FAIL | {"PASS": 381, "SKIP": 1, "FAIL": 2, "XFAIL": 4} |

Known issues (XFAIL, tracked in qa/BUGS.md): QA-OBS-005, QA-OBS-006
Token-like strings in logs: none

## FAIL / strict XPASS
- `test_offline::test_no_download_calls_in_backend_runtime_source` — AssertionError: audio/prepare.py: network operation download_yamnet:urllib.request.urlopen expected 0, got 2
  audio/prepare.py:26: with urllib.request.urlopen(yamnet.MODEL_URL, timeout=60) as response:
  audio/prepare.py:29: with urllib.request.urlopen(yamnet.LICENSE_URL, timeout=30) as response:
a
- `test_offline::test_reviewed_network_scopes_cannot_hide_new_runtime_calls` — assert not ['audio/prepare.py: network operation download_yamnet:urllib.request.urlopen expected 0, got 2', 'audio/prepare.py:26:...out=60) as response:', 'audio/prepare.py:29: with urllib.request.urlopen(yamnet.LICENSE_URL, timeout=30) as response:']
 +  where ['audio/prepare.py: network operation

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
