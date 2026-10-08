# A06-native — checkpoint 3, controlled Windows LIVE passed

Branch: `codex/proctor-A06-native`. Base: `64354c014c4ad51054e8bb67fe841580771d43e5`.
Helper tested from pushed code `29778e9f23200132427c7a31d10098e044464c92`; this checkpoint changes
verification/evidence/documentation only. No runtime changes after the LIVE test.

Captain explicitly approved both modes in chat: «Да, готов проверить оба режима».
Actual machine: Windows 11, `platform=win32`, `os_release=10.0.26200`, Python 3.12.14, standard user.
Both runs used `live_console.py`, each with `--max-minutes 2`, separate from Electron.

- Dry-run: real `ready` / hook; Win, Alt+Tab, PrtScn each emitted `swallowed=false`.
  Foreground events showed SearchHost / explorer / Code / SnippingTool (basenames only).
  Captain reported exit after Ctrl+Alt+Shift+F12; log ends with `bye:emergency_hotkey`.
- Enforce: all three shortcuts emitted `swallowed=true`. Captain confirmed their Windows actions
  were suppressed and that text input, Win, Alt+Tab and window focus worked after emergency exit.
  Log ends with `bye:emergency_hotkey`; no helper error. Subsequent process check found no A06 test helper.
- `VERIFICATION.json`: three `blocked` records and one `detected_only` foreground record for this exact
  platform/release. No promotion from self-check/ready alone; native blocked records still require
  available helper + explicit `QORGAU_SHELL_NATIVE_ENFORCE=1` in the shell.
- Evidence and exact scope: [checks/live-2026-10-08/RESULTS.md](checks/live-2026-10-08/RESULTS.md).
- After recording LIVE: `node main/tests/run.mjs capabilities native` → **21 PASS** (fakes only).
  Actual VERIFICATION.json parsed as 4 valid records; exact platform/release and length limits PASS.
  Capability checks PASS for matching enforce, dry-run, absent helper, other OS release and other platform.

QA-WIN-001: **standalone native-helper LIVE PASS** for Win/Alt+Tab/PrtScn and emergency release.
No P0 observed in these checks. A01/A09 still need acceptance from the actual integrated Electron exam;
do not treat this helper test as a full application PASS or close the integrated acceptance item solely on it.
Not LIVE-tested: Alt+Esc, Ctrl+Esc, individual LWin/RWin, `stop`, EOF, heartbeat loss, parent death,
max-duration expiration, console-close cleanup, hook failure/queue overflow. Lifecycle and key mappings
have automated fake-hook coverage (checkpoint 2: 41 Python PASS, 33 Node PASS; typecheck/build PASS).

Limits unchanged: unsigned Python prototype; Ctrl+Alt+Del and UAC are not blocked; Windows may silently
remove a slow hook. No SDK/compiler/packages installed, no Windows settings changed, no other processes killed.
A01: integrate this branch and preserve the opt-in. A09: exercise actual Electron start/pause/finish/recovery.

---
## Historical checkpoint 2 — Python launch + controlled-test preparation

Previous checkpoint pushed: `94b1c81042ea0ac13aa29c882411c5200e380ad4` (before 17:00).
Same branch/base/scope as checkpoint 1 below; no foreign paths changed.

- `environment/native.ts`: .py helpers launch as `QORGAU_PYTHON -u <script>`; default interpreter
  matches the backend venv. Missing default exe falls back to native/qorgau_guard.py. Explicit exe preserved.
  Existing default dry-run / QORGAU_SHELL_NATIVE_ENFORCE=1 opt-in and capability verification gates unchanged.
- Added actual Python subprocess protocol-fake test (no Win32); existing controller tests retained.
- Hardened Python stdin/stdout against daemon buffered-stdio shutdown deadlocks; actual pipe watchdog tests.
  Python signal handler defers locking to the coordinator, avoiding reentrant signal/lock deadlock.
- `LIVE_CHECK_RU.md` + `live_console.py`: heartbeat sender, max-minutes=2, stop command, local evidence logs;
  no automatic VERIFICATION.json promotion. Both real dry-run and enforce await captain's explicit yes.

