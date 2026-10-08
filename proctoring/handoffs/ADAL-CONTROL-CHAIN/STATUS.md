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
