# ASTRA-LAUNCH — Windows launchers for actual Classroom C1 + student

Branch: `codex/classroom-windows-launch`; base: `23c6c3b`.
Origin checked: `https://github.com/diiaanns07-droid/Qostanay_hub.git` (assigned Qorgau repo).
Owner paths only: `acceptance/classroom/Start-Teacher.ps1`, `Start-Student.ps1`, `README.md`,
`launcher-tests/` and this handoff. No product source, dependencies, .env or local credentials changed/read.
Additional coordinator assignment after checkpoint 2: `class-panel/tests/e2e/session.e2e.mjs` only;
launchers remain unchanged during the browser-test stage.

## Checkpoints

Checkpoint 1 `6cfb284` committed and **successfully pushed** to `origin/codex/classroom-windows-launch`.
Initial push under restricted network failed; approved escalation succeeded. Checkpoint 2 adds actual backend
runtime coverage, whitespace-label validation and the coordinator's ordinary «Создать класс» UI instructions.

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
  Session creation UI is an integration dependency: coordinator branch `codex/classroom-acceptance` @ `85cccd2`
  adds «Создать класс» / «Новый класс» to the same T02 panel. That product change is outside this owner's paths.
  No hidden installs, firewall changes, execution policy changes or simulator students.

## Observed validation on this Windows machine

`Qorgau-run/proctoring/.venv/Scripts/python.exe` (Python 3.12) used via explicit `-Python`;
source origin probe verifies this worktree, not the editable install's old source path.

`python proctoring/acceptance/classroom/launcher-tests/test_launchers.py`:

- Windows PowerShell 5.1: **9 tests passed, 22.113 seconds**.
- PowerShell 7 (QORGAU_TEST_POWERSHELL selected installed pwsh.exe): **9 tests passed, 31.701 seconds**.

Windows PowerShell 5.1 parse; check-only without data or port binding; invalid server/code/missing Python;
actual C1 readiness + `/login` + `/config.json` REAL on ephemeral loopback port; EOF exit 0; forced parent termination
leaves no C1 listener; busy-port failure; missing Electron binary produces current-copy preparation command.
Actual `proctor serve --token-stdin` backend startup and EOF exit 0 also passed with temporary data, no exam/camera;
join code absent from output. Whitespace-only student label rejected. Same cases passed on both PowerShell versions.
Teacher and student/backend check-only also executed directly and passed.

## Limits / next

Electron binary and dist are absent in this isolated worktree: actual student window intentionally not launched.
README gives `npm ci`, binary recovery command and `npm run build` in current checkout.
No real camera/mic, native hooks, LAN, multi-PC or CV-model validation claimed. Check-only does not verify join code.
Next: coordinator cherry-picks this branch (both commits), combines with its T02 session creation UI, and prepares
that same checkout's Electron build for an explicit manual classroom trial. Normal local startup commands:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\acceptance\classroom\Start-Teacher.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\acceptance\classroom\Start-Teacher.ps1 -Lan
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\acceptance\classroom\Start-Student.ps1 -Server '192.168.1.10:8765' -JoinCode $code -Label 'PC2'
```

Use actual PC1 address/code, and `-Python <existing 3.12 venv python.exe>` when local `.venv` is absent.
Commit SHA/push status are reported with checkpoint message (a commit cannot contain its own SHA).

## Checkpoint 3 — actual C1 browser session workflow

Added portable `proctoring/class-panel/tests/e2e/session.e2e.mjs`. It defaults to its own checkout but accepts
`QORGAU_TEST_ROOT`, `PYTHON`, `PLAYWRIGHT_MODULE`, and `PLAYWRIGHT_CHANNEL` (default `chrome`, headless).
Starts actual C1 on ephemeral loopback port with `--ui class-panel`, temporary data and stdin EOF shutdown.
PIN is read privately from the child handshake. No fixed PIN, route/API/WebSocket mocks, devices, student commands,
public navigation, saved cookie storage or unmasked codes in screenshots. Output directory must be outside both repos.
Temporary runtime data is removed after server stop; evidence remains separately for review.

Observed: **39/39 PASS**, headless installed Chrome on Windows, 6.63 s for the final process run.
Tested the coordinator's `Qorgau-integration` working copy after its error-localization fixes;
`session.js` SHA-256 observed immediately afterwards:
`036244592cf71eb3ca1c8f8fd0bf4980ba60ff74605054298210dd34d53ec225`.

- Login through the real PIN form; REAL adapter and empty database.
- Keyboard Enter/open, Tab avoiding background controls, Escape/focus restoration; cancel creates nothing.
- UI POST creates the class; genuine GET confirms title, allowed URLs, start URL, open state and displayed join code.
  Refresh retains the same session and code.
- Replacement requires an unchecked checkbox; click/Enter without confirmation sends no POST and preserves session.
  Confirmed replacement creates a distinct session; checkbox resets on reopening.
- Invalid protocol and malformed additional address produce localized messages and no create requests.
- Clearing this test browser's cookies causes actual C1 401; UI explains re-login, and re-login confirms no replacement.
- Stopping actual C1 via EOF causes real connection loss; clear localized error, usable cancel/submit, no leftover listener.
- 1366 px and 390 px session bar/modal: no horizontal overflow. Screenshots visually checked at 390 px.

The first run found English `Failed to construct 'URL': Invalid URL` and `Failed to fetch`; coordinator fixed its
`session.js`, this agent made no UI edits. An overly strict first Tab assertion was corrected: native modal dialogs
may focus browser chrome (`document.hasFocus() === false`), but never controls in the page behind the dialog.

Reproduction (PowerShell, from this repository; use corresponding absolute paths on another machine):

```powershell
$env:QORGAU_TEST_ROOT = 'C:/Users/LEGION/.codex/visualizations/2026/10/08/01a1199e-0be4-7bc2-9a7d-ff0fe11cfb02/Qorgau-integration'
$env:PLAYWRIGHT_MODULE = 'C:/Users/LEGION/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
$env:PYTHON = 'C:/Users/LEGION/.codex/visualizations/2026/10/08/01a1199e-0be4-7bc2-9a7d-ff0fe11cfb02/Qorgau-run/proctoring/.venv/Scripts/python.exe'
node proctoring/class-panel/tests/e2e/session.e2e.mjs '<directory outside repository>'
```

Final local evidence (not committed/pushed):
`C:/Users/LEGION/.codex/visualizations/2026/10/08/01a1199e-0be4-7bc2-9a7d-ff0fe11cfb02/session-real-e2e-pass2/`.
Contains masked screenshots plus non-sensitive `results.json`. No real-classroom/device capability claim.
Next: coordinator cherry-picks checkpoint 3 after the already integrated launchers, then can run the test against
its own checkout without `QORGAU_TEST_ROOT`. `node --check` and `git diff --check` also passed.
