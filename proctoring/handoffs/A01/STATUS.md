# A01 — STATUS

Role: architect, contracts owner, composition root, integrator.
Branch: `claude/nifty-ride-ux8e4j` (platform-assigned; created from `codex/proctoring-prompts` @ 7bccece).
Previous checkpoint: 7bccece (launch prompts package, no product code).
Stage: **BOOTSTRAP published — contracts v1 frozen.** The BOOTSTRAP commit SHA is reported to the user after push.

## Delivered
* Contracts v1: `contracts/python/proctor_contracts/v1.py` (source of truth) → generated JSON Schema + TS types +
  typed TS fixtures; Python interfaces (`interfaces.py`); bridge `window.qorgau` (`contracts/ts/bridge.ts`);
  33 synthetic fixtures.
* Backend skeleton: `python -m proctor serve --token-stdin` (READY handshake, stdin-EOF shutdown), FastAPI API v1
  (lifecycle, calibration proxy, environment events/capabilities, preview, metrics, WS stream + preview),
  security middleware (bearer token, loopback Host, Origin allow-list, body limit), SessionManager/SessionRuntime
  (single active session, per-session fusion thread, pause/finish/abort semantics), ModuleRegistry discovering
  module factories.
* Bootstrap (synthetic only, labelled): synthetic capture, scripted phone/attention analyzers, minimal engine
  (phone_visible + environment), in-memory store/router, `python -m proctor smoke`.
* Shared config: `pyproject.toml`, `uv.lock`, `requirements/{runtime,full}.txt`, `desktop/package.json` +
  `package-lock.json`, tsconfigs, `vite.config.ts`, `scripts/build-electron.mjs`, `scripts/typecheck.mjs`.
* Coordination: BOOTSTRAP.json, OWNERSHIP.json (+ `verify_ownership.py`), CONTRACTS.md, DECISIONS.md,
  REQUIREMENTS_MATRIX.md.

## Checks run (Linux x86_64 container, Python 3.12.3, Node 22.22.0; no camera/Windows/GPU)
| Command | Result |
|---|---|
| `python -m pytest -q` | 64 passed |
| `python contracts/tools/generate.py --check` | PASS |
| `python -m proctor smoke` | 34/34 PASS (synthetic) |
| `python coordination/verify_ownership.py --self-test` | PASS |
| `npm ci`-equivalent install (`ELECTRON_SKIP_BINARY_DOWNLOAD=1`) + `npm run check:contracts` / `typecheck` | PASS (main/renderer SKIP: no sources yet) |
| CV imports (cv2 4.13, mediapipe 0.10.35, onnxruntime 1.29 CPU) | import PASS; no inference run |

## Not verified / limitations
* Windows setup and run, any camera, any CV inference, Electron binary (download failed in sandbox),
  environment protection — all owned by later stages; nothing here is a CV or security result.
