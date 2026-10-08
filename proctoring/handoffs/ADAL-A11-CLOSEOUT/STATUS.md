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
| F01 | Previous policy/UI mismatch fixed | Default remains review-after-pause; `Operator.tsx:restAllowed` prevents forbidden REST calls and explains the policy. Optional operator_live_review is bound-session-only in `shell/access.ts`; FixtureBridge mirrors the default gate. This is policy alignment, not a claim that default LIVE review is enabled. Coordinator reports shell suite 105 PASS, 1 SKIP. |
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

## Final verification

Tested committed source **`9c5577a5ee5ce993f641c3b7719664fb1dc37134`**:
**123 PASS, 2 SKIP, 0 FAIL** in 32.44 s. Scope: all `backend/tests`, all
`backend/proctor/evidence/tests`, and `backend/proctor/fusion/tests/test_engine.py`.
Six A11 regressions are included. Raw output `regression.txt`; individual results `regression.xml`.
One existing Starlette/httpx deprecation warning. Skips: real replay acceptance without supplied
media and A08 browser-print check because its Node/Playwright probe is unavailable in this environment.

The launcher imported reviewed `qorgau_qa.fakes.install({})` before pytest, replacing **only**
AudioMonitor with the labelled no-device double. This matters because the older backend
`test_registered_modules_are_used_for_live` injects fake capture/CV but otherwise starts A14 audio.
All storage/TEMP/TMP/models/replay paths were isolated under this task, classroom uplink disabled;
no actual microphone, webcam, CV model or native guard used. Synthetic integrated flow and real
SQLite crash/transaction tests passed. This is separate evidence from the earlier QA 380-pass run,
which predates these production changes; full QA has not been rerun at this SHA.

Run form (with explicit task PYTHONPATH and isolated environment):
`python -c 'from qorgau_qa.fakes import install; install({}); import pytest; raise SystemExit(pytest.main(["proctoring/backend/tests", "proctoring/backend/proctor/evidence/tests", "proctoring/backend/proctor/fusion/tests/test_engine.py", "-q", "--import-mode=importlib", "--basetemp=<task-only-temp>", "--junitxml=proctoring/handoffs/ADAL-A11-CLOSEOUT/regression.xml"]))'`.

Final evidence commit changes only this handoff and sanitized test reports; tested code is unchanged.
Branch remains local and clean after commit. No agent push; coordinator merges and publishes.

## Follow-up: standard lifecycle test isolates audio itself

`backend/tests/test_lifecycle_api.py::test_registered_modules_are_used_for_live` now installs a tiny
local, labelled FakeAudioMonitor in `sys.modules` before application/session creation. Pytest's
monkeypatch restores the module entry afterward. No QA-package dependency, launcher injection or
production switch is required. Assertions verify the runtime chose this class, start/stop were
called for the same session, and its labelled `qa_audio_isolated` health observation reached the engine.

Ordinary command, with only backend/contracts PYTHONPATH and isolated task temp:
`python -m pytest proctoring/backend/tests/test_lifecycle_api.py -q --basetemp=<task-only-temp>`
→ **16 PASS**, 2.27 s, one existing Starlette/httpx deprecation warning. No microphone/camera/model/native
guard was activated. Only this test file and this handoff changed in the follow-up; coordinator owns push.