Checked using existing candidate Python / existing Node dependencies (no packages, SDK or compiler installed):
- `python -m pytest proctoring/desktop/native/tests -q -p no:cacheprovider` → **41 PASS**, 2.08 s;
  fake Win32 only; includes real subprocess pipes left open for heartbeat/parent/duration exits.
- `node main/tests/run.mjs native config capabilities guard` → **33 PASS**; native subset **11 PASS**.
- `npm run typecheck` → PASS contracts/main/renderer; `npm run build:electron` → PASS.
- Actual `--self-check` only → Windows `10.0.26200`, standard user, no hook installed.

NOT checked: real keyboard hook, key suppression/detection, foreground watch, console-close cleanup,
emergency chord and normal keyboard/focus after release. QA-WIN-001 remains OPEN pending controlled LIVE.
`desktop/native/VERIFICATION.json` still unchanged with records []. No blocking claim from fake tests/selfcheck.

---
## Checkpoint 1 — Python helper + protocol/lifecycle tests

Branch: `codex/proctor-A06-native`. Exact base: `64354c014c4ad51054e8bb67fe841580771d43e5`
(`codex/proctor-integration`, fetched and verified). Separate checkout `worktrees/A06-native`.
Scope for this continuation: native/, environment/native.ts + adjacent tests, handoffs/A06/ only.

- Python 3.12 ctypes helper implements the existing JSON-line protocol; dry-run by default.
- Dedicated WH_KEYBOARD_LL message thread, minimal callback, bounded output queue and separate writer.
- Win L/R, Alt+Tab, Alt+Esc, Ctrl+Esc, PrintScreen; emergency Ctrl+Alt+Shift+F12 passes and requests release.
- Stop/EOF/heartbeat loss/parent-handle death/time limit/error/console signal cleanup; no foreign process termination.
- Foreground emits only sanitized basename + foreign flag. No typed text/window titles/clipboard data.
- No SDK/compiler/packages installed. Uses existing candidate Python 3.12.14.

Checks on Windows: `python -m pytest proctoring/desktop/native/tests -q -p no:cacheprovider`
→ **36 PASS** (all hooks and Win32 lifecycle operations faked; no actual hook installed).
`qorgau_guard.py --self-check` → PASS, os `10.0.26200`, standard user; no hook installed.

At checkpoint 1: shell .py launch was pending; implemented in checkpoint 2 above.
QA-WIN-001 is still OPEN; VERIFICATION.json unchanged (`records: []`). LIVE, real suppression/release,
foreground observation and emergency chord have NOT been verified on hardware.
Limitations: unsigned Python prototype; Ctrl+Alt+Del/UAC cannot be blocked; OS may silently remove slow hooks.
No guarantee against Python/OS scheduling stalls. No registry/policy changes, service, autostart or installer.

---
# Historical A06 shell delivery (before A06-native)

Role: A06. Branch: `claude/inspiring-feynman-h9n9v8` (platform-assigned).
Contract/baseline: BOOTSTRAP A01 `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`, contract `qorgau.v1` 1.0.0.
Round-2 continuation SHA (previous verified delivery): `62a7fb1e42bc723152f49338ba4f816dfcad9334` (cp2).
Stage: **round 2 — teacher access model, no-stuck-restriction recovery checks, honest helper status.**
The new commit SHA is reported after push (a commit cannot contain its own SHA).

## Round 2 (task coordination/improvements-r2/04_A06_WINDOWS.txt)

### Delivered
* **Teacher access policy** (`main/src/shell/access.ts`, wired in `ipc/api.ts`; coordination doc
  `handoffs/A06/ACCESS_POLICY.md` with the full method table for A07/A01):
  default `review_after_pause` — during the exam review/history/report/evidence/summary/export/delete are
  closed even with the PIN; teacher reviews after pause (PIN) or finish. Opt-in
  `operator_live_review` (`QORGAU_SHELL_OPERATOR_LIVE_REVIEW=1`, **pending A01 approval, off by default**):
  PIN-unlocked console may read/review the **bound session only**; history/export/delete stay closed.
  No bypass in either policy.
* **Operator unlock expiry**: idle 180 s / absolute 30 min (configurable, bounded); a timer locks
  proactively and pushes `ShellState`. Exam start still clears the unlock.
