# Windows verification protocol (manual / LIVE) — prompt items 3, 4, 7, 8, 9

Owner: A09. **None of these checks can be PASS from the Linux cloud container.** They are executed on the target
Windows 10/11 x64 laptop (or an identical VM) on the A01 integration candidate SHA, and recorded in
`qa/RESULTS.md` with: exact Windows build/edition (`winver`), account type (standard/admin), Electron/Python
versions, keyboard layout(s), date, tester, verdict, evidence (screenshot/log name — no faces of real students).

Verdict per item: `PASS` / `FAIL` / `NOT RUN` / `N/A (by design)`. For environment protection the verdict is the
**observed** enforcement: `blocked` (action had no effect, verified) / `detected_only` (action happened, an event was
recorded) / `not detected`. It must equal what the shell reports in `EnvironmentCapabilities` — a mismatch is a FAIL.

## 0. Preconditions

* Clean Windows user profile, **standard user** (no admin) for W-INST/W-ENV; admin only where marked.
* Build from the candidate SHA following `proctoring/README.md` (or `packaging/` bundle when available).
* Models/assets prepared beforehand with checksums (manifests of A03/A04); then network **off** (see W-OFF).
* A second app open for foreground tests: Notepad, a browser window, Windows Camera app (for F3 only).
* Emergency exit known to the tester before starting (documented by A06) — verify it first (W-ENV-12).

## 1. Install / launch (item 7, 9)

