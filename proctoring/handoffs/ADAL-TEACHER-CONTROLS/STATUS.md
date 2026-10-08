# Adal teacher controls

Branch `codex/adal-teacher-controls`, base `90ce179d414f4dc0c7f2dc2b3b5e0b863bbcdd05`.
Isolated worktree `Adal-teacher-controls`; root integrates and pushes.

## Checkpoint 1

- New production `class-control-ui/classroom-module.js` uses only C1's active session, student card and
  persisted commands API. It never creates/selects a second T04 exam or sends through T04's separate bus.
- Fixed, authenticated `/api/teacher/control/assets/` allowlist; panel loader mounts the `exams` module.
- Start/finish and close/open Adal screen controls; visible reason presets/custom reason; explicit finish
  confirmation; pending, failed, receipt confirmed and unknown states. HTTP 202 is only acceptance.
- Scoped lock status propagated through `classroom/contracts/models.py`, persisted device status and cards.
  Old `locked: false`, stale status, server restart, reconnection and lost teacher feed cannot confirm unlock.
  Historical ACK remains an execution report; the UI requires its scoped app receipt to label it confirmed.
- Small card/drawer label corrections say Adal screen, never Windows lockdown.

Validation so far: 66 Node unit tests PASS; 74 classroom contract tests + 1 strict receipt check PASS; 3 new C1 tests PASS
(strict receipt contract, wire/storage/card/stream lifecycle, authenticated assets + canonical failed command).
Sandboxed subprocess startup timed out before READY; scoped unsandboxed localhost-only rerun passed in 3.16 s.
No camera, microphone or OS guard used. Strict JS check and browser acceptance are next.

No push performed by worker; parent owns publication. No physical multi-PC or actual renderer receipt test
claimed here: real C1 tests use synthetic WS peers. Root owns actual Electron main receipt wiring.

## Completed verification

- Strict TypeScript checking of production controls, their imports and touched panel model/loader: PASS.
- Control UI + panel unit suites: **66 PASS** (39 controls, 27 panel).
- New actual C1 tests: **4 PASS**, including a server restart with persisted status, resume and fresh receipt.
- Classroom server/contracts/T04 broader regression: **207 PASS, 2 FAIL**. Both failures are unchanged
  legacy-audio assumptions in `test_persistence_audio.py`: `test_audio_signaling_is_bound_to_an_acked_audio_session`
  and `test_audio_ends_when_student_goes_offline` still expect core `audio_start` to create a command. The
  integrated core intentionally refuses that path with `audio_extension_required`. Audio tests not edited.
- Real C1 + Chrome + synthetic student peer: **19/19 PASS**, including the actual capability loader,
  current-class-only controls, reason presets/validation, pending vs receipt, negative ACK, legacy ACK,
  lock/unlock, start/finish, stale status, replaced classroom and 390 px layout. No page errors. A historical
  receipt never supplies the current card state. Mobile controls screenshot visually inspected.
- Sanitized evidence: `evidence/browser-results.json` and `evidence/controls-mobile.png`. Only synthetic
  labels appear; no credentials, raw teacher/student wire data, recordings or device observations copied.

### Coordinator integration requirement

Root owns `classroom/server/app.py`. The browser run included its approved one-line mapping of
`("T04", "exams")` alongside T03/T05 in `/config.json`. That local test-only line is **not committed here**;
root is applying it in the integration branch. Without the mapping assets/APIs work but controls are not
automatically loaded. No other root-owned file change is required by this branch.

Reproduce after that root mapping, from `proctoring/` with prepared runtimes:

```powershell
# PYTHON points at a Python 3.12 runtime with project dependencies.
# PLAYWRIGHT_MODULE points at installed Playwright; PYTHONPATH must resolve this checkout.
node class-control-ui/tests/classroom.e2e.mjs <external-output-directory>
python -m pytest -q --tb=short classroom/server/tests/test_lock_status.py classroom/contracts/tests
node --test class-control-ui/tests/*.test.mjs class-panel/tests/unit/*.test.mjs
```

Next: root merge this branch and verify actual C1 -> C2 -> Electron receipts with its new main wiring.
The browser peer reports synthetic receipts; this run does not prove an actual student renderer or Windows
lockdown. Backend/C1 preserves the receipt fields; class-panel hides lock claims when its own live feed is lost.
