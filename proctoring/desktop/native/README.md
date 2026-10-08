# Qorgau Exam — optional Windows environment-protection helper (A06)

This folder holds the **optional** native helper and the manual verification record used by the
Electron shell. The prototype runs **without** it; the helper only adds OS-level signals that the
window cannot see or stop by itself (Alt+Tab, Win, PrintScreen, foreign-window foreground).

> The shell never changes the registry, group policy, or any system setting, never installs a
> service or autostart, and never terminates other processes. The helper is a per-exam, user-level
> process that removes its own hook when the exam ends, when the shell dies, or after a time limit.
> Blocking is **off by default** (`--mode dry-run`); it only swallows keys with the explicit
> `--mode enforce` opt-in, which is for a controlled test. It cannot block Ctrl+Alt+Del or the UAC
> secure desktop — do not claim otherwise.

## Status (A06-native, 8 October 2026) — Python prototype; controlled helper LIVE passed

`qorgau_guard.py` is a Python 3.12 / ctypes prototype with no third-party dependencies or compiler.
`guard_win32.py` owns the Win32 resources; `guard_core.py` owns the bounded line protocol/lifecycle.
It is **unsigned**, not a production kiosk solution. After the captain's explicit approval, the real
helper passed dry-run/enforce on this Windows 11 laptop (`win32`, exact release `10.0.26200`, Python
3.12.14). The captain confirmed Win/Alt+Tab/PrtScn suppression, emergency exit and restored keyboard/focus.
`VERIFICATION.json` records those three shortcuts and observed foreground detection only. See the
[evidence and remaining gaps](../../handoffs/A06/checks/live-2026-10-08/RESULTS.md).
QA-WIN-001's standalone-helper test passes; full Electron integration still needs A01/A09 acceptance.
Alt+Esc, Ctrl+Esc, separate LWin/RWin and non-emergency cleanup paths have not been tested LIVE.
The shell now supports `.py` via `QORGAU_PYTHON` (same venv selection as the backend), and falls back to
`native/qorgau_guard.py` when the default exe is absent. Explicitly configured missing exes do not fall back.

The hook runs on a dedicated message thread. Its callback only updates modifier/target-key state,
decides pass/swallow, and enqueues an allow-listed event. A separate daemon writes stdout with `os.write`.
A full event queue or output failure disables suppression and ends the helper; output cannot delay unhook.
The parent is opened once with SYNCHRONIZE and checked through that retained handle. No TerminateProcess
right, PID polling, window-title reads, clipboard reads, ordinary-key logging, or persistent OS changes.

Ctrl+Alt+Shift+F12 is always passed through and also asks the helper itself to unhook/exit. Stop, EOF,
5-second heartbeat gap, parent exit, duration limit, console signals, hook errors and exceptions all
enter cleanup. Matching target-key releases are swallowed only if their presses were swallowed here.

Unit tests use fake hooks/Win32 functions; `--self-check` installs no hook. Neither proves real suppression.
Windows can silently remove a hook after a callback timeout; Python scheduling is not a real-time guarantee.
See [Microsoft LowLevelKeyboardProc](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc).

```powershell
# Safe automated checks (no actual hook installed):
<candidate-python> -m pytest desktop/native/tests -q -p no:cacheprovider
<candidate-python> desktop/native/qorgau_guard.py --self-check
```

LIVE execution requires the captain's explicit **yes in chat**, for dry-run as well as enforce.
Emergency exits: Ctrl+Alt+Shift+F12; close the helper console; or Ctrl+Alt+Del → Task Manager.
Enforce acceptance must use `--max-minutes 2`. Ctrl+Alt+Del and UAC are never claimed blocked.

### Requirements for any future helper (acceptance, unchanged from the task)

