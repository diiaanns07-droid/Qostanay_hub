# A06 controlled LIVE on integration 73d6b14

Captain authorized points 1–2 in chat: one monitor only; focus in enforce; max-minutes 2.
Tested production source: `73d6b14b0de7f080931ce4c084c374ca38f5783e`.
OS Windows 11 `10.0.26200`, Electron `43.7.5`.
The dedicated harness imports unchanged ExamGuard and NativeHelper from that integration commit.
This is a component LIVE, not a full camera/backend exam. No third-party process is closed.

## Point 1 — one display

2026-10-08 18:17 Asia/Qyzylorda: real `screen.getAllDisplays().length` returned **1**.
`withDisplayCheck` passes the one-screen condition. Evidence: `preflight/machine.json`, `preflight/events.jsonl`.
Remote snapshot still found `rustdesk.exe`, so combined environment gating correctly refused start
with «Закройте rustdesk.exe, чтобы начать». Enforcement was not engaged by this read-only run.

After the captain closed RustDesk, the 18:24 and 18:25 preflight snapshots both returned:
`display_count=1`, remote processes `[]`, `remoteSession=false`, `environment_block_reason=null`.
The combined display/remote condition passed and the LIVE guard engaged. This does not claim
that every camera/model/storage check of a full backend exam was run.

Second monitor is unavailable: multi-monitor LIVE remains **NOT TESTED**. To test later:

1. Connect a second display, choose an extended desktop, and repeat application preflight.
   Expect «Защита среды»: FAIL, «Отключите второй монитор, чтобы начать»; start must be refused.
2. Disconnect it and rerun preflight; the display condition should pass.
3. With one display, start a captain-approved test; attach another screen during RUNNING.
   Expect `display_changed` within the 2 s polling interval (or sooner via screen event).
4. Remove it: expect another transition, without duplicate event/poll reports. Pause/finish:
   no more monitor polling. No Windows setting changes should be made automatically.

## Point 2 — focus

**PASS on this laptop, with captain confirmation.** The captain explicitly requested the repeat and
then confirmed: Alt+Tab did not switch windows; after Ctrl+Alt+Del → Task Manager, focus returned
automatically to the test window; after emergency exit, normal typing and Alt+Tab worked again.

First run (`enforce/`), 18:24:21–18:24:36 Asia/Qyzylorda: enforce engaged, Win/Ctrl+C events observed,
emergency hotkey released at t=15357 ms. No Alt+Tab/Taskmgr in this first run; it is NOT evidence for
those cases. Captain confirmed normal input/focus was restored and requested another run.

Repeat (`enforce-repeat/`), 18:25:46–18:26:18 Asia/Qyzylorda:

| Observation | Evidence from events.jsonl |
| --- | --- |
| Native helper enforce + emergency shortcut ready | `engaged` t=615 ms, `native_helper_enforce=ok`, emergency registered |
| Alt+Tab blocked twice | `shortcut_alt_tab`, `enforcement=blocked`, t=10554 / 10911 ms; captain confirmed no switch |
| Task Manager became foreground | `foreign_window_foreground`, `process_name=Taskmgr.exe`, t=21577 ms |
| Focus lost | `focus_lost` t=21428 ms |
| Return attempt | `move_top_attempt` t=21838 ms, `focus_attempt` t=21839 ms (411 ms after loss) |
| Actual focus returned | `window_focus focused=true` + `focus_regained` t=21860 ms, **432 ms** after loss |
| Emergency exit | `released reason=emergency_hotkey`, t=31850 ms, `guard_active=false`, process exit 0 |
| Normal keyboard/window switching restored | captain answered «Да, всё так» to the complete acceptance question |

Another blur/regain in the repeat lasted 420 ms; the Taskmgr-specific pair is the 432 ms one above.
The actual Electron focus event plus captain observation provide evidence beyond merely calling focus().
No foreign process/window was closed or minimized, and no Windows setting was changed.

NativeHelper was configured with `maxMinutes: 2`, with a 120 s Electron release timer and a separate
130 s launcher watchdog for its own child only. Both runs ended via emergency exit before the limit;
automatic timeout behavior was **configured, not timed out in this LIVE**. Secure desktop itself is
outside enforcement scope; this test establishes return after opening Task Manager on this machine.
No general claim is made for different privileges, Windows builds, or a second display.

Production guard/native files remained byte-for-byte unchanged relative to integration
`73d6b14b0de7f080931ce4c084c374ca38f5783e`. Changes after that base are harness and evidence only.
Historical capability VERIFICATION.json was not automatically promoted or rewritten.
