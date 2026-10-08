# A06 — STATUS (Electron shell, trusted IPC, backend process, exam-environment protection)

Role: A06. Branch: `claude/inspiring-feynman-h9n9v8` (platform-assigned; fast-forwarded to BOOTSTRAP).
Contract/baseline: BOOTSTRAP A01 `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (branch `claude/nifty-ride-ux8e4j`), contract `qorgau.v1` 1.0.0.
Previous verified checkpoint: 35bea4c (BOOTSTRAP, no A06 code).
Stage: **checkpoint 1 — secure shell + backend lifecycle + bridge + normal release.** Checkpoint 2
(capability matrix with runtime self-test, Windows helper, emergency-release matrix) follows.

## Paths changed (all A06-owned)
`desktop/main/` (src, tests, tools), `desktop/preload/src/preload.ts`, `handoffs/A06/`.

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

## Checks (Linux x86_64 container, Node 22.22.0, Python 3.12 venv from requirements/full.txt; no Electron binary, no Windows)
| Command (from `proctoring/desktop`) | Result |
|---|---|
| `npm run typecheck` | PASS contracts, PASS main/preload, SKIP renderer (A07) |
| `npm run build:electron` | PASS (`main.cjs` 216 kB, `preload.cjs` 4.2 kB; preload requires only `electron`) |
| `node main/tests/run.mjs` | 47/47 PASS (36 unit + 11 integration against the REAL backend process) |
| `node main/tests/shell-smoke.mjs` (after build) | 29/29 PASS — built `main.cjs` under an Electron **API stub** + real backend |

Integration (real `python -m proctor serve`): READY; token absent from `/proc/<pid>/cmdline` and `environ`; wrong
token 401; capabilities PUT accepted by the contract; synthetic session via the bridge API (exam only while
running, pause/resume, finish → released, history routes closed during exam, operator PIN); events delivered and
visible on the WS stream; SIGKILL backend → immediate release + restart with new token/port (old token 401);
graceful stdin shutdown with a RUNNING session exits 0 without terminate/kill; ignoring-shutdown backend → kill;
foreign READY rejected; READY timeout → bounded restarts → `failed`; missing python → `failed`; dev secrets not
inherited.

## NOT verified (honest)
* **No run inside real Electron** (binary not downloadable in this sandbox): window rendering, real
  before-input-event delivery, kiosk/always-on-top/content protection effect, focus/blur, global shortcut
  behaviour, renderer crash/unresponsive events. The stub smoke proves main-process wiring only.
* **No Windows**: nothing OS-level (Alt+Tab, Win, PrintScreen, foreign windows) is blocked or verified.
* Renderer (A07) not present; fallback page only.

## Next (checkpoint 2)
Runtime self-test window (real input pipeline) feeding the capability matrix; VERIFICATION.json for manual
controlled Windows tests; optional Windows helper (dry-run by default) with parent-death watchdog; full
PASS/FAIL/NOT RUN matrix including startup/shutdown/crash/force-close cases; Keyboard Filter/kiosk deployment note.

## Integration order
A06 integrates in parallel with the CV chain (OWNERSHIP integration_order: A06 with A07 after A08). Depends only
on contracts v1 + A01 backend routes; A07 consumes `window.qorgau`.