| ID | Check | Procedure | Expected |
|---|---|---|---|
| W-INST-1 | One-command start from working dir | `cd proctoring` → documented start command | App window opens, backend READY, health shows real modules (not `module_not_integrated`) |
| W-INST-2 | Path with spaces + Cyrillic | Copy bundle to `C:\Users\<user>\Мои документы\Qorgau Exam тест\` and start | Same as W-INST-1; data under `%LOCALAPPDATA%\QorgauExam`, nothing written into the bundle |
| W-INST-3 | No admin rights | Run W-INST-1 as standard user, UAC never prompts | Starts; managed-kiosk features report `unsupported`/`unverified`, not silently "blocked" |
| W-INST-4 | Cold start time | Reboot, start app, stopwatch to "preflight ready" | Recorded (seconds); target agreed with A01 |
| W-INST-5 | Second instance | Start the app twice | Second instance refused or focuses the first; never two camera owners |
| W-INST-6 | Antivirus / SmartScreen | Defender on, first launch | Note prompts; no exclusion is required for normal mode |

## 2. Environment protection — each shortcut separately (item 3)

Run inside an **explicitly started** exam session (state `running`). For each row: press the shortcut 3 times,
observe the OS, then check the session's environment events/episodes in the teacher view.

| ID | Shortcut / action | Expected verdict to record | Notes |
|---|---|---|---|
| W-ENV-1 | Alt+Tab | blocked / detected_only | OS-level switcher is usually not blockable from a normal app — `detected_only` (focus loss) is an honest result |
| W-ENV-2 | Ctrl+C, Ctrl+X | blocked inside exam window | clipboard content must not change; verify with Notepad paste after exam |
| W-ENV-3 | Ctrl+V | blocked inside exam window | paste from outside text must not appear in answers |
| W-ENV-4 | Win (key alone), Win+D, Win+Tab, Win+R | record each | Start menu opening = not blocked |
| W-ENV-5 | PrtScn, Win+Shift+S, Alt+PrtScn, Win+PrtScn | record each | check `Pictures\Screenshots` and clipboard; Snipping Tool overlay = not blocked |
| W-ENV-6 | Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+PgUp/PgDn, Ctrl+N, Ctrl+T, Ctrl+W | blocked | single exam window, no new windows/tabs |
| W-ENV-7 | Alt+F4, Ctrl+Q | blocked or confirmed-exit | must not end the exam silently; finish is explicit |
| W-ENV-8 | Click a foreign window (Notepad) / taskbar | detected ≤ 1 s → `foreign_window_foreground`/`focus_lost` episode | logging focus loss alone does not satisfy PDF 2.3 "block foreign windows" — record honestly |
| W-ENV-9 | Open a browser via taskbar/Start during exam | blocked or detected with process basename only (no window title) | privacy allow-list |
| W-ENV-10 | Ctrl+Alt+Del, Win+L, UAC prompt | **N/A (by design)** — not blockable by an app | must be stated in the UI/report, not hidden |
| W-ENV-11 | Kill shell / backend during exam (Task Manager → End task) | keyboard, Win key, Alt+Tab, clipboard and focus work again immediately; no leftover hooks/kiosk; restart works | repeat for: kill Electron main, kill backend (python), kill both |
| W-ENV-12 | Emergency exit | documented exit works from every exam state; restrictions released | test first, before W-ENV-1 |
| W-ENV-13 | Pause by operator | restrictions released during pause, re-engaged on resume, pause visible as coverage gap | |
| W-ENV-14 | Display change (unplug/plug 2nd monitor) | `display_changed` event | |
| W-ENV-15 | Managed kiosk capabilities (Assigned Access / Shell Launcher / policies) | separate explicit status: `unsupported` / `verified on <build>` | never enabled on a developer machine; only in a dedicated VM with explicit consent |

Restrictions must exist only while the session is `running`: before start and after finish/abort/crash every
shortcut behaves normally again (re-run W-ENV-1…5 quickly after finish).

## 3. Shell security (item 4, desktop part)

| ID | Check | Expected |
|---|---|---|
| W-SEC-1 | Renderer has no Node: DevTools console `typeof require`, `process` | `undefined`; `contextIsolation` on, `sandbox` on |
| W-SEC-2 | `window.qorgau` exposes only the fixed methods of `contracts/ts/bridge.ts` | no generic invoke/fs/shell/exec channel; arguments validated in main |
| W-SEC-3 | Navigation to external URL (link in exam text, `window.open`, drag&drop URL, `location=`) | blocked (`navigation_blocked`/`new_window_blocked`), no external browser opened |
| W-SEC-4 | DevTools shortcuts (F12, Ctrl+Shift+I) in exam build | blocked (`devtools_blocked`) |
| W-SEC-5 | Token/port not visible to renderer | not in `window`, localStorage, URLs, renderer console |
| W-SEC-6 | XSS in teacher view / report | student label, review comment, exam text with `<img src=x onerror=alert(1)>` render as text; report.html has no external URLs/JS (CSP) |
| W-SEC-7 | Other local user/process calls the API | without token → 401; browser page on the same machine → 403 (Origin) / 401 |
| W-SEC-8 | Logs | `%LOCALAPPDATA%\QorgauExam` logs contain no token, no answers text, no frames by default |

## 4. Offline (item 8) — how it is really checked on Windows

Never change the user's global firewall or network adapters. Use one of:
1. **Windows Sandbox** (`.wsb` with `<Networking>Disable</Networking>`), bundle + prepared models mapped read-only;
2. a VM snapshot with the virtual NIC disconnected;
3. additionally (portable layer) run the backend under the QA network guard:
   `python -m qorgau_qa.netguard --log guard.jsonl -- -m proctor serve --token-stdin --port 0` (PYTHONPATH as in
   `qa/README.md`) and require an empty `guard.jsonl` after a full session.

| ID | Check | Expected |
|---|---|---|
| W-OFF-1 | Full session (preflight → calibration → exam → review → report) with networking disabled | works; models load from disk; report opens without network |
| W-OFF-2 | First run after preparation, no internet | no download attempt (guard log empty; Electron: no requests in `--enable-logging` netlog except loopback) |

## 5. LIVE end-to-end (requirement 4 of the PDF)

| ID | Scenario (one session, real camera, consenting team member — no real students) | Expected |
|---|---|---|
| W-E2E-1 | Preflight → calibration (5 targets) → exam → (a) phone shown and raised toward the screen, (b) long look down, look to the side, (c) leave the seat, (d) second person enters, (e) Alt+Tab, Ctrl+C, PrtScn → teacher reviews each episode → export → finish → restart | All three directions visible in **one** session timeline; episodes explainable; teacher decisions recorded; export works; restart clean |
| W-E2E-2 | Same with replay clip (labelled REPLAY everywhere) | Same pipeline, replay label on every record and in the report |

## 6. Performance on the demo laptop

See `qa/protocols/PERFORMANCE.md` (record hardware, OS, versions, resolution, capture/processed FPS, p50/p95
latency, memory over 30–60 min, cold start).