* JS test runner not pinned (vitest 4.1.11 crashed npm's resolver). Request one in DEPENDENCIES if needed.

## Integration order
A02 → A03/A04 → A05 → A08 → (A06 shell in parallel, A07 on fixtures) → A09/A10 on the candidate SHA.

## Next (A01)
Integration checks for startup/shutdown, review incoming DEPENDENCIES requests, integrate delivered SHAs on an
integration branch, keep REQUIREMENTS_MATRIX current.

---
## Round 2 — integration (continuation of 35bea4c7b28d2c622cf7ba26ff354273cc7b6c49)

### Checkpoint 1: shared fixes for A09 findings (before module merges)
* QA-BUG-004: fusion loop calls `engine.consume()` independently of `store.record_observation()`; store errors in
  `_emit`/snapshot/upsert never stop the stream.
* QA-BUG-005: `PipelineFaults` per component (analyzer/fusion/evidence) → `/health` degraded with code + error
  count, `SessionInfo.last_error`, `health` stream message, `HealthObservation` (→ monitoring_degraded) except for
  engine faults; rate-limited (5 s), recovery events, no re-write to a failing store. Fusion queue overflow visible.
* QA-BUG-003: `HTTP_STATUS_BY_CODE` table (all ErrorCodes) is authoritative in the error handler.
* QA-BUG-002: router dependency validates every path id (also the A08/bootstrap router) → 422; error bodies clipped.
* QA-BUG-001: log filter strips query strings from uvicorn/websockets records.
* A02 R7: health listener detached before `capture.close()` (no gap at every finish).
* QA-OBS-004 / A08 #3: `BackendContext.forget_session()`; bootstrap router calls it after DELETE.
* A08 #2 / A05 A01-2: optional `store.record_session_config(session_id, models, engine_config)` at start.
* A04 R2: CalibrationMsg pushed every 250 ms while calibrating when A04's `updated_at` changes.
* A05 A01-4: analyzer health changes during running → HealthObservation.
* Tests: A01 tests hide modules explicitly (`module_overrides={key: None}`) and use tmp models/data dirs;
  new `backend/tests/test_qa_regressions.py` (in-process + real subprocess). Smoke now 37 checks.

### Checkpoint 2: integrated candidate r2-candidate-1 (manifest: coordination/CANDIDATE.json)
Integrated by `git merge --no-ff` of the pinned SHAs, order A02 → A03 → A04 → A05 → A08 → A06 → A07 → A10 → A09.
All 9 deliveries: BOOTSTRAP descendants, `verify_ownership.py` PASS, zero conflicts, no owner file edited by A01.
A09's e01fff5 already contains the earlier A09 delivery (f8ac906) + the friend's Windows tooling — merged once.

Shared follow-ups done after the merges: smoke uses a temporary data dir (the real SQLite store would otherwise
write smoke sessions into the user's data dir); late `exam_mode_released`/focus events accepted ≤ 5 s after
finish/abort and stored for the report only (A06 #6); `backend/tests/test_integrated_flow.py`.

### Requests to owners (small, reproducible; A01 did not edit their modules)
* **A08** — `backend/proctor/evidence/tests/test_evidence_api.py::test_full_synthetic_flow` asserts
  `config_versions["fusion.rule_version"] == "bootstrap-0"`; with A05 integrated it is `a05-rules-1.0.0`
  (correct behaviour). Make the assertion module-independent (or hide fusion via `module_overrides={"fusion": None}`).
  Optional hooks now offered by A01: `store.record_session_config(session_id, models, engine_config)` (called at
  start when present) and `context.forget_session(session_id)` (call after a successful DELETE; QA-OBS-004).
* **A03 / A05** — false positive on a public image WITHOUT a phone: MediaPipe asset `man-woman-okay.jpg`
  (letterboxed 640×480) → `cell phone` 0.26 at bbox (0.62, 0.40, 0.67, 0.45) (hand "OK" gesture) → REPLAY
  produced `phone_visible` + `phone_raised` episodes, priority medium. Repro: `QORGAU_IT_REPLAY_DIR=<dir with
  faces_01.json> pytest -s backend/tests/test_integrated_flow.py` (manifest/builder described in the test docstring;
  media stays outside Git). Ask: hand-gesture hard negative, confidence floor for episode opening.
* **A04** — `face_missing` did not open for a 3 s empty segment in the same replay (expected if the rule needs
  longer; please confirm the threshold in INTERFACE.md for A10's script).
* **A06** — real Electron + Windows run is the open item for 2.3 (NOT RUN here). Late release events are now accepted.
* **A07** — `GET /v1/exam` before session creation (A07 #1) is NOT added yet: it needs a bridge method (A06) →
  contract v1.1 together with the shell error codes (A06 #5 / A07 #3).

### Checks on product SHA 0397ac8abaab46d958115c4d5d5aa8f23caea4f1 (candidate = this + coordination/handoff docs only)
Environment: Linux x86_64 container, Python 3.12.3, Node 22.22.0, weights prepared with the owners' tools
(yolo11n.onnx sha256 634279b40c07…, face_landmarker.task sha256 64184e22…), libgles2/libegl1 installed; no camera,
no Windows, no real Electron binary.

| Check | Result |
|---|---|
| `python -m pytest -q` (all modules + A01) | 891 passed, 7 skipped, **1 failed** = A08 `test_full_synthetic_flow` (expects bootstrap engine; request sent) |
| `python -m proctor smoke` | 37/37 PASS (real A02/A05/A08, synthetic analyzers) |
| `pytest backend/tests/test_integrated_flow.py` with `QORGAU_IT_REPLAY_DIR` | 2 passed: synthetic flow to HTML/JSON report + restart; REPLAY through real A03/A04 (face_count 1 and 2, `multiple_faces` high) |
| `python qa/run_qa.py --with-baseline` (A09 suite) | 351 PASS, 6 XFAIL, 1 SKIP, 11 FAIL = **10 strict XPASS** (QA-BUG-001…005 fixed, A09 removes markers) + 1 real FAIL: `test_offline.py::test_no_download_calls_in_backend_runtime_source` flags A03's explicit prep CLI `phone/prepare.py` (allow-list names `model_tool.py`) → A09/A03 |
| desktop `npm run typecheck` / `build:electron` / `build:renderer` | PASS (3/3 projects) / PASS / PASS |
| A06 `node main/tests/run.mjs` / `shell-smoke.mjs` | 64/64 / 30/30 (Electron API stub + real backend) |
| A07 `renderer/tests/e2e-fixture.mjs` (vite preview + global Playwright) | 30/30 on the labelled FixtureBridge |
| A10 `demo/verify_demo.py` | PASS (content only) |
| A10 `demo/rehearse.py --mode synthetic` / `--mode replay` | PASS 7.3 s / PASS 8.3 s; calibration, LIVE camera, Windows shortcuts NOT_RUN. **The replay "phone_episode PASS" is the false positive above (no phone in those images) — not evidence of detection.** |

NOT RUN: LIVE camera, Windows install/launch, real Electron window, OS-level shortcut blocking, measured
performance on the demo laptop, CV precision/recall (no consented recordings).
