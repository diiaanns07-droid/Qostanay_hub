# A06 — environment-protection & lifecycle verification matrix

Scope: case requirement 2.3 (restrict Alt+Tab, Ctrl+C/V, Win, PrtScn, tab switching, foreign
windows) plus the shell lifecycle. Honest status, two separate columns:

- **Shell logic** — verified here (Linux container, Node 22, Python 3.12 venv) by `node main/tests/run.mjs`
  and `node main/tests/shell-smoke.mjs` (built `main.cjs` under an Electron **API stub** + the real
  backend). This proves classification, state transitions, IPC/argument rules, event delivery and
  process lifecycle — **not** real OS/Chromium behaviour.
- **Real enforcement** — requires the actual Electron binary and/or Windows. **NOT RUN** in this
  environment (the Electron 43.7.5 binary could not be fetched in the cloud sandbox; no Windows).
  The runtime self-test (`environment/probe.ts`, real input pipeline) and `VERIFICATION.json`
  populate this column on the target machine (A06 checkpoint 2 on Windows / A09).

A capability status is `blocked` only when the mechanism's RESULT was observed; calling an API is
never evidence. In this environment the shell reports (see `node main/tests/print-matrix.mjs`):
in-app items `unverified` (self-test NOT RUN), OS-level items `unsupported` (not Windows).

## Per-shortcut / foreign-window matrix

| Requirement | Shortcut / event | Classification (target Win10/11) | Mechanism | Shell logic | Real enforcement |
|---|---|---|---|---|---|
| 2.3a | Ctrl+C / Ctrl+Insert | **blocked (window)** | before-input-event | PASS (keyboard+guard tests) | NOT RUN |
| 2.3a | Ctrl+V / Shift+Insert | **blocked (window)** + clipboard_blocked | before-input-event | PASS | NOT RUN |
| 2.3a | Ctrl+X / Shift+Delete | **blocked (window)** | before-input-event | PASS | NOT RUN |
| 2.3a | PrtScn | **detected_only** (key) / window excluded from capture (content protection) | global shortcut + setContentProtection | PASS (detect) | NOT RUN |
| 2.3a | Win / Ctrl+Esc | **detected_only** in-window; **blocked** only with a verified OS mechanism | native helper (not delivered) / managed Keyboard Filter | PASS (classify) | NOT RUN — helper not delivered |
| 2.3a | Alt+Tab / Alt+Esc | **detected_only** in-window; **blocked** only with a verified OS mechanism | native helper (not delivered) / managed Keyboard Filter | PASS (classify) | NOT RUN — helper not delivered |
| 2.3b | Ctrl+Tab / Ctrl+PgUp/PgDn | **blocked** (single window, no tabs) | before-input-event | PASS | NOT RUN |
| 2.3b | New window / tab (Ctrl+N/T, window.open) | **blocked (app)** | window_open_handler | PASS (smoke: window.open denied) | NOT RUN |
| 2.3b | Navigation / reload (F5, Ctrl+R, Alt+←/→, off-origin) | **blocked (app)** | will-navigate / will-redirect | PASS (smoke: navigation blocked) | NOT RUN |
| 2.3c | Foreign window foreground | **detected_only** (process basename) with a helper; today focus loss only | native helper (not delivered) | PASS (controller vs fake) | NOT RUN — helper not delivered |
| 2.3c | Focus lost | **detected_only** (never counts as a block) | browser_window_blur | PASS (guard test: duration measured) | NOT RUN |
| — | Alt+F4 / Ctrl+W / window close | **blocked (window)** | window close event | PASS (guard + smoke) | NOT RUN |
| — | DevTools (F12, Ctrl+Shift+I/J/C) | **blocked (window)** | devTools off + devtools-opened close | PASS | NOT RUN |
| — | Print/Save/Open/Find/Zoom | **blocked silently (window)** | before-input-event | PASS | NOT RUN |
| — | Ctrl+Alt+Del / UAC secure desktop | **unsupported** (cannot be blocked) | — | n/a — documented, never claimed | n/a |

Not blockable by design: Ctrl+Alt+Del, the UAC secure desktop, and OS session sign-out. Managed
kiosk / Keyboard Filter (institution deployment) is documented in `native/README.md`; it is never
applied automatically.

## Lifecycle matrix ("no stuck restriction" checks)

"Shell logic" = `node main/tests/run.mjs` (real backend process) and `node main/tests/shell-smoke.mjs`
(built `main.cjs` under the Electron **API stub** + real backend; after every path the smoke asserts the
stub window has kiosk/always-on-top off, closable on and zero global shortcuts). Round 2: 74 + 48 PASS.

| Case | Expected | Shell logic | Real Electron / Windows |
|---|---|---|---|
| Startup: secure window, no restrictions before a session | kiosk off; exam mode only after explicit start | PASS (state + smoke) | NOT RUN |
| Backend spawn + READY handshake | token via stdin only (absent from argv/env), port validated | PASS (integration: /proc check) | NOT RUN |
| Normal start → exam mode | guard engaged only on `running`; teacher unlock cleared | PASS (smoke) | NOT RUN |
| Pause (operator PIN) | restrictions released, session kept | PASS (integration) | NOT RUN |
| Finish | released, `normal`, review open again | PASS (smoke) | NOT RUN |
| Emergency hotkey | release first (no backend dependency), abort, latch: never re-engages | PASS (smoke) | NOT RUN |
| Renderer crash during exam | release, `error`, reload; RUNNING session re-engages when the UI is back | PASS (smoke, stub event) | NOT RUN |
| Renderer unresponsive 5 s | release, `error`; re-engage when responsive | PASS (smoke, stub event, 5 s real timer) | NOT RUN |
| Uncaught exception in main | synchronous release, `enforcement_error` event, session latched (no engage/crash loop) | PASS (smoke) | NOT RUN |
| Backend SIGKILL during exam | release, `error`, restart with new token/port, dead session not resurrected | PASS (smoke + integration) | NOT RUN |
| Backend ignores shutdown | terminate → kill | PASS (integration) | NOT RUN |
| Graceful stdin shutdown with RUNNING session | backend aborts the session and exits 0 by itself | PASS (integration) | NOT RUN |
| Quit during exam | release, flush events, backend stopped, exit 0 | PASS (smoke) | NOT RUN |
| Force close (Alt+F4 / window close) during exam | prevented | PASS (guard + smoke) | NOT RUN |
| Teacher access during exam | default: review closed; opt-in live review: bound session + PIN + expiry | PASS (unit + integration) | NOT RUN |
| Windows helper lifecycle (stdin EOF, stop, heartbeat, parent death, max duration, hook error) | helper unhooks and exits on every path | controller PASS vs protocol fake only | **NOT DELIVERED** (no helper source/build in this round) |

## How to complete the NOT RUN column (Windows, A06-cp2 / A09)

```powershell
cd proctoring\desktop
npm ci                      # downloads Electron 43.7.5
npm run build
npm start                   # logs "capability matrix: {...}" and "self-test: ..."
node main\tests\print-matrix.mjs   # the matrix for this machine (reads VERIFICATION.json)
```

Run a controlled OS-level test (helper built, `QORGAU_SHELL_NATIVE_ENFORCE=1`, dedicated test VM),
observe each key, and record the real result in `desktop/native/VERIFICATION.json` with the exact
`os_release`. Only then do OS-level items read `blocked`/`detected_only` instead of `unverified`.