Default dry-run; enforcement only with the explicit controlled-test opt-in; only the agreed shortcuts;
nothing typed/clipboard/window titles transmitted or stored. It must release and exit on: `stop`, stdin
EOF, a missed heartbeat, the parent's death (checked through a process handle, not a PID number that
can be reused), `--max-minutes`, a hook failure and a normal exit; the cleanup must run on every exit
path; output must never block the input path (bounded queue, broken pipe tolerated); the shell's
emergency combination must keep working. No registry/policy changes, no services, no autostart, no
killing other processes. A `ready`/`selfcheck` line is never evidence of blocking — only a measured
`VERIFICATION.json` record on the exact OS build promotes a capability.

## Line protocol (one JSON object per line)

```
helper -> main (stdout):
  {"type":"selfcheck","version","os","elevated"}                         # answer to --self-check, then exit 0
  {"type":"ready","version","mode":"dry_run"|"enforce","hook":bool,"foreground_watch":bool}
  {"type":"key","key":"win"|"ctrl_esc"|"alt_tab"|"alt_esc"|"print_screen","swallowed":bool}
  {"type":"foreground","foreign":bool,"process":"<basename>"|null}       # basename only, allow-listed chars
  {"type":"error","code":"<snake_case>"}
  {"type":"bye","reason":"<snake_case>"}
main -> helper (stdin):
  "hb"      every 1 s        # heartbeat; a 5 s gap (parent gone) makes the helper unhook and exit
  "stop"                     # graceful stop; stdin EOF has the same effect
```

**Privacy:** the helper transmits only the fixed key names above and a process **basename**. Never
window titles, typed text, key sequences, or clipboard content. It is not a keylogger.

## Invocation (by the shell)

```
qorgau-guard.exe --self-check
qorgau-guard.exe --parent-pid <pid> --mode dry-run|enforce --max-minutes <n>
```

The shell starts it only while a session is RUNNING and stops it on pause/finish/abort/exit. The
helper must also exit on its own if: stdin closes, a heartbeat is missed for 5 s, the parent pid
disappears, or `--max-minutes` elapses. No restriction may survive a reboot.

## Config (shell environment)

| Variable | Meaning |
|---|---|
| `QORGAU_SHELL_NATIVE_HELPER` | helper `.exe` or `.py`; default exe, then `desktop/native/qorgau_guard.py` if exe absent |
| `QORGAU_PYTHON` | Python executable for `.py` (otherwise the backend root's `.venv/Scripts/python.exe`) |
| `QORGAU_SHELL_NATIVE_ENFORCE=1` | allow `--mode enforce` (swallow keys); **controlled test only**, default dry-run |

Interactive test instructions: [LIVE_CHECK_RU.md](LIVE_CHECK_RU.md). `live_console.py` supplies heartbeat,
caps each run at 2 minutes, writes protocol evidence locally and never edits `VERIFICATION.json`.

## Verifying OS-level items

`VERIFICATION.json` records manual controlled tests. The shell promotes an OS-level capability above
`unverified` only when a record's `platform` and `os_release` match the running machine exactly (and,
for a native `blocked` record, the helper is available and enforcing). Writing a record without a real
test on that machine is exactly what the acceptance criteria forbid — don't.

## Managed deployment (alternative to the helper, Windows Pro/Edu/Enterprise)

Stronger, OS-managed lockdown is possible **outside** this app and is a deployment choice for the
institution, not something the app applies:

- **Assigned Access / Shell Launcher (kiosk).** Run the exam app as the kiosk shell for a dedicated
  local exam account. Requires Windows Enterprise/Education (Shell Launcher) or Pro+ for single-app
  Assigned Access. Recovery: a separate admin account; Ctrl+Alt+Del still reaches the secure desktop.
- **Keyboard Filter (`EnhancedKeyboardFilter`/WDAC feature).** Blocks Win, Alt+Tab, Ctrl+Esc, etc. at
  the OS level. Enterprise/Education feature; enable per the Microsoft docs, scope to the exam account,
  and **remove it after the exam**. Ctrl+Alt+Del is deliberately not filterable.

These require the matching Windows edition and administrator setup by the institution, and an explicit
recovery/rollback plan. This repository does not enable them automatically; measure and record any
result in `VERIFICATION.json`.
