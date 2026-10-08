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

## Status (round 2) — helper NOT delivered

The helper's **source and build are not in this repository** (round 2 did not produce them; see
`handoffs/A06/STATUS.md`). `desktop/native/bin/qorgau-guard.exe` is absent, so the shell reports all
OS-level items as `unverified` (Windows) / `unsupported` (other OS) and never claims them blocked. What
exists and is tested on every OS: the shell-side controller, protocol parser and lifecycle in
`main/src/environment/native.ts`, exercised against `main/tests/fake-helper.mjs` (a protocol-only fake
that hooks nothing). The exam runs without the helper; OS-level shortcuts are then only detected
in-window (focus loss) and the gap is visible in the capability matrix.

Until a reviewed helper exists, the supported way to get OS-level lockdown on the demo machine is the
**managed deployment** below (institution-administered, reversible), not code in this app.

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
| `QORGAU_SHELL_NATIVE_HELPER` | path to the helper exe (default `desktop/native/bin/qorgau-guard.exe`) |
| `QORGAU_SHELL_NATIVE_ENFORCE=1` | allow `--mode enforce` (swallow keys); **controlled test only**, default dry-run |

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
