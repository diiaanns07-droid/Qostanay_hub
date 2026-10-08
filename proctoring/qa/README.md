# qa/ — independent QA and release checks (owner: A09)

Black-box tests of the Qorgau Exam backend through its **public** API, plus the acceptance matrix and the manual
Windows/LIVE protocols. A09 does not patch other modules: findings go to owners in `BUGS.md`.

| Path | What |
|---|---|
| `qorgau_qa/backend.py` | start a REAL `python -m proctor serve --token-stdin --port 0` exactly like Electron main: random 32-byte token via stdin, READY handshake, stdin-EOF stop, log capture with token-leak scan |
| `qorgau_qa/contract.py` | validate every payload twice: JSON Schema `$defs` (generated) **and** Pydantic (`proctor_contracts.v1`); `ApiError` checker (status, code, no stack traces) |
| `qorgau_qa/stream.py` | WS `/v1/stream` recorder, `/v1/preview` binary frame parser |
| `qorgau_qa/scenario.py` | full flow: preflight → calibration → exam → episodes → review → summary/report → finish → restart (PASS/FAIL/NOT_RUN rows) |
| `qorgau_qa/netguard.py` | portable offline guard: CPython audit hook in the backend process blocks + records non-loopback connects / DNS |
| `qorgau_qa/netns.py` | Linux: loopback-only network namespace for the whole test process tree (`unshare -rn`) |
| `qorgau_qa/offline_check.py` | full flow under the guard (and optionally inside the namespace), JSON verdict |
| `qorgau_qa/fakes.py` | QA-only fault-injection doubles for the five module factories, injected into a real `serve` process via `sys.modules` (`QA_FAKES` env); labelled `qa.fake_*`, never module/CV results |
| `tests/` | pytest suites (below) |
| `run_qa.py` | runs everything and writes `results/<date>_<label>_<sha12>/summary.{json,md}` bound to the tested SHA |
| `../packaging/` | Windows preparation/launch scripts and read-only file/import/model-checksum gate |
| `ACCEPTANCE_MATRIX.md` | PDF requirement → measurable acceptance → test → status |
| `scenarios/FAILURES.md`, `scenarios/WINDOWS.md` | failure matrix; manual Windows / offline / security / LIVE protocol |
| `protocols/PERFORMANCE.md`, `protocols/MODEL_QUALITY.md` | how performance and episode quality will be measured (drafts) |
| `BUGS.md` | reproducible findings for owners |
| `RESULTS.md` | current verdict per tested SHA |

## Run (from `proctoring/`, after the setup in `proctoring/README.md`)

```bash
.venv/bin/python -m pytest qa/tests -q            # Windows: .venv\Scripts\python -m pytest qa\tests -q
.venv/bin/python qa/run_qa.py --with-baseline     # + A01 checks; writes qa/results/...
.venv/bin/python qa/run_qa.py --label candidate --expected-sha FULL_SHA_FROM_A01
```
`qa/tests` is deliberately not in the default `testpaths` (A01's `pytest -q` stays fast); ~3 min, needs no camera,
no network, no admin. On Windows everything runs except the Linux network-namespace layer (skipped with a reason).

Result directory names now include a UTC timestamp so later runs do not overwrite earlier evidence.
The runner records SHA256 for each harness source file and forces UTF-8 in child processes on Windows.
The tested commit includes the harness when it is committed; `product_tree_dirty` excludes A09-owned paths.
`product_release_verified` remains false until the separate candidate/LIVE/Windows acceptance is complete.

Suites: `test_e2e_synthetic` (scenario, finish/abort/pause during an open episode, restart) ·
`test_lifecycle_matrix` (8 states × 11 actions, concurrency) · `test_security_negative` (token, Host, Origin/CORS,
body limits, WS auth, leakage) · `test_payload_negative` (≈100 hostile payloads, privacy allow-list) ·
`test_stream_preview` · `test_process_failures` (port busy, crash/restart, stdin EOF, bad token, corrupt exam,
Cyrillic path) · `test_fault_injection` (camera missing/busy/denied/no frames/unplugged, weights missing/corrupt,
storage down, slow analyzer, failing engine/store) · `test_offline` · `test_harness_selfcheck` (the harness itself).

## Honesty rules

* On BOOTSTRAP every session is **synthetic** (`source_mode=synthetic`, `producer.module=bootstrap.*`): results
  prove wiring, contract conformance and negative handling — not CV accuracy, not Windows protection.
* Known defects are `xfail(strict=True)` with a `QA-BUG-…` id: visible in every run, and they flip to a failure when
  fixed so the marker gets removed. Observations use non-blocking `QA-OBS-…` markers.
* Windows/LIVE items are never PASS from Linux; they are `NOT RUN`/`BLOCKED` until measured per `scenarios/WINDOWS.md`.
