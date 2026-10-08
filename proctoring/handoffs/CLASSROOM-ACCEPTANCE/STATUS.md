# Classroom acceptance and teacher workflow

Branch: `codex/classroom-acceptance`. User authorized autonomous integration and UI/UX improvements.
Separate worktree; active agents' branches and original files are not changed.

## Integrated snapshot

- Captain integration `2a6e87e` (native helper, latest T03/T05).
- T01 `8c94f3a` (actual C1 server).
- T04 `8f0b67c` (control UI).
- A07-student `ae41e1e` (student overlays).
- Latest captain `f46565c`: optional upward calibration target for laptop webcams; **19 calibration tests
  PASS** on the integrated tree. Final desktop build/typecheck PASS after this merge.

## Teacher panel checkpoint

Changed only `proctoring/class-panel/` and this handoff:
- Session creation and join code through C1's actual session API, not curl.
- Existing class replacement requires explicit acknowledgement; this does not finish external exams.
- Site policy is labelled configuration, not proof of enforcement.
- View options moved behind a disclosure. Main surface focuses on cards and attention queue.
- Removed developer-facing protocol text and absent feature placeholders.
- Student detail keeps connection diagnostics collapsed; background is inert during the dialog.
- More restrained typography, spacing and status colours; source/demo labels retained.
- Panel meta CSP permits same-origin media (C1's HTTP CSP must be updated by its owner when mounting video).

Checked: strict JS TypeScript check PASS; 25 panel unit tests PASS, including source/frame provenance.
Actual C1 + Chrome session UI: 39/39 PASS (Windows, local loopback). Screenshots inspected separately.
Panel data flow/keyboard/load checks functionally pass; the first browser run also detected local antivirus
CSP injection and a missing favicon. The favicon is fixed; the test now reports verified antivirus
injection separately, without ignoring application errors. Full rerun pending.

Second checkpoint: source labels for each student/frame/event; absent source means unknown,
earlier synthetic frames keep their label after a switch to live. DEMO explicitly supplies simulated.
Localized invalid-address and network errors. Login link on expired teacher authentication.
Desktop npm ci/build/typecheck PASS. Shell test exposed one old synthetic incident count mismatch;
assigned to recovery agent for investigation, not counted as passing yet.

## Independent acceptance work

`codex/classroom-chain-check`: real C1 + two full synthetic backends (no webcam).
`codex/classroom-windows-launch`: launch/check-only scripts.
`codex/classroom-client-recovery`: stale resume and initial class_state replay fixes.

## Known boundaries

T01 feature adapters for T03/T04/T05 are still being developed externally. Source merge alone is not mounting.
No claim of 100 real cameras, working WebRTC in Electron, or complete external-site confinement.
Current C2 still acknowledges lock/mic flags before actual renderer/device confirmation.
Synthetic backend provenance was corrected by c3c1329; initial/legacy unknown sources remain unknown.

## Final acceptance on Windows (2026-10-08)

- Actual C1 + two complete `proctor serve` / C2 processes: **15/15 PASS** after provenance fix.
  Sanitized evidence: `proctoring/acceptance/classroom/chain/windows-integration-result.json`.
  Includes targeted commands, distinct student event histories, previews, C1 crash/restart, student restart,
  persistence and bounded cleanup. Synthetic input; loopback network.
- Teacher panel: **61/61 browser checks PASS**, 2/30/100 synthetic cards, focus, filters, reconnect,
  layout, reduced motion, mock-adapter errors. 100-card render p95 = 2.4 ms on this machine.
  10 CSP console warnings attributed to observed Kaspersky DOM injection were counted separately.
- Real C1 + Chrome teacher form: **39/39 PASS** (no mock API), 390/1366 widths.
  Screenshot masking now replaces DOM text temporarily, since the strict CSP blocked injected CSS.
- Panel unit tests: **25 PASS**; strict JS checking PASS.
- C1/class contracts/C2 tests: **150 PASS** (source-owner run); generated schemas/42 fixtures/TS PASS.
- Windows launchers: **9/9** on PowerShell 5.1 and **9/9** on PowerShell 7 (source-owner runs).
- Desktop build/typecheck PASS. Full shell suite: **80 PASS, 1 platform-specific SKIP**, no failures.
- Student preparation UX: two fixture viewport runs, 11 checks each, PASS; these are not real CV tests.
- Actual Electron + actual backend startup PASS. No session/camera/microphone started.
  Real startup exposed a stale preflight error: health requests now wait for readiness, rerun on state
  changes, and ignore late results from the previous effect. Preflight reloads after recovery.
  Portable regression: `desktop/renderer/tests/electron-startup.mjs` (build first; set QORGAU_PYTHON
  and PLAYWRIGHT_MODULE where needed). It checks visible connection/diagnostics, not only process state.

## Publishing and next step

Agent ASTRA-CHAIN's individual push was rejected due to the unrelated GOV_DIPLOME instructions in its
limited context. Its saved commits were integrated locally. Root revalidated the exact origin against
the human's explicit Qostanay_hub repository request; the approval review accepted root's integration
push. Therefore the earlier blocked agent branch is not the delivery: use `codex/classroom-acceptance`.

This is a verified integration checkpoint, not a Windows installer or a proven 100-camera deployment.
Next milestone: T01 mounts the supplied history/control/audio feature adapters; validate clips, explicit
student audio indication and effective lock acknowledgements across the user's three physical PCs.
The main teaching flow should then expose only capabilities those adapters actually confirm.

Interactive teacher UI preview is running locally at `http://127.0.0.1:18790/?students=30` with the explicit
DEMO adapter (synthetic cards). It is a visual preview, not the live class server or a camera test.
The real startup regression ran separately against the built Electron and the actual current backend.

## Adal checkpoint — 2026-10-08, second integration pass

User confirmed the public name Adal. Visible student/launcher branding has been integrated from
771ee10; internal qorgau bridge/protocol/env/data identifiers intentionally retain compatibility.
Root merged latest A07 cb424a6 and contracts 6263aef in fc8a3a8, preserving the simplified preparation
screen and health readiness fixes. Desktop typecheck and full build passed after both merges.
The bounded C2 fix ad01b78 is integrated: audio_start/audio_update return not_supported until a real
media endpoint exists. This prevents the old boolean ACK from falsely claiming microphone capture.
Agent tested 7 regressions and 32 related uplink/server tests, all passed.

Teacher panel now has a single class heading, compact priority filters with an All action, image-led
tiles, shorter status labels, and a row-based review queue. The previous layered main-workspace CSS
was replaced. Source labels and unknown/stale states remain explicit. Panel strict check and 25 unit
tests passed; Chrome e2e 61/61 passed after layout changes (100 synthetic cards p95 2.8ms, not 100 cameras).
The later dark synthetic SVG preview was visually inspected in Chrome at 1366x900; source marking stays
visible. The real C1 session form passed 39/39 at 390/1366 widths after the redesign. Contract generation
check and 48 Python contract tests passed. Latest A07 renderer unit tests: 7/7; fixture class UI: 46/46.
Teacher login now displays Adal with a masked PIN input; existing panel-serving/auth checks: 3/3.
HTML presentation prompt: coordination/launch-prompts/ADAL_HTML_PRESENTATION.txt.

Verified gaps before the next wave: C1 sends exam policy as metadata but the student does not yet
enforce it; Electron overlay is not OS-wide PC lockdown; old C2 lock ACK preceded the visible overlay;
T05 media signaling still needs the C1/C2/Electron bridge. LAN student connections are supported by
Start-Teacher -Lan; teacher browser remains restricted to the server's local machine. Three physical
PCs/Ethernet/Wi-Fi and real media have not been tested.

User requested ten Astra workers; environment permits four active agents including root. Work is
split into ten bounded tasks, with three Astra-ultra workers at a time and root integration:
1. Exam website surface/allowlist (astra_exam_web, active, isolated Adal-exam-web).
2. Effective lock acknowledgement (astra_lock_ack, active, isolated Adal-lock-ack).
3. Transparent audio bridge (astra_audio_bridge, active, isolated Adal-audio-bridge).
4. Teacher commands/policy UI (queued behind 1/2).
5. Teacher audio UI (queued behind 3).
6. Clip/history feature adapter (queued, independent module).
7. LAN launch/pilot diagnostics (queued, no claimed physical pilot).
8. UX/browser acceptance of integrated controls (queued).
9. Cross-process negative/recovery regression (queued).
10. Root integration, public Adal branding, visual review, and presentation prompt (in progress).

All worker changes stay in isolated branches; root publishes the integrated checkpoint. No native
Windows hooks or real cameras/microphones are to be activated by automated acceptance on this machine.
