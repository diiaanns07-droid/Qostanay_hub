# A09 — STATUS (independent QA / Windows release)

Role: A09, independent QA and release engineer. Writes only `proctoring/qa/`, `proctoring/packaging/`,
`proctoring/handoffs/A09/`.
Branch: `codex/proctor-A09`, continuing published `claude/focused-cerf-4po06f` through
`f8ac90617147152d2032b57034560b395f1d1c8f` (fault-injection delivery retained).
Baseline / contract SHA: `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (A01 BOOTSTRAP, contracts qorgau.v1 1.0.0 frozen).
Previous checkpoint: `b275c70a59450e00bb887d949b98d4ca1680c9c8` (Windows continuation after the rebase).
Stage: **Windows backend QA and launch tooling delivered**. Full product acceptance awaits A01 candidate.

## Delivered (paths)
* `qa/qorgau_qa/` — harness: real-process launcher (token via stdin, READY, stdin-EOF stop, token-leak scan),
  dual contract validator (JSON Schema + Pydantic), WS recorder / preview parser, end-to-end scenario, offline guard
  (audit hook, portable) + Linux loopback-only netns runner.
* `qa/tests/` — 10 suites, 369 cases: existing API/lifecycle/security/failure/offline/fault-injection
  checks plus seven asset checksum/readiness checks.
* `qa/qorgau_qa/fakes.py` — QA-only fault-injection doubles for the five module factories, injected into a real
  `serve` process via `sys.modules` (labelled `qa.fake_*`; never module/CV results).
* `qa/run_qa.py` — result record bound to the tested SHA (`qa/results/<date>_<label>_<sha12>/`).
* `qa/ACCEPTANCE_MATRIX.md`, `qa/scenarios/FAILURES.md`, `qa/scenarios/WINDOWS.md`,
  `qa/protocols/PERFORMANCE.md`, `qa/protocols/MODEL_QUALITY.md`, `qa/BUGS.md`, `qa/RESULTS.md`, `qa/README.md`.
* `packaging/`: Windows prepare/launch scripts and read-only dependency/build/model checksum gate.

## Interfaces used (public only)
HTTP/WS API v1 (CONTRACTS.md §3), process handshake (§4), `proctor_contracts.v1` models and the generated JSON
Schema. No backend internals imported; no other module's files edited.

## Checks (Linux x86_64 container, Python 3.12.3; no camera/GPU/Windows)
| Command (from `proctoring/`) | Result |
|---|---|
| `.venv/bin/python qa/run_qa.py --with-baseline --label bootstrap --base 35bea4c7…` | A01 pytest/generate/smoke/ownership PASS; A09: 345 PASS, 16 XFAIL (tracked), 1 SKIP, 0 FAIL; product tree identical to 35bea4c |
| `.venv/bin/python -m pytest qa/tests -q` | same suite (~4 min) |
| `python coordination/verify_ownership.py --agent A09 --base 35bea4c7…` | PASS (run before push; see commit) |

## Findings sent to owners (repro in `qa/BUGS.md`, priority order)
* QA-BUG-004 (A01, medium): a failing store write drops every observation from fusion → no episodes.
* QA-BUG-005 (A01, medium): engine/store failures invisible in health/metrics/stream (unknown looks like all-clear).
* QA-BUG-003 (A01, medium): 4xx ErrorCodes returned as HTTP 500.
* QA-BUG-002 (A01 + rule for A08, medium-low): path ids not validated; long id → 500 + connection reset.
* QA-BUG-001 (A01, low): token logged when misplaced in a WS URL.
* Observations QA-OBS-003…009 (answers not checked against exam, delete keeps session readable, lax coercion,
  Host case, WS rejection = HTTP 403, undocumented re-entry transitions, module `health()` must match a failed
  `load()/open()` — for A03/A04/A08).

## Not verified / limitations
* Everything CV (A02–A05), storage/report (A08), Electron shell and Windows protection (A06), renderer (A07):
  not integrated at BOOTSTRAP → BLOCKED in the acceptance matrix. Synthetic results are wiring only.
* Windows Python dependency installation and backend/process/API tests (including Cyrillic paths) are verified below.
  Real Electron rendering, camera, per-shortcut enforcement, keyboard/focus recovery and Windows Sandbox are NOT_RUN.
* Offline guard covers Python sockets of the backend; Electron network not covered yet.
* Preparation/launch tooling is delivered and syntax/readiness logic checked. Running the full desktop launcher
  is BLOCKED by the absent integrated modules/build/models; no standalone installer has been built.

## Integration order / what A09 needs
A09 tests only A01's integration candidate SHA (not individual module branches). Needed from A01: candidate SHA +
integrated module SHAs; confirmation of performance targets. Requests: `handoffs/A09/DEPENDENCIES.txt`.

## Next (A09)
1. On A01's candidate SHA: `run_qa.py --with-baseline --label candidate`, re-run fault injection per real module
   (replace one module at a time), replay quality protocol, offline guard with CV models loaded.
2. Windows protocol on the demo laptop (`qa/scenarios/WINDOWS.md`), performance record.
3. Exercise the delivered packaging scripts on A01's candidate; portable installer is optional later work.

## Windows continuation checkpoint by Codex

Branch `codex/proctor-A09`, starting from published A09
`fbd9dcbb84ca981bc7632260f589d34f06400ee3`. Windows 11 build 26200, Python 3.12.14,
unchanged A01 pinned lockfile installed with `uv sync --frozen --extra cv --extra dev`.

Added source-checkout preparation/launch scripts, a read-only dependency/model checksum gate,
seven negative/positive asset checks, timestamped result directories, UTF-8 subprocess logs,
exact candidate SHA guard and harness source hashes. Adapted orchestration assertions to permit
A05 grouped incidents and integrated module health; preparation-only model download CLIs are
excluded explicitly from the runtime static scan (runtime network guard remains active).

Final pre-sync run: 335 PASS, 13 XFAIL, 1 SKIP, 0 FAIL; evidence:
`qa/results/20261008T055245Z_windows_final_fbd9dcbb84ca/`.
Initial baseline checks also passed. The intermediate failed run is retained; its two failures
were in A09's synthetic preflight assertion and were fixed before this final run.

Backend readiness PASS. Desktop readiness correctly FAILS because the integration candidate,
compiled desktop and models are absent here. PowerShell syntax PASS. No webcam or OS restriction
was activated. Publication status and delivery commit are reported separately.

Latest A09 upstream `f8ac90617147152d2032b57034560b395f1d1c8f` appeared during work.
Its same-role fault-injection additions are now retained as the parent of this Windows continuation.
A01 product integration remains outside this role.

## Final combined Windows run

Tested HEAD: `b275c70a59450e00bb887d949b98d4ca1680c9c8`.
Product files are byte-identical in Git to A01 `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`;
the comparison and harness hashes are recorded in
`qa/results/20261008T055722Z_windows_combined_b275c70a5945/summary.json`.

Command: `python qa/run_qa.py --label windows_combined --base 35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`.
Result: **352 PASS, 16 XFAIL, 1 SKIP, 0 FAIL**. Token-like strings in logs: none.
All 20 upstream fault-injection cases are included. They use labelled doubles, not a camera or real CV.
The one SKIP is Linux network namespace isolation on Windows. XFAIL includes QA-BUG-001 through 005
and tracked contract observations; these issues remain open for their owners.

Only A09-owned paths changed. Working backend tests do not establish that the full product is releasable.
The final commit containing this report is metadata-only relative to the tested code.
