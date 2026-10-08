# ADAL-CONTROL-CHAIN

Branch: `codex/adal-control-chain`. Base: `5170478e426beaac7752567d64e197a599021bdf`. Scope: acceptance harness and this handoff; no product changes. Publication is owned by the root coordinator; no push performed here.

## Final result

**22/22 actual acceptance checks passed, zero failures, zero skipped.** The full no-skip runner exited 0 after graceful cleanup. `results.json` is the sanitized final report. Both defects discovered here are fixed in separately owned product commits: scrollbar coverage `52a6ed9` (local cherry-pick `61618fe`) and fresh renderer recovery `3b53958` (local `e25e1ad`). Production main/preload were rebuilt before the final run; React is the production build from this checkout.

Confirmed through actual processes and public teacher UI: custom reason, visible pending then confirmed lock, genuine unlock, independent student targeting, actual teacher feed loss/reconnect, no-renderer timeout, hidden-window refusal, a new visible request after refusal, renderer reload requiring a new genuine `recovery=true` request and painted receipt, full Electron/backend restart preserving identity and re-confirming persisted lock, and isolated second-client disconnect. The public audio module loads but no audio call is started. No renderer page errors.

All sources are SYNTHETIC, sessions CREATED; no native guard, fullscreen, kiosk, camera or microphone was activated. This is proof of the **application overlay**, not OS enforcement or live CV/media. No test helper sends a lock ACK or device status; the production renderer and main independently validate the screen and C2 forwards the genuine result.

Coordinator integration: root already owns the two product fixes. Cherry-pick this branch's acceptance-only commits if avoiding duplicate product history; do not cherry-pick local `61618fe`/`e25e1ad` again when their original commits are already present. Current source syntax (Python/JS) and `git diff --check` pass. No push performed by this agent.

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

The run then exposed a second product recovery issue: reload returns an overlay but leaves the current backend lock failed/unconfirmed. A recovery request is emitted during navigation before the new renderer listens; C2's 5s state republish interval matches its 5s receipt deadline. A later passive new-page probe confirmed the first delivered class state was already `failed`, with `Нет подтверждения экрана приложения в срок`; no fresh requested/recovery event reached that renderer. `reload-before-fix.json` now records fifteen preceding passes and this exact timeout trace. Root owns the correction. The harness requires the new renderer's genuine `recovery=true` request so a stale prior receipt cannot satisfy the reload assertion.

## Independent restart and feed-loss verification

Using the explicit diagnostic `--skip-reload` option, 21 checks pass. This includes full Electron/backend restart with the same C1 identity and persisted lock followed by a new genuine confirmation; unlock after restart; second-backend disconnect isolation; no running exam/fullscreen/kiosk; public audio module mounted without media use. Added actual teacher WebSocket loss/reconnect: the confirmed card label disappears while the real student's overlay and backend confirmation remain; reconnection restores the current card label from real C1 state. Traffic is relayed unchanged and the test closes only its own teacher connection.

`independent-restart.json` explicitly records the single skipped reload scenario. This is earlier diagnostic evidence; the final `results.json` supersedes it with all scenarios passing. The reload fix was implemented by `astra_exam_resume`; no timeout increase or fake ACK is used.
