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

Second monitor is unavailable: multi-monitor LIVE remains **NOT TESTED**. To test later:

1. Connect a second display, choose an extended desktop, and repeat application preflight.
   Expect «Защита среды»: FAIL, «Отключите второй монитор, чтобы начать»; start must be refused.
2. Disconnect it and rerun preflight; the display condition should pass.
3. With one display, start a captain-approved test; attach another screen during RUNNING.
   Expect `display_changed` within the 2 s polling interval (or sooner via screen event).
4. Remove it: expect another transition, without duplicate event/poll reports. Pause/finish:
   no more monitor polling. No Windows setting changes should be made automatically.

## Point 2 — focus

PENDING: RustDesk closure / operator readiness. Enforce has not yet been launched in this check.
Harness uses NativeHelper `maxMinutes: 2` and a 120 s Electron release timer; the launcher has
an additional 130 s watchdog for only its own Electron child. Ctrl+Alt+Shift+F12 releases the test.
Record actual blur/foreign process/focus events and operator observations; focus() calls alone
are not proof that Windows returned focus. Secure desktop itself is outside enforcement scope.