* `calibrationSkip` now requires the operator unlock in main (A07 already asks for the PIN there).
* **Main exception latch**: an uncaught exception in main records `enforcement_error`, releases
  synchronously and forbids re-engaging that session (no engage/crash loop).
* **Release-path smoke extended** (stub, real backend): finish, emergency hotkey (+latch), renderer crash
  (+re-engage after recovery), renderer unresponsive 5 s (+re-engage), uncaught exception (+latch),
  backend SIGKILL (+restart, dead session not resurrected), quit during the exam — after each path the
  smoke asserts no restriction is left on the window and no global shortcut is registered.
* Capability test: helper `ready`/self-check alone never promotes an OS-level item.

### Adversarial review of the round-2 diff (workflow, 3 lenses × 2 skeptics, 43 agents) — follow-up commit
Fixed (several were inherited from cp1/cp2 code but are A06's to fix):
* **Cross-session access during the exam** (high): `listAnswers`/`getExam`/`getSession`/lifecycle on another
  session id passed without a PIN. Now during the exam every session-scoped method must target the bound
  session (any type trick refused); outside the exam another session (history, incl. `listSessions`) needs
  the unlock.
* **"Exam" was derived from the guard only**: after a release path with a still-RUNNING session, history
  opened. Now exam = restrictions engaged OR bound session `running`.
* **Unlock survived resume / recovery re-engage**: the student could pause again without the PIN. Now every
  engage clears the unlock.
* **Main-exception latch on a never-engaged session** made a later start run unrestricted. Now the latch
  applies only if an exam was in progress, and `startExam`/`resumeExam` of a latched session are refused.
* Renderer crash: main now re-reads the session itself after the reload (`did-finish-load`) instead of
  relying on the renderer; 3 crashes in 2 min → latch (no restriction toggling loop).
* Export: access re-checked after the report fetch (no native save dialog if an exam started meanwhile).
* Operator timers (unlock expiry, PIN lockout) use a monotonic clock; only successful operator calls
  refresh the idle timer.
* Tests: per-session refusal matrix, history PIN, operator methods in exam, api→timer→push wiring,
  precise export-after-finish assertion. `node main/tests/run.mjs` 78/78 PASS.

Still OPEN from the review (test-harness honesty; not fixed in this round):
* `shell-smoke.mjs` can exit 0 if a step throws (main.cjs's `uncaughtException` handler runs in the same
  process) — treat a smoke run as PASS only if the summary line shows all checks and no `FAIL` line.
* Smoke "renderer crash → re-engage" still issues the `getSession` call itself (main's own re-read is
  implemented but not separately asserted); smoke "emergency latch" is not a latch test (the latch is
  covered by `state.test.ts`); `clean()` omits fullscreen/content-protection/minimizable/resizable/movable;
  the backend-restart and quit checks do not assert a new PID / exit code / no orphan; the
  `enforcement_error` event on a main exception is not asserted by the smoke.

### NOT delivered (honest)
* **Windows native helper source/build: NOT delivered in round 2.** The attempt was stopped and is not in
  the repository. OS-level blocking (Alt+Tab, Win, PrtScn) and foreign-window identification therefore
  remain an **open gap**: the shell detects focus loss only and reports those items `unverified`
  (Windows) / `unsupported` (elsewhere). The shell-side controller/protocol stays tested against the
  protocol fake. Supported alternative for the demo machine: institution-managed Keyboard Filter /
  Assigned Access (`native/README.md`), measured and recorded in `VERIFICATION.json`.
* **Real Electron: NOT RUN** — the Electron 43.7.5 binary is not available in this sandbox (not retried).
  Every "Shell logic" PASS is Node + Electron **API stub** + real backend, never real Electron/Windows.

### Round-2 checks (Linux container, Node 22.22.0, Python 3.12 venv; no Electron binary, no Windows)
| Command (from `proctoring/desktop`) | Result |
|---|---|
| `npm run typecheck` | PASS contracts, PASS main/preload, SKIP renderer (A07 not on this branch) |
| `npm run build:electron` | PASS |
| `node main/tests/run.mjs` | 78/78 PASS (after the review fixes; incl. access policy + live-review integration against the real backend) |
| `node main/tests/shell-smoke.mjs` | 48/48 PASS, exit 0 (Electron **API stub** + real backend; see the open harness caveats above) |
| `python coordination/verify_ownership.py --agent A06 --base 62a7fb1…` and `--base 35bea4c…` | PASS (see commit report) |

### Next
A01: approve/reject live review; integrate A06 with A07 on the candidate. A09/teacher machine: `npm ci` +
`npm start` on Windows to run the real-Electron self-test and fill the NOT RUN column of `main/MATRIX.md`.
OS-level blocking needs either a reviewed helper implementation or the managed Windows lockdown.

---

## History: checkpoint 2 (62a7fb1)
## Paths changed (all A06-owned)
`desktop/main/` (src, tests, tools, MATRIX.md), `desktop/preload/src/preload.ts`,
`desktop/native/` (README.md, VERIFICATION.json; `bin/` is git-ignored, see DEPENDENCIES #2),
`handoffs/A06/`.

## What works (checkpoint 1)
* `main/src/main.ts` → `dist/main/main.cjs`: single instance, `app.enableSandbox()`, menu removed, one window
  with `contextIsolation`, `sandbox`, `nodeIntegration=false`, no webview, `webSecurity`, devTools off
  (on only unpackaged + `QORGAU_SHELL_ALLOW_DEVTOOLS=1`, closed in exam), in-memory partition.
* Renderer served from privileged `qorgau://app/` (not file://) with CSP `default-src 'none'; script-src 'self'`
  (no inline/eval), traversal-safe file mapping, fallback page if A07's build is missing.
* All navigation/redirects off-origin, `window.open`, webview, downloads, permissions (camera/mic/display
  capture/devices) and renderer network requests are denied for every webContents.
* Backend: `python -m proctor serve --token-stdin --port 0`, 64-hex token per launch via stdin only (never
  argv/env/URL/log; logger redacts), READY line validated (contract/major/port/pid), health after READY,
  PUT capabilities, WS `/v1/stream` + `/v1/preview` with `Authorization` header (no Origin). Restart on crash
  with backoff (3 in 120 s → `failed`), new token+port per launch. Stop: `shutdown` + stdin EOF → terminate →
  kill. Child env filtered (`ELECTRON_*`, `NODE_OPTIONS`, `QORGAU_DEV_TOKEN`, `QORGAU_SHELL_*`, `QORGAU_OPERATOR_*`).
* `preload/src/preload.ts` exposes exactly `window.qorgau: QorgauBridge` (typecheck enforces the shape); each
  method = one fixed IPC channel; no ipcRenderer/event objects leak; preview forwarded only while subscribed.
* IPC: sender must be the main window's MAIN frame on the trusted origin; exact arity, 64 KB cap, strict
  validators (contract key sets, Id pattern, no `.`/`..`), exam-mode gating (history/review/report/evidence
  closed during exam), operator gating (pause/resume/review/evidence/export/delete) with scrypt PIN
  (`QORGAU_OPERATOR_PIN_HASH`, `node main/tools/hash-pin.mjs`; 5 failures → lockout), start/resume only for the
  session created in this shell run.
* State machine `normal/preflight/exam/releasing/error` (`shell/state.ts`): exam mode ONLY when the backend
  reports the bound session `running`; pause/finish/abort/failed/backend loss/renderer gone/unresponsive (5 s)/
  quit → release; engage/release serialized; emergency latch (a session never re-engages after emergency exit).
* Guard (`environment/guard.ts`): kiosk+fullscreen, always-on-top `screen-saver`, content protection, close
  prevention, in-window key policy (`environment/keyboard.ts`, layout-independent by physical key), clipboard
  cleared on entry/exit, focus loss (detected_only) + re-focus attempt, display change, emergency hotkey
  `CommandOrControl+Alt+Shift+F12` (registration result checked; failure reported as enforcement_error).
  Release is idempotent, step-isolated, never throws; `releaseSync` used from crash handlers/`will-quit`.
* Environment events → `POST /v1/sessions/{sid}/environment/events`: strictly increasing `client_seq`, batches
  ≤100, bounded queue with counted drops, retry with backoff, flushed BEFORE pause/finish/abort.

## Checkpoint 2 additions
* `environment/probe.ts` + `probe-electron.ts`: startup self-test in a hidden, identically-hardened
  offscreen window using the REAL Chromium input pipeline (`sendInputEvent`) and the same key policy;
  a check passes only when the RESULT is observed (key never reached the page / window.open returned
  null and no window appeared / URL unchanged / devtools closed / close cancelled). If the pipeline
  cannot be exercised the result is `inconclusive` → matrix stays `unverified`.
* `environment/capabilities.ts`: matrix `blocked/detected_only/unsupported/unverified`. In-app items
  become `blocked` only from a passing self-test on THIS machine; OS-level items only from a
  `VERIFICATION.json` record whose platform+os_release match exactly (and, for native `blocked`, the
  helper is available + enforcing). No evidence → `unverified` (Win) / `unsupported` (other OS).
* `environment/native.ts`: controller for the optional Windows helper (self-check, ready handshake,
  heartbeat/parent-death, dry-run default, enforce only on opt-in, JSON line protocol, privacy-safe
  parsing). Wired into the guard; real helper NOT built (Windows only).
* `native/README.md`, `native/VERIFICATION.json` (empty records + examples), `main/MATRIX.md`
  (per-shortcut + lifecycle matrix with real PASS / NOT RUN), `main/tests/print-matrix.mjs`.

## Checks (Linux x86_64 container, Node 22.22.0, Python 3.12 venv from requirements/full.txt; no Electron binary, no Windows)
| Command (from `proctoring/desktop`) | Result |
|---|---|
| `npm run typecheck` | PASS contracts, PASS main/preload, SKIP renderer (A07) |
| `npm run build:electron` | PASS (`main.cjs` ~233 kB, `preload.cjs` 4.2 kB; preload requires only `electron`) |
| `node main/tests/run.mjs` | 64/64 PASS (unit: validate/keyboard/state/guard/web/capabilities/native + 11 integration against the REAL backend process) |
| `node main/tests/shell-smoke.mjs` (after build) | 30/30 PASS — built `main.cjs` under an Electron **API stub** + real backend |
| `node main/tests/print-matrix.mjs` | prints this machine's matrix (here: 11 unverified, 4 unsupported — self-test NOT RUN, not Windows) |

Integration (real `python -m proctor serve`): READY; token absent from `/proc/<pid>/cmdline` and `environ`; wrong
token 401; capabilities PUT accepted by the contract; synthetic session via the bridge API (exam only while
running, pause/resume, finish → released, history routes closed during exam, operator PIN); events delivered and
visible on the WS stream; SIGKILL backend → immediate release + restart with new token/port (old token 401);
graceful stdin shutdown with a RUNNING session exits 0 without terminate/kill; ignoring-shutdown backend → kill;
foreign READY rejected; READY timeout → bounded restarts → `failed`; missing python → `failed`; dev secrets not
inherited.

## NOT verified (honest) — the "Real enforcement" column of MATRIX.md is NOT RUN
* **No run inside real Electron** (binary not downloadable in this sandbox): window rendering, the real
  before-input-event self-test, kiosk/always-on-top/content-protection effect, focus/blur, global
  shortcut behaviour, renderer crash/unresponsive events. The stub smoke proves main-process wiring
  only; the self-test therefore reports `inconclusive` here and in-app items stay `unverified`.
* **No Windows**: nothing OS-level (Alt+Tab, Win, PrintScreen, foreign windows) is blocked or verified.
  The native helper is NOT built; its controller is tested only against a protocol fake that hooks
  nothing. Any OS-level `blocked` claim is an open gap until a controlled Windows test fills
  `VERIFICATION.json`.
* Renderer (A07) not present; fallback page only.

## Open gaps (missing enforcement by the honest rule, not "done because the event is visible")
* OS-level blocking of Alt+Tab / Win / PrtScn / foreign windows: implemented as detect + optional
  helper, but **not verified blocked** (no Windows). Needs helper build + controlled test (A09).
* Real-Electron validation of every in-app restriction: `npm start` on Windows (see MATRIX.md).

## Next
Build the Windows helper; run `npm start` + the self-test on Windows; fill VERIFICATION.json from a
controlled test; hand A09 the per-key PASS/FAIL results to complete MATRIX.md's real column.

## Integration order
A06 integrates in parallel with the CV chain (OWNERSHIP integration_order: A06 with A07 after A08). Depends only
on contracts v1 + A01 backend routes; A07 consumes `window.qorgau`.
