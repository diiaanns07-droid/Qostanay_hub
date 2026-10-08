# ADAL-LOCK-RECOVERY

Branch: `codex/adal-lock-recovery`, based on `f3a4a28` in Qostanay_hub.

The production renderer can reload while the local backend WebSocket stays connected. The navigation-time loss notification generated a recovery request before the replacement renderer subscribed; the next periodic state arrived after its receipt deadline.

Preload now reports subscription availability only after registering the callback. Main accepts that notification only from the existing trusted primary frame. The first subscription waits for the previous renderer-loss invalidation, then asks the local backend for fresh scoped proof. Later subscribers receive only the current, unexpired class state; they do not invalidate proof again. The cache is cleared on navigation, backend loss and local-session change. Audio commands and other stream events are never cached. The existing independent DOM/visibility verification and exact request receipt remain the only ACK path; receipt deadlines are unchanged.

Product fix: `3b53958`. Final validation:

- Focused class-lock suite: 11 PASS, including ordered loss/readiness, cancellation, late subscribers, cache invalidation, scope and expiry rejection.
- All three TypeScript configurations: PASS.
- Electron + production renderer build: PASS (Windows realpath required escalation).
- Full shell suite with explicitly selected shared Python: 111 PASS, 1 expected Windows skip, no failures. This includes real synthetic-backend lifecycle tests and protocol-only helper fixtures. The first run without the interpreter failed on a missing local `.venv`; the explicit-interpreter rerun resolved that environment issue.
- Independent upstream audit: actual production chain **22/22 PASS**, zero failures or skips, exit 0 and clean process cleanup. Reload delivers a genuine `recovery=true` request to the new page, produces the genuine painted-state receipt, and returns C1 to applied/confirmed. Full Electron/backend restart, hidden/no-renderer refusals, teacher connection loss/reconnect and other-student isolation also pass. Evidence: `codex/adal-control-chain` commit `448b59626c8016e0a95d119b9d82996c60de408f`, `proctoring/handoffs/ADAL-CONTROL-CHAIN/results.json` (the product commit is cherry-picked there as `e25e1ad`).

No camera, microphone, native keyboard hook or enforced exam mode was exercised. The actual chain covers application class-control with no running exam; it is not a physical lockdown or active-exam telemetry stress test. Root integrates and pushes; this agent did not push.
