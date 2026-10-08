# ADAL-CONTROL-CHAIN

Branch: `codex/adal-control-chain`. Base: `5170478e426beaac7752567d64e197a599021bdf`. Scope: acceptance harness and this handoff; no product changes. Publication is owned by the root coordinator; no push performed here.

## Checkpoint 2026-10-08

Built the production desktop successfully. Added a real C1 + two real proctor/C2 processes + production Electron + Chrome teacher UI harness. No mock backend, fixture main, fabricated device status or manually sent lock ACK. All sources are SYNTHETIC, sessions stay CREATED, no preflight/start/native guard/camera/microphone.

Verified: production IPC creates a synthetic session; C1 receives two independent actual C2 identities/provenance; initial false lock is not confirmed unlock. The first real teacher lock fails honestly and reveals a production geometry bug:

- React renders the requested custom reason, `inert=true`, `aria-hidden=true`, visible opaque overlay with center hit testing passing.
- At Windows display scaling, fixed overlay bounds were right=1251.20007/bottom=763.20001 while `innerWidth=1266`/`innerHeight=763`; the approximately 15px vertical scrollbar is outside the content viewport.
- `ClassLockController.confirmApplied` compared coverage against `innerWidth`, sending a genuine negative receipt. C2 records `failed`; C1 and teacher UI show failure without claiming a lock.

Reported the exact measurements to root. Root owns the product correction and regression test. This acceptance harness is kept unchanged as the independent recheck after that fix. Remaining lock/unlock/reload/restart scenarios are implemented but not yet passed at this checkpoint.

Run instructions and evidence boundaries: `acceptance/classroom/control-chain/README.md`. Raw runtime data lives outside Git; commit only sanitized results and cropped screenshots.

## Scrollbar fix recheck

Root fix `52a6ed9` was cherry-picked as `61618fe`; rebuilt production main/preload. Twelve checks now pass: actual UI lock/receipt/unlock, custom reason, pending, isolated second student, real no-renderer timeout, hidden-window refusal, and a fresh visible request after refusal. `actual-lock.png` was visually inspected; it shows only the synthetic lock and custom reason, no credentials.

The run then exposed a second product recovery issue: reload returns an overlay but leaves the current backend lock failed/unconfirmed. A recovery request is emitted during navigation before the new renderer listens; C2's 5s state republish interval matches its 5s receipt deadline. Root has the report and owns the correction. `reload-before-fix.json` records twelve passes and the exact failed scenario; full restart remains unverified until rerun. The harness now passively observes the new renderer's genuine `recovery=true` request so a stale prior receipt cannot satisfy the reload assertion.

## Independent restart and feed-loss verification

Using the explicit diagnostic `--skip-reload` option, 21 checks pass. This includes full Electron/backend restart with the same C1 identity and persisted lock followed by a new genuine confirmation; unlock after restart; second-backend disconnect isolation; no running exam/fullscreen/kiosk; public audio module mounted without media use. Added actual teacher WebSocket loss/reconnect: the confirmed card label disappears while the real student's overlay and backend confirmation remain; reconnection restores the current card label from real C1 state. Traffic is relayed unchanged and the test closes only its own teacher connection.

`independent-restart.json` explicitly records the single skipped reload scenario. The reload fix is delegated by root to `astra_exam_resume`; no timeout increase or fake ACK is used.
