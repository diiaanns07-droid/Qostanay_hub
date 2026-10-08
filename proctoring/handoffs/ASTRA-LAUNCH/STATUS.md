# ASTRA-LAUNCH — Windows launchers for actual Classroom C1 + student

Branch: `codex/classroom-windows-launch`; base: `23c6c3b`.
Origin checked: `https://github.com/diiaanns07-droid/Qostanay_hub.git` (assigned Qorgau repo).
Owner paths only: `acceptance/classroom/Start-Teacher.ps1`, `Start-Student.ps1`, `README.md`,
`launcher-tests/` and this handoff. No product source, dependencies, .env or local credentials changed/read.

## Checkpoint 1

- Both scripts support Windows PowerShell 5.1, optional `-Python`, explicit current-checkout `PYTHONPATH`,
  short Russian readiness errors and exit code 1 on failure. UTF-8 BOM is intentional for Windows PowerShell 5.1.
- Teacher runs real `classroom.server`, T02 REAL panel. Loopback by default, explicit `-Lan` for `0.0.0.0`.
  Readiness is parsed from C1; URL uses actual port including `-Port 0`. PIN is transient console output only.
- Student requires `-Server -JoinCode -Label`, real local Electron binary/build; rejects absent/stale build.
  Old checkout Electron/UI is never used as fallback. `-BackendOnly` runs actual backend C2 for diagnostics.
  Generated backend bearer token travels through stdin and is never output. Join code is redacted in output.
  Native enforce explicitly off; `ELECTRON_RUN_AS_NODE`, NODE_OPTIONS and external dev renderer removed in child env.
- Shared private functions are in Start-Teacher (Start-Student dot-sources with `-Library`). No extra runtime file.
- Child awaits launch payload until attached to Windows kill-on-close Job Object. C1/backend stdin EOF allows
  normal cleanup; abrupt launcher death closes the job and terminates descendants. No background service installed.
- `-CheckOnly` imports/probes local dependencies and paths, without app startup, camera, microphone, hooks or network.
- README contains explicit setup, PC1 PIN/session/join-code flow, PC2/3 commands, data paths and shutdown.
  No hidden installs, firewall changes, execution policy changes or simulator students.

## Observed validation on this Windows machine

`Qorgau-run/proctoring/.venv/Scripts/python.exe` (Python 3.12) used via explicit `-Python`;
source origin probe verifies this worktree, not the editable install's old source path.

`python proctoring/acceptance/classroom/launcher-tests/test_launchers.py`: **7 tests passed, 16.351 seconds**.
Windows PowerShell 5.1 parse; check-only without data or port binding; invalid server/code/missing Python;
actual C1 readiness + `/login` + `/config.json` REAL on ephemeral loopback port; EOF exit 0; forced parent termination
leaves no C1 listener; busy-port failure; missing Electron binary produces current-copy preparation command.
Teacher and student/backend check-only also executed directly and passed.

## Limits / next

Electron binary and dist are absent in this isolated worktree: actual student window intentionally not launched.
README gives `npm ci`, binary recovery command and `npm run build` in current checkout.
No real camera/mic, native hooks, LAN, multi-PC or CV-model validation claimed. Check-only does not verify join code.
Next: add actual backend readiness/stop integration coverage and review launch edge cases; then coordinator cherry-picks
the pushed launcher commit and prepares this same checkout's Electron build for an explicit manual classroom trial.
Commit SHA/push status are reported with checkpoint message (a commit cannot contain its own SHA).
