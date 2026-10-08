> **?????????? ???????? Windows:** [CANDIDATE_WINDOWS.md](results/CANDIDATE_WINDOWS.md).
> de7290509bf558d6488be84d2e0730b2b9ab104a: A09 ????? ??????????? harness 363 PASS / 6 XFAIL / 1 SKIP;
> ??????? Electron **FAIL / P0 QA-WIN-007**, LIVE ?? ??????. ???? ? ???????????? ??????????.

# A09 QA results

## Current Adal integration alignment

Full QA on committed source `361e2445153de5c3035234331a616ee6e5887fa4`:
**380 PASS, 1 SKIP, 4 XFAIL, 0 FAIL, 0 XPASS** in 134.95 s, Windows 11 / Python 3.12.14.
Both product tree and harness were clean. [Exact run record](results/20261008T140835Z_adal_alignment_361e2445153d/summary.json)
includes source SHA, harness hashes and environment; its sibling pytest/JUnit logs preserve individual outcomes.

This run validates the integrated HTTP/WS lifecycle, independent canonical 1.1.0 schema checks,
labelled synthetic flows and fault doubles. Fault-injection LIVE sessions now always use a labelled
fake audio monitor; no camera, microphone, native guard or audio model was activated. Offline static
exceptions are exact reviewed function/call counts, while the unchanged runtime audit still blocks
public network attempts, including the actual C2 uploader. Production LAN transport is unaffected.

The skip is Linux network-namespace isolation. Four expected failures remain: three lax typed-value
cases (QA-OBS-005) and uppercase LOCALHOST (QA-OBS-006). The unknown-question and deleted-session
checks now pass and their obsolete xfail markers were removed. Separate targeted contract/harness/fault
validation passed 124 tests, and generated contract artifacts pass `generate.py --check`.
This automated result does not verify physical CV accuracy or product release readiness. Earlier
candidate/release findings below retain their historical scope; they are not reassessed by this run.

## Latest Windows continuation

