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

Checked: strict JS TypeScript check PASS; existing 23 panel unit tests PASS.
Browser visual and actual server interaction checks are next, not yet claimed.

## Independent acceptance work

`codex/classroom-chain-check`: real C1 + two full synthetic backends (no webcam).
`codex/classroom-windows-launch`: launch/check-only scripts.
`codex/classroom-client-recovery`: stale resume and initial class_state replay fixes.

## Known boundaries

T01 feature adapters for T03/T04/T05 are still being developed externally. Source merge alone is not mounting.
No claim of 100 real cameras, working WebRTC in Electron, or complete external-site confinement.
Current C2 still acknowledges lock/mic flags before actual renderer/device confirmation.
Synthetic backend provenance on the class wire is not yet correctly represented by the original implementation.
