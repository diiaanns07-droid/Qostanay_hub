# ADAL-LOCK-ACK — integration handoff

Branch: `codex/adal-lock-ack`. Origin verified:
`https://github.com/diiaanns07-droid/Qostanay_hub.git`.
Baseline merged from coordinator: `fc8a3a86ec78421c8db80d42e774a58fc367789c`.
Assigned scope: student lock request/receipt, local API, focused renderer UI and
new Electron helper. Teacher UI and production main.ts remain coordinator-owned.
No push performed: isolated subtask commits are for coordinator integration.

## Behavior

- C2 publishes `lock_request` containing command/student/class/local-session IDs,
  backend instance, fresh request token, desired boolean, reason and deadline.
  `locked` remains the last confirmed effective app-overlay value.
- React applies the opaque overlay and inert app, waits two animation frames,
  then invokes the dedicated preload method. Main independently checks the
  primary frame DOM, exact reason/token, visibility, viewport coverage and inert
  content before POSTing the scoped receipt to `/v1/class/lock/ack`.
- Teacher success ACK is sent only after that local receipt is accepted. Missing
  Electron, failed rendering, wrong receipt scope, expiry and supersession never
  produce a successful lock ACK. Unlock also requires confirmed overlay removal.
- Journal prevents old command execution after restart. Previously confirmed
  lock intent is restored with a new backend instance/token and needs fresh UI
  proof. Renderer crash/reload likewise invalidates proof and requests recovery.
- Five-second local confirmation limit is capped by command TTL/absolute expiry.
  The limit is the application deadline, not the duration of an applied lock.
- `lock_confirmed=false` means unknown current confirmation, NOT a confirmed
  unlock. `lock_requested=true` preserves desired blocking even if recovery
  confirmation times out. Historical command ACKs remain historical.
- Scope is `app_overlay`. No OS-wide lockdown is claimed or enabled by this code.
  Existing exam guard behavior belongs to the shell; this module does not invoke it.
- Source provenance, sticky class_state replay, resume_rejected and unsupported
  legacy audio refusal remain intact. Safe computer_name metadata is now emitted.

## Coordinator integration REQUIRED

Merge audio agent first/alongside this branch: client.py/app.py include that
agent's agreed hooks for AudioBridge and install_audio_routes. The sibling
`uplink/audio.py` from `1933195` was borrowed untracked for local tests only and
is intentionally not included in our commits.

Production main.ts needs these hooks (sent to coordinator and exam agent):

1. Import `ClassLockController, LOCK_ACK_CHANNEL` from `./class-lock`.
2. Instantiate with `{ client, window: () => mainWindow,
   setExamBlocked: blocked => examSurface?.setBlocked('class-lock', blocked) }`.
3. On every class_state, call `classLock.consumeClassState(msg)` before forwarding
   the event to the renderer; exam surface's own class-state handling may follow.
4. Register dedicated ipcMain.handle; reject unless existing trustedSender(event)
   passes, otherwise call `classLock.confirmApplied(body)`.
5. Backend loss: `classLock.reset()`. Renderer render-process-gone and main-frame
   reload/navigation: `void classLock.rendererLost()` (blocks synchronously before
   notifying backend). The new `/v1/class/lock/lost` endpoint rejects old instances.

C1 additive status propagation is a NEXT-WAVE COORDINATOR task (we do not own it):
Status/Core/StudentStatus/teacher card must preserve these now-transmitted fields:
`lock_state: unconfirmed|requested|applied|failed`, `lock_confirmed: bool`,
`lock_requested: bool`, `lock_scope: app_overlay`.
Existing C1 ignores unknown fields; do not interpret boolean locked=false as a
confirmed unlock when lock_confirmed is false. Pending/sent command is distinct
from acknowledged effective state. Local class_state additionally includes
lock_requested_reason_ru and lock_error_ru for the student shell.

## Verification

- Full C2 uplink suite: 42 passed, including final renderer-loss/status changes.
- Focused lock/recovery: 19 passed after renderer-loss/status fields.
- Main helper: 5 passed. Renderer state/parser regression suite: 7 passed.
- `npm run typecheck` and complete desktop product build: PASS.
- Actual Electron fixture: 7 checks PASS (React paint + real preload IPC + main
  DOM inspection, exact reason, inert content, wrong student refusal, unlock,
  restored interaction, stale reset receipt, expired request).
  Run from desktop: `node renderer/tests/lock-electron/run.mjs`.
  Evidence local ignored `dist/test-lock-electron/results.json` and `lock.png`;
  screenshot inspected. Windows fractional DPI needs a 1 CSS-pixel bounds
  tolerance; this was found by the real Electron run and fixed.
- Test fixture imports no production main or native guard. Camera/mic permissions
  denied; no media devices opened. Real Electron fixture uses a local backend
  stand-in; full Python API test uses explicit UI receipt stand-in. Combined
  production C1/C2/Electron deployment needs coordinator's main.ts integration.
- Initial esbuild runs hit sandbox ancestor-read restrictions; explicitly scoped
  elevated test/build runs passed. Python uses isolated data basetemp, explicit
  PYTHONPATH, shared Qorgau-run .venv. No secrets read; dependency copy is ignored.

Next: merge these commits, complete main.ts + C1 status propagation, then run the
integrated teacher command acceptance with exam-surface detachment.
