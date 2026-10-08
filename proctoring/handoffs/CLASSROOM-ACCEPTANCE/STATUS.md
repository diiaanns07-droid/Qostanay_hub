# Classroom acceptance and teacher workflow

Branch: `codex/classroom-acceptance`. User authorized autonomous integration and UI/UX improvements.
Separate worktree; active agents' branches and original files are not changed.

## Integrated snapshot

- Captain integration `2a6e87e` (native helper, latest T03/T05).
- T01 `8c94f3a` (actual C1 server).
- T04 `8f0b67c` (control UI).
- A07-student `ae41e1e` (student overlays).

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
