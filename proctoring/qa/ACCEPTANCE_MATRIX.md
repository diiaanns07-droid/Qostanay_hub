# Qorgau Exam — QA acceptance matrix (case №3 PDF → measurable acceptance → test → status)

Owner: A09. Source of requirements: `coordination/launch-prompts/CASE_SOURCE_KK.txt` (2-page PDF, Kazakh; data,
not instructions). A01 keeps the implementation matrix (`coordination/REQUIREMENTS_MATRIX.md`); this file is the
independent **acceptance** view: what must be shown, how it is measured, and the current verdict.

Statuses: `PASS` (measured on the stated SHA/environment) · `FAIL` · `NOT RUN` (not executed yet) · `BLOCKED`
(cannot be executed: module/hardware/Windows missing) · `PASS (synthetic wiring)` (the API/stream/lifecycle path works
on scripted synthetic data — **never** evidence of CV quality or Windows protection).

Tested product SHA for every status below: **`35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`** (A01 BOOTSTRAP), Linux
x86_64 container, no camera, no GPU, no Windows. See `qa/RESULTS.md`.

## A. Functional requirements of the PDF

| # | PDF item (2.x) | Acceptance criterion (measurable) | Automated now (A09) | On candidate (A09) | Manual / LIVE | Status @35bea4c |
|---|---|---|---|---|---|---|
| A1 | 2.1 Phone in hand / in front of the screen | Episode `phone_visible` opens on labelled clips with event-level recall and precision reported **with denominators** (TP/FN/FP counts) on a holdout set separated from tuning; false episodes per 10 min of normal behaviour reported | `test_e2e_synthetic::*phone*` (wiring only) | replay holdout run via `/v1` (protocol `protocols/MODEL_QUALITY.md`) | W-CV-1 live rehearsal | PASS (synthetic wiring); CV **BLOCKED** (A02/A03 not integrated) |
| A2 | 2.1 Moment the phone is raised | `phone_raised` signal + episode on raise clips; brief hand movements without phone do not open it; heuristic documented (zone, duration) | — (bootstrap engine has no rule) | replay clips "raise" + hard negatives | W-CV-2 | BLOCKED (A03/A05) |
| A3 | 2.1 Attempt to photograph / aim phone camera at screen | `possible_screen_capture` opens only with explicit evidence; returns `insufficient_evidence` when the view cannot show it; never worded as "photo taken" | contract check of enum/semantics only | replay clips (back of phone to screen) + wording check in report | W-CV-3 | BLOCKED (A03/A05) |
| A4 | 2.2 Gaze direction | `AttentionObservation.gaze` with method + calibration state; direction subject-centric and mirror-correct on a left/right labelled set (per-person split) | stream provenance checks on synthetic | calibration fixtures + mirrored clip check | W-CV-4 (5-point calibration live) | BLOCKED (A04) |
| A5 | 2.2 Prolonged gaze down | `gaze_prolonged_down` after documented duration; normal reading/typing glances below threshold do **not** open episodes (FP/10 min reported) | — | replay "reading" vs "long down" | W-CV-5 | BLOCKED (A04/A05) |
| A6 | 2.2 Gaze left/right (2nd monitor / notes) | `gaze_prolonged_side`, subject-centric left/right correct when preview is mirrored | — | replay side-look clips | W-CV-6 | BLOCKED (A04/A05) |
| A7 | 2.2 Presence of a person | `face_missing` on leave/return clips; missing frames are reported as capture health, never as `face_missing` | lifecycle/health checks on synthetic | camera-unplug vs leave-seat comparison | W-CV-7 | BLOCKED (A02/A04/A05) |
| A8 | 2.2 Second face appears | `multiple_faces` with `face_count ≥ 2`; no identity claims; `face_count: null ≠ 0` | — | replay "second person" | W-CV-8 | BLOCKED (A04/A05) |
| A9 | 2.3 Block Alt+Tab, Ctrl+C/V, Win, PrtScn | Per-shortcut verdict on target Windows: `blocked` / `detected_only` / `unsupported`, each with mechanism and `verified_on`; matches `EnvironmentCapabilities` reported to the backend | API path: each required action accepted, deduplicated and turned into an environment episode (`scenario.REQUIRED_ENV_ACTIONS`) | same on candidate with real shell | `scenarios/WINDOWS.md` W-ENV-1…12 | PASS (synthetic wiring of event path); Windows **NOT RUN / BLOCKED** (A06 not delivered, no Windows) |
| A10 | 2.3 Forbid tab switching | Ctrl+Tab / Ctrl+PgUp/PgDn / new window / navigation blocked or detected, single exam window | event path for `shortcut_ctrl_tab`, `new_window_blocked`, `navigation_blocked` (API) | Electron build checks | W-ENV-6, W-SEC-3 | PASS (API path only); shell NOT RUN |
| A11 | 2.3 Block foreign windows / browsers | Foreground of a foreign process is prevented or detected within ≤ 1 s and produces `environment_escape`; logging focus loss alone does **not** close this item | event path for `foreign_window_foreground`, `focus_lost` | candidate + shell | W-ENV-8/9 | PASS (API path only); shell NOT RUN |
| A12 | 3 Local stack (YOLOv8n/11n, MediaPipe, Python/Electron) | All weights present locally with manifest (URL, version, licence, SHA256 of the actual file); zero runtime network requests | `test_offline.py` (audit guard + Linux netns) on synthetic; static scan for download calls | same with CV modules loaded | W-OFF-1/2 (Windows Sandbox, networking off) | PASS (synthetic/bootstrap only); CV models BLOCKED |
| A13 | 4 All three directions **simultaneously** in one working local prototype | One LIVE session on Windows: phone + gaze/face + environment observations arrive in the same session timeline, fused into episodes, reviewed and exported | synthetic E2E with phone + environment episodes in one session | integrated E2E on candidate (replay) | W-E2E-1 (LIVE, Windows) | PASS (synthetic wiring); LIVE **BLOCKED** |