Full result: **352 PASS, 16 XFAIL, 1 SKIP, 0 FAIL**, Windows 11 build 26200 / Python 3.12.14.
Tested HEAD `b275c70a59450e00bb887d949b98d4ca1680c9c8`; product files identical to A01
`35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (bootstrap). Both trees and harness hashes are recorded in
`results/20261008T055722Z_windows_combined_b275c70a5945/summary.json`.

Command: `python qa/run_qa.py --label windows_combined --base 35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`.
This includes the latest A09 fault-injection delivery `f8ac906` plus seven model-file readiness checks.
Synthetic signals and labelled module doubles exercise orchestration and failures; no physical camera,
CV accuracy or Windows keyboard enforcement was measured. Linux network isolation is the skipped check.

Pinned dependency installation, backend startup/shutdown, HTTP/WS tests, process crashes/restarts and
paths with Cyrillic ran on Windows. Backend asset/import readiness PASS; desktop readiness FAILS as
expected because this branch has no complete desktop candidate/models. PowerShell scripts parsed successfully.
Native enforcement was never activated. The publication payload contains code, documentation and sanitized
synthetic test reports; no camera recordings, model weights or credentials.

Earlier local records are preserved: the initial Windows run passed 328 tests; adding seven readiness
checks and a too-broad synthetic-preflight assertion produced two QA-only failures; that assertion was
corrected and the pre-sync run passed 335 tests. The combined run above is authoritative for delivered code.
Generated logs have trailing horizontal whitespace removed for Git whitespace checks; verdicts are unchanged.

**Product release verdict remains NOT RELEASABLE at this bootstrap.** Open issues QA-BUG-001–005 and
tracked observations are listed in BUGS.md. Final LIVE/REPLAY/Electron acceptance needs A01's candidate SHA.

Historical Linux verdict: **Run 2** (below Run 1 is kept for history). Product under test in both runs: A01 BOOTSTRAP
`35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` — synthetic pipeline, no CV modules, no Windows.

## Run 2 — BOOTSTRAP + fault injection

| | |
|---|---|
| Product under test | `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`; HEAD carried only A09 commits — the runner verified `product_files_differing_from_base: []` |
| Harness | branch `claude/focused-cerf-4po06f` (this commit) |
| Command | `cd proctoring && .venv/bin/python qa/run_qa.py --with-baseline --label bootstrap --base 35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` |
| Raw record | `qa/results/20261008_bootstrap_fbd9dcbb84ca/` (HEAD at run time = A09 checkpoint `fbd9dcb`; harness changes of this commit were uncommitted and are recorded as `harness_uncommitted: true`) |
| Environment | as Run 1 |

New in Run 2: `test_fault_injection.py` — QA-only doubles for the five module factories, injected into a REAL
`serve` process (`qa/qorgau_qa/fakes.py`). They check A01's composition handling of module failures, not modules.

| Area | Result |
|---|---|
| A01 baseline (pytest 64, generate --check, smoke 34/34, ownership self-test) | PASS |
| Doubles drive a complete LIVE session (harness validity) | PASS |
| Camera: open fails `CAMERA_UNAVAILABLE` / `CAMERA_BUSY` / `CAMERA_DENIED`, no frames (fails ≤ 10 s), factory crash, unplugged mid-exam (HealthObservation `camera_disconnected`, session controllable) | PASS |
| Models: phone weights missing (health `model_missing`, live preflight FAIL, no bootstrap fallback), corrupt weights (`init_error`, backend up), face model missing (calibration refused), synthetic demo still labelled | PASS |
| Storage: open error / unwritable → live preflight FAIL | PASS |
| Storage write failure during exam | **FAIL — QA-BUG-004** (every observation dropped from fusion, no episodes) |
| Pipeline failure visibility (engine or store raising) | **FAIL — QA-BUG-005** (`/health` stays ok: unknown looks like all-clear) |
| Slow analyzer (1.5 s/frame): `/health` p95 < 0.5 s, finish < 10 s; engine consume failure: pause/resume/finish work; engine finish failure: session still finishes, camera released | PASS |

Totals: **345 PASS, 16 XFAIL (tracked: QA-BUG-001…005, QA-OBS-003…006), 1 SKIP, 0 unexpected FAIL.**

Release gate @ 35bea4c: **NOT RELEASABLE — bootstrap only.** Blocking: section A of `ACCEPTANCE_MATRIX.md` BLOCKED
(no CV/capture/fusion/storage/shell modules), open A01 bugs QA-BUG-002…005, no Windows run.

---

## Run 1 — BOOTSTRAP (not a candidate build)

| | |
|---|---|
| Tested product SHA | `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (A01 BOOTSTRAP, branch `claude/nifty-ride-ux8e4j`); product tree unmodified during the run (`product_tree_dirty: false`) |
| Harness | `proctoring/qa/` on branch `claude/focused-cerf-4po06f` (commit that adds this file; harness was uncommitted while running, recorded in `summary.json`) |
| Environment | Linux x86_64 cloud container, Python 3.12.3, fastapi 0.141.1, uvicorn 0.53.0, websockets 17.1, pydantic 2.13.5; **no camera, no GPU, no Windows, no Electron binary** |
| Date | 2026-10-08 |
| Raw record | `qa/results/20261008_bootstrap_35bea4c7b28d/` in commit `fbd9dcb` (replaced in the tree by the Run 2 record) |
| Command | `cd proctoring && .venv/bin/python qa/run_qa.py --with-baseline --label bootstrap` |

### What this run is — and is not

It is a black-box check of the **public HTTP/WS API, lifecycle and process behaviour** of the real backend process on
the **synthetic, labelled bootstrap pipeline**. It is **not** a check of the CV product (no capture, phone, attention,
fusion or evidence module is integrated: health reports `module_not_integrated` for all five), not a Windows check,
not a performance or accuracy measurement.

### Verdicts

