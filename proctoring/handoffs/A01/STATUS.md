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
