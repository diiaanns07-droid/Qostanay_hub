# ADAL-EMERGENCY-EXIT

Branch: `codex/adal-app-shell`. Only emergency-exit code was implemented; the application allowlist feasibility review produced no product changes.

Ctrl+Alt+Shift+F12 now exits Adal. Before waiting for any backend request or queued transition, main permanently prevents guard re-engagement, latches the current session, releases restrictions, resets class lock/audio/exam content and closes streams. Abort has a 1500 ms HTTP timeout; cleanup receives at most 2 seconds before graceful quit. A deadline at 6 seconds forces Electron exit if the normal shutdown path stalls. Repeated hotkeys/quit events share one shutdown path. A native helper completing its start after release is stopped again. In-app panic IPC retains its existing release/recovery-screen behavior, with synchronous guard release added.

Enforce now refuses an emergency shortcut whose registration fails, throws or cannot be verified. Partial restrictions are released before any native helper starts. Dry-run continues to report the unavailable shortcut as failed.

Validation: 33 focused emergency/guard/state tests PASS; all three TypeScript configs PASS; production Electron and renderer build PASS. Tests use fake clocks/windows/platform/native helpers, including hung cleanup, failed diagnostics, late native startup and attempted re-engagement. No physical hooks, camera, microphone or app lockdown was activated.

The main-thread deadline cannot run when Electron's event loop is frozen. The separately owned launcher watchdog must provide that independent fallback and must not compete for the Electron global shortcut registration. Root handles integration and push; this agent has not pushed.
