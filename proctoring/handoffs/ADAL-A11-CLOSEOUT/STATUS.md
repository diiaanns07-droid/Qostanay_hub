# ADAL A11 closeout

Branch `codex/adal-a11-closeout`, isolated checkout based on `1cdc210`. Coordinator owns push.
Original review read from `origin/codex/proctor-A11-r2:research/proctoring-review-r2/A11/REVIEW-INTERIM.md`
and `findings.finder.json`. Imported repro scripts were not executed. The original findings describe
older module SHAs; they are not automatically current failures.

## Checkpoint 1

Implemented targeted F08/F17 storage failure visibility and bounded incident retries, F14 paused
metadata, F16 persisted read/lifecycle consistency, and F09 pre-start filtering. Six new regressions
passed using real SQLite and fusion with labelled synthetic inputs; no camera/audio/model/native
guard activated. Broad regression verification and final disposition evidence follow.

Scope extensions explicitly approved by coordinator: two backend app read handlers and its error
import; fusion's paused health-only update; visible report branding changed to Adal (protocol names
unchanged). No capture/phone/attention/contracts/desktop changes.

Failure detection preserves A08's non-raising recording API through a monotonic error counter.
Session fault reporting updates memory/stream without immediately rewriting the failed store.
Retry storage retains only the latest deep-copied incident per ID, at most 2000 IDs, 32 retries per
one-second pass, plus one final pass before terminal persistence. Permanent disk failure remains
visible; this is not a durable disk-failure journal and cannot guarantee recovery after process exit.

## Current disposition of all 17 findings

| ID | Disposition | Current evidence / remaining action |
|---|---|---|
| F01 | Previously fixed in source | IPC `decideAccess` considers operator/session context; coordinator reports full shell suite 105 PASS, 1 SKIP. This task does not claim physical Electron enforcement. |
| F02 | Previously fixed in source | `main.ts` clears operator on main-frame navigation/renderer loss; `shell/state.ts` clears it on error. Coordinator checkpoint `3935a30`. |
| F03 | Previously fixed in source | `shell/state.ts:bind` clears operator; access policy gates calibrationSkip. Coordinator's shell regression run covers the integration. |
| F04 | Still open, desktop policy owner | `shell/operator.ts:check` still rejects correct PIN during exponential lockout; IPC logs but does not persist failed attempts. Needs explicit operator-access/audit policy, not removal of brute-force protection. |
| F05 | Previously fixed in source | App PIN maxLength=64; shared skip dialog in Preflight maxLength=200. No hardware proof implied. |
| F06 | Previously fixed in source | `liveStore.ts` retains REST-owned review/evidence overlay independently of engine seq; equal-seq REST refreshes it. |
| F07 | Previously fixed in source | `Summary.tsx` uses explicit pending count or subtracts only decided reviews. |
| F08 | Fixed here, regression passing | Actual SQLite fault hook fails CLOSED transaction; manager exposes STORAGE_ERROR and HealthMsg; latest seq retries before finish, REST/export close consistently. |
| F09 | Fixed here; formatter previously fixed | Stamp RUNNING/start before accepting CV and reject pre-start timestamps. `sessionT` already formats negative offsets using absolute magnitude. |
| F10 | Previously fixed geometry; physical accuracy unverified | Calibration renders `calfs`, CSS fixed/inset:0, center50% and edge targets44px; half-stage remains unused legacy CSS. Needs real calibration accuracy acceptance, outside this task. |
| F11 | Partially mitigated; replay owner action remains | Start/resume seed current capture health, so ended capture is not invisible. Replay still starts when preflight opens capture, consuming pre-exam frames; restart/start boundary needs coordinated capture design. |
| F12 | Still open, replay timeline integration | Capture maps replay media PTS to session time; lifecycle/store transitions use wall clock. speed!=1/lockstep still need one authoritative clock. No capture/contract redesign here. |
| F13 | Still open, attention owner | Calibration timeout still advances only in `CalibrationController.offer`; no-frame wall timeout absent. Needs explicit failure/presentation behavior, with attention owner. |
| F14 | Fixed here, regression passing | Health/environment traverse paused queue and persist; CV still drops. Fusion only updates health cache during pause (no new episode), so recovery clears stale resume gap. |
| F15 | Attribution corrected previously; reason detail remains limited | Baseline report label is neutral `прервана`; arbitrary abort reason remains only a log value, no typed persisted field. No operator attribution claim; reason schema change deferred. |
| F16 | Fixed here, regression passing | Read routes use existing persisted session; mutation routes return409 for known persisted session,404 unknown. No runtime/device resurrection; delete restores404. |
| F17 | Fixed fault visibility here, regression passing | Real store error counter reaches manager fault/health/last_error path; existing raising stores still supported. Raw observations are not queued for durable retry during a permanent disk failure. |

Next: coordinator integrates tested checkpoint and owns F04/F11/F12/F13 and remaining physical checks.
Do not describe all 17 as newly fixed or all 17 as still open.