| Area | Result | Evidence |
|---|---|---|
| A01 baseline (`pytest -q` 64, `generate.py --check`, `proctor smoke` 34/34, ownership self-test) | PASS | `a01_*.txt` |
| Harness self-check (validators reject mutated payloads, ApiError checker, preview parser, start-failure detection, network guard blocks/records) | PASS | `test_harness_selfcheck.py`, `test_offline.py::test_netguard_*` |
| E2E synthetic flow: health → capabilities → create → exam def → preflight → 5-target calibration → start → answers (last-writer by client_seq) → 8 required environment actions (+dedup) → phone episode on stream → incidents → append-only review → summary → metrics → preview → finish → restart | PASS (35 rows) · report.html / report.json **NOT RUN** (2 rows, 501 until A08) | `test_e2e_synthetic::test_full_synthetic_flow` |
| Finish / abort / pause during an open episode | PASS (closed with `session_finished` / `session_aborted` / `session_paused`; no episode while paused) | `test_e2e_synthetic` |
| Lifecycle: 8 states × 11 actions, read routes in every state, concurrent finish/create | PASS (100 cases) | `test_lifecycle_matrix.py` |
| Security: bearer token (11 bad-credential variants × 6 routes), token in query, Host/DNS-rebinding, loopback-only listen, Origin/CORS (incl. dev allow-list exact match), body limit/chunked/malformed Content-Length, WS auth (header, subprotocol, query, Origin), no token in responses, no stack traces, OpenAPI surface | PASS · open **QA-BUG-001** (low) · obs QA-OBS-006/007 | `test_security_negative.py` |
| Hostile payloads (≈100): malformed JSON, extra fields, traversal ids, ranges, naive datetimes, environment privacy allow-list (no paths/titles/typed text/clipboard), batch limits, answers/reviews/capabilities validation | PASS for validation · **FAIL (tracked)**: QA-BUG-002, QA-BUG-003 · obs QA-OBS-003/004/005 | `test_payload_negative.py` |
| Stream/preview: session filter isolation, provenance (unique ids, frame order, tz-aware time), many clients, slow client does not block, binary preview framing + `byte_length`, REST preview meta = image size | PASS | `test_stream_preview.py` |
| Process failures: port busy (fail fast, no READY), ephemeral port, short/empty/no token, `--token-env`, non-loopback bind refused, corrupt exam → visible failure, missing exam → labelled demo fallback, stdin EOF during exam (exit 0, < 10 s), `shutdown` line, SIGKILL mid-exam → restart on same data dir + port freed, stdin garbage ignored, path with spaces + Cyrillic, nothing written into the source tree | PASS (Linux) | `test_process_failures.py` |
| Offline: full flow with the backend under the audit-hook guard (0 outbound attempts) and, in addition, inside a loopback-only network namespace (isolation probe: TEST-NET/1.1.1.1 unreachable, DNS fails); static scan: no download calls in backend runtime code | PASS (synthetic pipeline only) | `test_offline.py` |
| CV requirements A1–A8, environment protection on Windows A9–A11 (shell part), LIVE A13, performance B12 | **BLOCKED** (modules / Windows / camera not available) | `ACCEPTANCE_MATRIX.md` |

Totals of the A09 suite: **328 PASS, 13 XFAIL (tracked), 1 SKIP** (no non-loopback interface to probe in the container), 0 unexpected FAIL.

### Open issues (owners)

| ID | Owner | Severity | Summary |
|---|---|---|---|
| QA-BUG-003 | A01 | medium | 4xx error codes (INVALID_ARGUMENT, SESSION_MISMATCH, MODULE_NOT_INTEGRATED) returned with HTTP 500 |
| QA-BUG-002 | A01 (+A08 rule) | medium-low | path ids not validated (contract `Id`); long ids → 500 INTERNAL and reset keep-alive connection |
| QA-BUG-001 | A01 | low | token written to stderr when a client wrongly puts it into a WS URL query |

Repro steps: `qa/BUGS.md`. Observations QA-OBS-003…008 there as well.

### Release gate @ 35bea4c

**NOT RELEASABLE — bootstrap only.** Blocking: no CV/capture/fusion/storage/shell modules integrated (section A of
the acceptance matrix BLOCKED), error contract bugs QA-BUG-002/003 open, no Windows run. Nothing here is converted to
PASS by mocks or synthetic data; the final end-to-end verdict will be produced only on A01's integration candidate SHA.

### How the offline check was really done

Not by changing any firewall/network: (A) a CPython audit hook installed only in the backend process
(`qorgau_qa.netguard`) aborted and logged every non-loopback connect/DNS lookup — the log stayed empty for a full
session; (B) on Linux the whole test process tree ran in a fresh `unshare -rn` network namespace with only `lo` up,
confirmed by an in-namespace probe. Limits: (A) sees only Python-level sockets; Electron is not covered yet.
