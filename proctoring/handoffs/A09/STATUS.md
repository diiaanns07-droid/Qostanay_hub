# A09 — STATUS (independent QA / Windows release)

Role: A09, independent QA and release engineer. Writes only `proctoring/qa/`, `proctoring/packaging/`,
`proctoring/handoffs/A09/`.
Branch: `claude/focused-cerf-4po06f` (platform-assigned), fast-forwarded to the BOOTSTRAP commit.
Baseline / contract SHA: `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (A01 BOOTSTRAP, contracts qorgau.v1 1.0.0 frozen).
Previous checkpoint of this branch: `7bcceced65722e894a6523dc854ca9334bf5557f` (prompt package only).
Stage: **milestone 1 done** — contract/API integration + negative tests against `python -m proctor serve`
(synthetic), acceptance matrix, failure / Windows / offline scenarios, plus composition-level fault injection.
Checkpoints on this branch: `fbd9dcb` (suites + docs), next commit (fault injection, Run 2).

## Delivered (paths)
* `qa/qorgau_qa/` — harness: real-process launcher (token via stdin, READY, stdin-EOF stop, token-leak scan),
  dual contract validator (JSON Schema + Pydantic), WS recorder / preview parser, end-to-end scenario, offline guard
  (audit hook, portable) + Linux loopback-only netns runner.
* `qa/tests/` — 9 suites, 362 test cases: e2e synthetic, lifecycle matrix, security, hostile payloads,
  stream/preview, process failures, fault injection, offline, harness self-check.
* `qa/qorgau_qa/fakes.py` — QA-only fault-injection doubles for the five module factories, injected into a real
  `serve` process via `sys.modules` (labelled `qa.fake_*`; never module/CV results).
* `qa/run_qa.py` — result record bound to the tested SHA (`qa/results/<date>_<label>_<sha12>/`).
* `qa/ACCEPTANCE_MATRIX.md`, `qa/scenarios/FAILURES.md`, `qa/scenarios/WINDOWS.md`,
  `qa/protocols/PERFORMANCE.md`, `qa/protocols/MODEL_QUALITY.md`, `qa/BUGS.md`, `qa/RESULTS.md`, `qa/README.md`.

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
* Windows: install/launch, per-shortcut protection, crash recovery of keyboard/focus, offline in Windows Sandbox,
  path with Cyrillic on NTFS, standard user — protocol written, NOT RUN.
* Offline guard covers Python sockets of the backend; Electron network not covered yet.
* `packaging/` not started (planned after the first candidate: one-command Windows bundle + checksum gate).

## Integration order / what A09 needs
A09 tests only A01's integration candidate SHA (not individual module branches). Needed from A01: candidate SHA +
integrated module SHAs; confirmation of performance targets. Requests: `handoffs/A09/DEPENDENCIES.txt`.

## Next (A09)
1. On A01's candidate SHA: `run_qa.py --with-baseline --label candidate`, re-run fault injection per real module
   (replace one module at a time), replay quality protocol, offline guard with CV models loaded.
2. Windows protocol on the demo laptop (`qa/scenarios/WINDOWS.md`), performance record.
3. `packaging/`: one-command Windows working-dir bundle with model checksum gate, then portable package if time.
