# Qorgau Exam — local proctoring prototype (case №3, Qostanai Industry Hackathon)

Local-only prototype: camera CV (phone, faces, approximate gaze) + exam environment protection, merged into
explainable episodes that a teacher reviews. No cloud services in the runtime path. Observations are not accusations.

**Status: BOOTSTRAP (contracts v1).** Only the skeleton, contracts and a clearly labelled SYNTHETIC pipeline exist.
Read first: `coordination/BOOTSTRAP.json`, `coordination/OWNERSHIP.json`, `coordination/CONTRACTS.md`,
`coordination/DECISIONS.md`, your `handoffs/Axx/`.

## Layout

```
proctoring/
  coordination/   BOOTSTRAP.json OWNERSHIP.json CONTRACTS.md DECISIONS.md REQUIREMENTS_MATRIX.md verify_ownership.py   (A01)
  contracts/      python/proctor_contracts (source of truth) · schema/v1 · ts · fixtures/v1 · tools/generate.py   (A01)
  backend/proctor/  app.py session.py settings.py __main__.py bootstrap/ (A01)
                    capture/ (A02) phone/ (A03) attention/ (A04) fusion/ (A05) evidence/ (A08)
  backend/tests/  A01 lifecycle/API tests
  desktop/        package.json, lockfile, tsconfig*, vite.config.ts, scripts/ (A01)
                  main/ preload/ native/ (A06) · renderer/ (A07)
  qa/ packaging/ (A09) · demo/ docs/pitch/ (A10) · handoffs/Axx/ (each agent)
  models/         local weights — git-ignored; manifests live in the module packages
```

## Setup (Windows, PowerShell; Python 3.12 x64, Node 22 LTS)

```powershell
cd proctoring
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements\full.txt
.venv\Scripts\python -m pip install --no-deps -e .
cd desktop; npm ci; cd ..
```
With uv instead: `uv sync --extra cv --extra dev` (uses `uv.lock`). Linux/macOS: `.venv/bin/python` instead of
`.venv\Scripts\python`.

## Run / check (from `proctoring/`)

| Command | What it proves |
|---|---|
| `python -m pytest -q` | contracts (Pydantic + JSON Schema + fixtures) and A01 lifecycle/API/security tests |
| `python contracts/tools/generate.py --check` | generated JSON Schema/TS are in sync with `v1.py` |
| `python -m proctor smoke` | starts a REAL backend process (`serve --token-stdin`), READY handshake, auth/origin checks, one SYNTHETIC session through preflight → calibration → exam → incident → review → finish → restart, stdin-EOF shutdown |
| `python coordination/verify_ownership.py --self-test` | ownership map has no overlapping paths |
| `python coordination/verify_ownership.py --agent A03 --base <BOOTSTRAP_SHA>` | your branch touched only your paths |
| `cd desktop && npm run check:contracts` | generated TS types + typed fixtures compile (`strict`) |
| `cd desktop && npm run typecheck` | contracts + main/preload (A06) + renderer (A07) when present; missing = SKIP |

Manual server (development):
```powershell
$env:QORGAU_DEV_TOKEN = -join ((1..48) | % { '{0:x}' -f (Get-Random -Max 16) })
.venv\Scripts\python -m proctor serve --token-env QORGAU_DEV_TOKEN --port 8765
```
Settings: environment variables `QORGAU_<FIELD>` for fields of `backend/proctor/settings.py` (e.g. `QORGAU_DATA_DIR`,
`QORGAU_MODELS_DIR`, `QORGAU_CAMERA_INDEX`, `QORGAU_DEV_ALLOW_ORIGIN=http://127.0.0.1:5173` for browser renderer dev).

Synthetic mode is a labelled test mode (`source_mode="synthetic"`, `producer.module="bootstrap.*"`); it is not CV and
never substitutes LIVE.
