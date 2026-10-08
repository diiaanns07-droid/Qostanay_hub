# Teacher (operator) access during the exam — A06 ↔ A07 ↔ A01

Owner: A06 (shell). Applies to every `window.qorgau` call; enforced in Electron main
(`main/src/shell/access.ts`, wired in `main/src/ipc/api.ts`). The renderer cannot bypass it: the
check runs in main before any backend request. Generated from the same code
(`accessTable()`; unit-tested in `main/src/__tests__/access.test.ts`, integration-tested against the
real backend in `backend.integration.test.ts`).

## Model (the safe default needs no approval; the opt-in does)

1. **Default — `review_after_pause`.** While exam restrictions are engaged (`ShellState.exam_mode_active`)
   nobody can open history, incidents, evidence, summary, export or delete — not even with the PIN.
   The teacher reviews **after `pauseExam`** (operator PIN; restrictions are released for the pause and
   the interval is a coverage gap, CONTRACTS §2) **or after `finishExam`**. Live incidents still reach
   the renderer through `subscribeEvents` (stream), so a teacher console can show *that* an episode
   opened; acting on it (review, evidence) waits for pause/finish.
2. **Opt-in — `operator_live_review`** (`QORGAU_SHELL_OPERATOR_LIVE_REVIEW=1`), **pending A01 approval,
   off by default.** During the exam an operator-**unlocked** console may call `listIncidents`,
   `getIncident`, `getEvidence`, `getSummary`, `addReview` for the **bound session only**. History
   (`listSessions`), `exportReport`, `deleteSession` stay closed until the exam is not running.
   Restrictions stay engaged while the teacher reads.

Both policies:
* Operator methods need `operatorUnlock(pin)`: scrypt hash from `QORGAU_OPERATOR_PIN_HASH`
  (`node main/tools/hash-pin.mjs`), constant-time compare, 5 failures → growing lockout. No demo PIN
  in production; the one-time demo PIN exists only with `QORGAU_SHELL_DEMO_OPERATOR=1` and is printed to
  the operator's terminal.
* The unlock is cleared on **every** engage of exam mode — start, resume and crash-recovery re-engage —
  so after a resume the student cannot pause again without the teacher; and it **expires** after
  `QORGAU_SHELL_OPERATOR_IDLE_S` (default 180 s) without an operator call and after
  `QORGAU_SHELL_OPERATOR_MAX_S` (default 1800 s) in total. Expiry pushes `ShellState` with
  `operator_unlocked=false` immediately (timer), so the UI locks even if nothing is clicked. The timer
  is monotonic (system clock changes do not extend it); only successful calls that needed the unlock
  refresh it (polling PIN-free reads does not).
* History is teacher-only: `listSessions` and any call on another session need the unlock outside the
  exam and are refused during it.
* `calibrationSkip` now requires the unlock (A07 already asks for the PIN there; the shell enforces it).
* Student/lifecycle methods are never closed by this policy (answers, exam, finish, emergency exit).
  Other shell rules still apply on top: `startExam`/`resumeExam` only for the session created in this
  shell run; `createSession` refused while an exam is engaged; pause/finish/abort of another session
  refused while one holds exam mode.

## Availability table

"exam" = an exam is in progress: restrictions engaged **or** the current (bound) session is still
`running` (e.g. after a renderer-crash or main-exception release); paused/finished = not in exam.
"other session" = any session id that is not the one created in this shell run (history).
During the exam **no** session-scoped method accepts another session id, whatever the PIN state.

| method | current session, not in exam: locked / unlocked | other session, not in exam: locked / unlocked | exam: locked / unlocked (default) | exam, unlocked (live-review opt-in) |
|---|---|---|---|---|
| `getShellState` | yes / yes | yes / yes | yes / yes | yes |
| `getEnvironmentCapabilities` | yes / yes | yes / yes | yes / yes | yes |
| `operatorUnlock` | yes / yes | yes / yes | yes / yes | yes |
| `operatorLock` | yes / yes | yes / yes | yes / yes | yes |
| `requestEmergencyExit` | yes / yes | yes / yes | yes / yes | yes |
| `health` | yes / yes | yes / yes | yes / yes | yes |
| `listSessions` | — / yes | — / yes | — / — | — |
| `createSession` | yes / yes | yes / yes | yes / yes | yes |
| `getSession` | yes / yes | — / yes | yes / yes | yes |
| `runPreflight` | yes / yes | — / yes | yes / yes | yes |
| `calibrationStart` | yes / yes | — / yes | yes / yes | yes |
| `calibrationTarget` | yes / yes | — / yes | yes / yes | yes |
| `calibrationState` | yes / yes | — / yes | yes / yes | yes |
| `calibrationFinish` | yes / yes | — / yes | yes / yes | yes |
| `calibrationCancel` | yes / yes | — / yes | yes / yes | yes |
| `calibrationSkip` | — / yes | — / yes | — / yes | yes |
| `startExam` | yes / yes | — / yes | yes / yes | yes |
| `pauseExam` | — / yes | — / yes | — / yes | yes |
| `resumeExam` | — / yes | — / yes | — / yes | yes |
| `finishExam` | yes / yes | — / yes | yes / yes | yes |
| `abortExam` | yes / yes | — / yes | yes / yes | yes |
| `getExam` | yes / yes | — / yes | yes / yes | yes |
| `saveAnswer` | yes / yes | — / yes | yes / yes | yes |
| `listAnswers` | yes / yes | — / yes | yes / yes | yes |
| `listIncidents` | yes / yes | — / yes | — / — | yes |
| `getIncident` | yes / yes | — / yes | — / — | yes |
| `addReview` | — / yes | — / yes | — / — | yes |
| `getEvidence` | — / yes | — / yes | — / — | yes |
| `getSummary` | yes / yes | — / yes | — / — | yes |
| `exportReport` | — / yes | — / yes | — / — | — |
| `deleteSession` | — / yes | — / yes | — / — | — |

## Refusals the UI should handle (all `BridgeResult.ok=false`, contract `ErrorCode` + `details.shell_code`)

| `shell_code` | `code` | Meaning for the UI |
|---|---|---|
| `exam_mode_active` | `INVALID_STATE` | closed while the exam runs → offer "Пауза" (teacher) or show after finish |
| `operator_locked` | `INVALID_STATE` | PIN required or the unlock expired → show the PIN dialog |
| `operator_pin_wrong` | `UNAUTHORIZED` | wrong PIN |
| `operator_pin_rate_limited` | `UNAUTHORIZED` | too many attempts; `retryable=true`, wait |
| `operator_pin_not_configured` | `NOT_IMPLEMENTED` | no PIN on this computer → teacher features unavailable |
| `session_not_bound` | `SESSION_MISMATCH` | only the current session during the exam / start-resume of a foreign session |

## Requests

* **A07:** do not render review/evidence/export buttons as available while `exam_mode_active` unless the
  live-review policy is active *and* `operator_unlocked`; treat `operator_locked` after expiry as "lock
  the console", not as an error toast. The policy in use is visible in the shell log; if the UI needs it
  at runtime, ask A01 for a `ShellState.access_policy` field (contract change).
* **A01:** approve or reject `operator_live_review` for the demo; until then the shell runs the default.
  Optional contract addition for the audit trail: an `EnvironmentAction` (e.g. `operator_unlocked`) so a
  teacher unlock during the exam appears in the session report (today it is only in the shell log).
