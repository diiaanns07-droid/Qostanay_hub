# ADAL-EXAM-WEB — website implementation ready for coordinator integration

Branch: `codex/adal-exam-web`; initial baseline `308b8ac`, coordinator baseline `fc8a3a8` merged after checkpoint `ab3ea97`. Parent coordinates integration; no push requested or performed. Final SHA reported to coordinator after commit.
Scope: website URL mode in the Windows Electron student app. Never an OS-wide browser/app/firewall claim.

Implemented: strict URL policy; isolated ephemeral exam Session/WebContentsView without preload; request/navigation/redirect/history-API checks; deny downloads/popups/permissions; immediate native-view hiding and request gate closure on lock/pause/end/backend loss, followed by asynchronous Chromium destruction; private desktop viewport/status bridge helper. Main/preload/renderer are wired. URL exam has a dedicated screen with reload/finish/emergency exit and visible origin. Unsupported application mode gets an explicit message. Primary app Session permissions/network/navigation restrictions are unchanged.

Policy: exact HTTP(S) origin + path; terminal `/*` subtree only; no host wildcard, credentials, ambiguous encoded separators. First allowlisted URL is landing URL. Every auth/CDN/resource endpoint must be listed explicitly. Exact pattern query constrains query; absent query allows query parameters. Pop-up authentication is deliberately unsupported; same-window authentication supported. Native application mode reports unsupported.

## Validation (2026-10-08, Windows)

- `npm run typecheck`: contracts/main/renderer PASS.
- `npm run build`: Electron + production renderer PASS.
- `node main/tests/run.mjs exam-policy web guard state`: 32 PASS (4 new URL/geometry tests, 28 existing security/guard/state tests). Guard errors in output are expected injected test failures, not physical guard activation.
- `node main/tests/exam-surface.mjs`: 13 actual Electron/Chromium scenarios PASS: approved page/scripts, no preload or qorgau bridges, separate session, wrong host/path fetch/resources/iframe/navigation blocked before server receipt, popup denied, forbidden protocols, same-window fixture auth redirect, forbidden redirect, cancelled download, camera/microphone/geolocation denied without capture, worker blocked, history API outside path closes view, lock/unlock lifecycle, trusted strips/HTML dialog detachment, stream reconnect requires fresh class state, pause/disconnect/finish cleanup and cookie deletion.
- The real Electron fixture imports production ExamSurface and injects synthetic shell/class state. Hidden window, HTTP servers on ephemeral 127.0.0.1 ports; no Python backend, camera/microphone capture, keyboard hooks, kiosk or globalShortcut registration. This proves the view/policy boundary, not the entire C1→C2→Electron chain.
- Dependencies copied read-only from coordinator's existing installation into this worktree. Sandbox ancestor access prevented esbuild initially; bounded local tests/build ran with approved escalation. No production network test performed.

## Coordinator wiring and next checks

Merge this branch/commits including renderer component and preload helper. Main currently forwards class_state to examSurface before renderer push; stream close sets `backend-stream` blocker. Keep this callback when combining audio's onClose callback.

Lock agent integration: instantiate ClassLockController with `setExamBlocked: blocked => examSurface.setBlocked('class-lock', blocked)`. Consume lock requests BEFORE examSurface.consumeClassState and renderer push. Effective `class_state.locked` independently blocks under `class-state-lock`, so a pending unlock cannot show the site early. Lock ACK proves synchronous hiding; Chromium's `isDestroyed()` becomes true asynchronously.

Audio integration: install audio permission helper ONLY on primary Session. Exam Session always denies all media/device/display permissions. Native view clamps y >= 112 DIP, reserves bottom 64 DIP and removes itself for any shell dialog; audio's fixed 72px trusted banner remains uncovered.

Root should run integrated C1→C2→Electron command flow and visible renderer layout after merging lock/audio helpers. No production exam vendor or SSO browser matrix tested; popup-based sign-in is intentionally unsupported. Pause/lock/disconnect destroys remote document (cookies retained until finish), so unsubmitted in-page edits may be lost; students must use the exam site's save/submit controls. Remote answer storage is owned by that site.

HTTP(S)/WebSocket policy enforcement is scoped to this dedicated Chromium surface. It is not an OS-wide network firewall, Windows application allowlist, or proof against every Chromium transport; WebRTC uses disable_non_proxied_udp and external pages receive no media permissions. No claim of physical Windows lockdown is made.

Sources consulted for Electron API behavior: https://www.electronjs.org/docs/latest/api/web-contents-view and https://www.electronjs.org/docs/latest/api/web-request; installed Electron 43.7.5 typings. Actual runtime checks above take precedence over assumed synchronous destruction.
