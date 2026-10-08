# Requirement matrix — case №3 PDF → implementation → test → limitation

Status: `missing` / `partial` / `implemented+not verified` / `implemented+verified (scope)`.
State: r2-candidate-1 (see CANDIDATE.json), Linux x86_64 container, no camera / no Windows / no real Electron.
"Verified" below never means measured CV accuracy: no consented recordings exist yet, so precision/recall is NOT EVALUATED.

| # | PDF item | Implementation (owner) | Evidence on the candidate | Limitation to state | Status |
|---|---|---|---|---|---|
| 2.1a | Phone in hand / in front of the screen | YOLO11n ONNX on CPU, `phone_visible` signal (A03) → rule `phone_visible` (A05) | real inference in REPLAY through the full pipeline; **false positive** on a public image without a phone (`cell phone` 0.26 on a hand gesture → medium episode) | detector ≠ proof of photographing; threshold/hard negatives need tuning on recordings | implemented+not verified |
| 2.1b | Moment the phone is raised | `phone_raised` temporal heuristic, frame-relative zone (A03) → rule (A05) | fired together with the FP above; no labelled raise clips | heuristic, zone geometry documented by A03 | implemented+not verified |
| 2.1c | Attempt to photograph / aim phone camera at screen | `possible_screen_capture` (A03) with `insufficient_evidence` when unobservable → caveat in phone episode (A05) | unit/golden tests only | camera side often unobservable; never "photo taken" | partial |
| 2.2a | Gaze direction | MediaPipe Tasks FaceLandmarker: head pose + approximate gaze, 5-point calibration (A04) | REPLAY of static public images: attention observations from the real module; calibration flow tested on synthetic | approximate, not eye tracking; static images cannot verify direction | implemented+not verified |
| 2.2b | Prolonged gaze down | rule `gaze_prolonged_down` (A05) | A05 golden fixtures | brief glances must not trigger (A05 tests); no real clips | implemented+not verified |
| 2.2c | Gaze left/right | rule `gaze_prolonged_side` (A05) | A05 golden fixtures | subject-centric directions | implemented+not verified |
| 2.2d | Person present in frame | `face_count`/`primary_face_present` (A04) → `face_missing` (A05) | REPLAY: 3 s empty segment did not reach the rule's duration in the 13 s run | missing frames = capture health, not absence | implemented+not verified |
| 2.2e | Second face appears | `face_count ≥ 2` (A04) → `multiple_faces` (A05) | **REPLAY of public image with two people → `multiple_faces` (high)** end-to-end | no identification; 1 public image ≠ accuracy | implemented+verified (wiring, 1 image) |
| 2.3a | Block Alt+Tab, Ctrl+C/V, Win, PrtScn | Electron shell: in-window blocking + capability matrix, optional Windows helper (A06) | shell tests 64/64, stub-Electron smoke 30/30 (Ctrl+V prevented in exam window) | OS-level keys **NOT RUN** on Windows; Ctrl+Alt+Del/UAC not blockable | partial — OPEN |
| 2.3b | Forbid switching tabs | single window, navigation/new-window/devtools blocked, Ctrl+Tab (A06) | stub-Electron smoke | real Electron NOT RUN | partial — OPEN |
| 2.3c | Block foreign windows/browsers | kiosk/top-most + focus loss → `environment_escape` (A06→A05) | stub tests only | focus-loss logging alone does NOT close this item | partial — OPEN |
| 3 | Local stack: YOLOv8n/11n, MediaPipe Face Mesh, Python/Electron | DECISIONS.md; weights prepared by explicit tools, runtime never downloads | models prepared + sha256-verified in the container; A09 offline checks | Face Mesh legacy API removed → Tasks FaceLandmarker; YOLO11n weights AGPL-3.0 | implemented+verified (Linux) |
| 4 | All three directions simultaneously in a working local prototype | one session: capture → phone + attention + environment events → fusion → store → UI | integrated REPLAY run with phone + attention analyzers and environment events in one session; synthetic flow to HTML/JSON report | Windows + camera + real Electron **NOT RUN** | partial |
| — | Explainable episodes + teacher decision + local report | A05 explanations, A08 review/report, A07 UI | integrated synthetic flow: review → HTML (escaped, no external URLs) + JSON | priority = review queue, not guilt | implemented+verified (synthetic) |