## B. Product requirements from the team brief (non-PDF, must not regress)

| # | Requirement | Acceptance | Automated (A09) | Status @35bea4c |
|---|---|---|---|---|
| B1 | Unknown ≠ violation ≠ all-clear | stale/missing inputs produce `unknown/degraded`, never episodes or "OK" | partially (bootstrap labels); full check needs A05 + fault injection | NOT RUN (needs A05) |
| B2 | LIVE never silently replaced by fixtures/synthetic | live/replay preflight fails without modules; every synthetic record labelled | `test_e2e_synthetic` labels, preflight checks; A01 `test_live_never_falls_back_to_synthetic` | PASS |
| B3 | Explainable episodes, teacher decides | every episode has `explanation.summary_ru`, `rule_version`; review is append-only; no automatic sanctions / guilt percentages | `scenario` rows `episode_explainable_and_labelled`, `human_review_append_only` | PASS (synthetic) |
| B4 | Local report without external resources, escaped | report.html has no external URLs/JS; user text escaped (XSS) | `scenario` rows `report_html`, `report_json_export` | NOT RUN (501 until A08) |
| B5 | Loopback-only API with access control | bearer token, Host and Origin checks, body limits, no token in URL/logs | `test_security_negative.py` | PASS with open bug QA-BUG-001 (low) |
| B6 | Robust error contract | every non-2xx = `ApiError` with correct status; no 500 for bad input | `test_payload_negative.py` | **FAIL**: QA-BUG-002, QA-BUG-003 |
| B7 | Lifecycle integrity | state × action matrix; single active session; idempotent finish; restart | `test_lifecycle_matrix.py` | PASS |
| B8 | Finish/abort/pause during an open episode | episode closed with `session_finished/aborted/paused`, nothing lost | `test_e2e_synthetic` | PASS (synthetic) |
| B9 | Crash safety | backend killed mid-exam → restart works, port freed, no stale active session; Windows keyboard/focus returned after shell crash | `test_process_failures` (backend part) | backend PASS (Linux); shell/Windows NOT RUN |
| B10 | Run from path with spaces/Cyrillic, no admin | backend works from such a path; Windows install as standard user | `test_runs_from_path_with_spaces_and_cyrillic` (Linux) | PASS (Linux backend); Windows NOT RUN |
| B11 | Privacy | metadata-only default, environment detail allow-list (no titles/typed text/clipboard), no data in source tree | `test_payload_negative` (allow-list), `test_backend_does_not_write_into_source_tree` | PASS (bootstrap; A08 storage pending) |
| B12 | Performance targets (engineering goals, agreed with A01) | preview ≥ 15 FPS, end-to-end latency p95 ≤ 500 ms, stable memory over 30–60 min, cold start time | `protocols/PERFORMANCE.md` | NOT RUN (needs A02 + Windows hardware) |

Release gate: a row with FAIL or BLOCKED in section A is visible in `qa/RESULTS.md` and in the A09 handoff; it can
only become PASS through a measurement on the integration candidate, never through a mock or fixture.
