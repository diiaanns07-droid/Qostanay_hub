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

Validation so far: 66 Node unit tests PASS; 75 classroom contract checks PASS; 3 new real C1 tests PASS
(strict receipt contract, wire/storage/card/stream lifecycle, authenticated assets + canonical failed command).
Sandboxed subprocess startup timed out before READY; scoped unsandboxed localhost-only rerun passed in 3.16 s.
No camera, microphone or OS guard used. Strict JS check and browser acceptance are next.

No push performed by worker; parent owns publication. No physical multi-PC or actual renderer receipt test
claimed here: real C1 tests use synthetic WS peers. Root owns actual Electron main receipt wiring.
