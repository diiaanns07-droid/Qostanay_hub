# ADAL-EXAM-RESUME — bounded external exam resume

Branch: `codex/adal-exam-resume`, baseline `ac9cdad`; origin is the explicitly assigned `Qostanay_hub` repository. Coordinator integrates and pushes; this agent does not push or change main. Own files: `desktop/main/src/exam/surface.ts`, `desktop/main/tests/exam-surface.fixture.ts`, this handoff.

## Completed checkpoint (2026-10-08)

HTTP exam documents retain the same isolated WebContents/DOM and unsent textarea answers during class lock, temporary class/backend stream disconnection, pause, recoverable shell error, operator view and trusted-dialog hiding. Main immediately closes the request gate, detaches/hides/mutes the view, stops loading; then asynchronously closes HTTP connections and freezes Chromium page lifecycle through main-only CDP. Resume is serialized after suspension; repeated lock/unlock cannot expose a stale thaw. No preload, remote-debugging port or site-accessible bridge was added. Existing allowlist, sandbox, worker/download/popup/permission restrictions remain.

The current production ShellStateMachine uses `preflight` for paused sessions and `error` for recoverable backend/renderer failures; those preserve the document. `normal` is terminal/emergency exit/unbound, so it clears the document and ephemeral storage. New session ID, policy replacement and disposal clear the old document/session. A stopped partial page may need explicit Reload; reload or site code can still discard answers. Remote submission remains the website's responsibility.

## Proven limit and safe fallback

Actual Electron 43.7.5 testing found that neither `Session.closeAllConnections()` nor session offline emulation closes an already upgraded WebSocket. Offline emulation was investigated and removed. A document that attempted an allowed WebSocket therefore falls back to destruction on suspension, closing the socket and displaying an explicit warning that unsaved answers may have been lost. Active WebSocket compatibility remains; no silent global transport ban. Detection is conservative even after a socket closes. A detached/failed lifecycle debugger also destroys the document with an explicit warning.

Freeze and connection closure complete asynchronously, after synchronous view hiding/muting/request denial. This is not proof of instantaneous process suspension or every Chromium transport (for example WebRTC/WebTransport), and not an OS firewall/application lockdown. No real external vendor/SSO, hardware capture or native guard was exercised. Page lifecycle controls suspend the tested timers; they cannot promise preservation against the site's own freeze/resume scripts, renderer crash, reload, or server-side session expiration.

## Validation

- `node scripts/typecheck.mjs`: all 3 projects PASS.
- `node main/tests/run.mjs exam-policy web guard state`: all 32 PASS; injected guard errors are expected, FakeGuard only.
- `node main/tests/exam-surface.mjs`: 20 actual Electron checks PASS. Retains all original 13 scenario areas and extends lifecycle assertions: typed field survives lock/unlock, stream replay, reconnect, real ShellStateMachine pause/recovery; hidden timers remain frozen and allowlisted fetch/beacon/image/WebSocket attempts never reach fixture server; existing HTTP streaming response closes; rapid relock remains hidden; actual finish/new running session/policy replacement destroy old contents and replace/clear storage; WebSocket fallback closes the server-observed connection; missing freeze controller fails closed.
- Fixtures run hidden BrowserWindow, local ephemeral loopback servers, synthetic answer text, no backend process/device capture/native guard. Main-only test CDP inspection intentionally bypasses page scheduling to exercise the request gate; the remote document has no access to this control.
- Dependency junction targets the coordinator's existing `Qorgau-integration/proctoring/desktop/node_modules`; no dependency/lockfile changes. Sandbox blocked the initial fixture and junction creation; bounded escalated commands succeeded.

Next: bound lifecycle acknowledgement wait, then rerun fixture/typecheck and report final SHA to coordinator for integration and push. Review must retain the explicit WebSocket/async-freeze limitation in product claims.

References: [Electron webContents](https://www.electronjs.org/docs/latest/api/web-contents), [CDP Page lifecycle](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-setWebLifecycleState), installed Electron typings; runtime assertions take precedence over API assumptions.
