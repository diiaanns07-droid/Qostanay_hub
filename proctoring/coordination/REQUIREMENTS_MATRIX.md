# Requirement matrix — case №3 PDF → implementation → test → limitation

Status: `missing` / `partial` / `implemented+not verified` / `implemented+verified`. Updated by A01 at each integration.
Source: `coordination/launch-prompts/CASE_SOURCE_KK.txt` (PDF, 2 pages). State at BOOTSTRAP (2026-10-08).

| # | PDF item | Contract / route | Owner | Planned test | Limitation to state | Status |
|---|---|---|---|---|---|---|
| 2.1a | Phone in hand / in front of the screen | `PhoneObservation.detections`, signal `phone_visible` → rule `phone_visible` | A03 → A05 | event-level precision/recall on recorded clips (denominators) | detector ≠ proof of photographing | missing (scripted synthetic only) |
| 2.1b | Moment the phone is raised | signal `phone_raised` → rule `phone_raised` | A03 → A05 | labelled raise clips + hard negatives | temporal heuristic, zone geometry documented | missing |
| 2.1c | Attempt to photograph / aim phone camera at screen | signal `possible_screen_capture` (`insufficient_evidence` when not observable) | A03 → A05 | clips where the back of the phone faces the screen | camera direction may be unobservable; never "photo taken" | missing |
| 2.2a | Gaze direction | `AttentionObservation.gaze` (+ `head_pose`, calibration) | A04 | calibration fixtures, mirrored checks, per-person split | approximate, not eye tracking | missing |
| 2.2b | Prolonged gaze down | rule `gaze_prolonged_down` | A04 → A05 | normal reading vs long down-look | brief glances must not trigger | missing |
| 2.2c | Gaze left/right (second monitor, notes) | rule `gaze_prolonged_side` | A04 → A05 | side-look clips | subject-centric directions | missing |
| 2.2d | Person present in frame | `face_count`, `primary_face_present` → rule `face_missing` | A04 → A05 | leave/return clips | missing frames = capture health, not absence | missing |
| 2.2e | Second face appears | `face_count ≥ 2` → rule `multiple_faces` | A04 → A05 | second person clips | no identification | missing |
| 2.3a | Block Alt+Tab, Ctrl+C/V, Win, PrtScn | `EnvironmentCapabilities`, `EnvironmentObservation` | A06 | per-shortcut PASS/FAIL/NOT RUN matrix on target Windows | Ctrl+Alt+Del/UAC not blockable; OS-level needs verified helper | missing |
| 2.3b | Forbid switching tabs | single window, no new windows/navigation, `shortcut_ctrl_tab` | A06 | navigation/new-window tests | | missing |
| 2.3c | Block foreign windows/browsers | `foreign_window_foreground`, `focus_lost` → rule `environment_escape` | A06 → A05 | foreign window tests | logging focus loss alone does NOT close this item | missing |
| 3 | Local stack: YOLOv8n/11n, MediaPipe Face Mesh, Python/Electron | DECISIONS.md | A01 | offline run without network | Face Mesh legacy API removed → Tasks FaceLandmarker | partial (pinned, imports verified on Linux) |
| 4 | All three directions simultaneously in a working local prototype | one session, three sources → fusion → UI | A01 | integrated E2E on Windows (A09), rehearsal (A10) | | partial: synthetic E2E wiring only |
| — | Explainable episodes + teacher decision + local report | `Incident.explanation`, `HumanReview`, report routes | A05/A08/A07 | golden fixtures, XSS/escape tests | priority ≠ guilt | partial (bootstrap synthetic review path) |
