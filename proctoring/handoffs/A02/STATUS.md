# A02 — STATUS (camera, frame stream, replay, runtime metrics)

Role: A02, the single owner of the camera/replay/synthetic frame source.
Branch: `claude/pensive-pasteur-wnjd2n`. Contract / baseline: `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (A01 BOOTSTRAP,
qorgau.v1 1.0.0, frozen; no newer A01 baseline received). Previous A02 checkpoint: `7769f13` (pushed).
Stage (2026-10-08): **checkpoint 4 — first LIVE run on the real Windows demo laptop + fixes for what broke there.**

## LIVE on the demo laptop (measured 2026-10-08, this machine only)
Machine: ASUS TUF Gaming A15 FA507NU, **AMD Ryzen 5 7535HS** (6C/12T), 15.2 GB RAM, Windows 11 Home 10.0.26200,
Python 3.12.14, OpenCV 4.13.0 (opencv-contrib-python 4.13.0.92), env from A01's `uv.lock` (`uv sync --frozen --extra cv
--extra dev`, lockfile unchanged). Camera: integrated "USB2.0 HD UVC WebCam" (USB VID_322E PID_2233), index 0 only.
Windows camera privacy: HKLM, HKCU and HKCU\NonPackaged all `Allow` (read-only check, nothing changed).

| Check (from `proctoring/`) | Result |
|---|---|
| `python -m proctor.capture verify-live --snapshot` (automatic part) | PASS on every automatic check; overall INCOMPLETE only because the 5 operator checks were not run (report: `%LOCALAPPDATA%\QorgauExam\a02-live\verify_live_auto.json`) |
| open | **DSHOW**, 1.45 s (incl. first-frame probe), actual 640×480, driver_fps 30.0, pixel format YUY2 |
| capture FPS, 30 s, 2 simulated consumers | **30.02 FPS** mean (per-second p50 30.0, p95 30.2), 0 frames dropped |
| latency (harness, read() return → callback end) | phone-like 8 FPS×60 ms: frame age p50/p95 15/46 ms, e2e p50/p95 78/109 ms; attention-like 15 FPS×25 ms: e2e p50/p95 47/75 ms |
| preview JPEG | 640×480, ~25 KB, ≥10 FPS under load |
| reopen ×3 | PASS, open + first frame 1.70 / 1.52 / 1.48 s, no leaked threads |
| DSHOW vs MSMF (raw OpenCV, scratch) | DSHOW open 0.2–1.0 s, first frame 110 ms, 30.0 FPS; MSMF open **5.0–6.0 s**, first frame 484 ms, 30.6 FPS → default order DSHOW→MSMF is right |
| **12-min LIVE soak** `soak --source live:0 --minutes 12` (report `%LOCALAPPDATA%\QorgauExam\a02-live\soak_live_12min.json`) | 720.0 s, 20 314 frames, **0 dropped**, mean **28.2 FPS** (per-second median 30.0, min 9.1: in 6 windows, ~100 s of 720 s in total, the camera itself switched to 15 FPS — UVC auto-exposure in dim light; health stayed `running`, consumers unaffected); phone-like 7.8 FPS e2e p50/p95/max 78/110/156 ms; attention-like 12.8 FPS e2e p50/p95 47/78 ms; RSS 156 → 160 MB, **growth after warm-up 3.6 MB**; threads 1 → 1, leaked runs 0; clean close (`stopped/closed`); all A02 targets PASS |
| camera busy (2nd process holds the camera via DSHOW or MSMF) | before fix: **open "succeeded", black frames at ~1 FPS** (bug); after fix: `CAMERA_BUSY/camera_no_frames` in 2.4 s, the other app keeps its 29.8 FPS; after it releases, the next open works |
| `pytest backend/proctor/capture/tests` (Windows) | **110 passed, 2 skipped** (skips: "a camera may be present", "symlink not permitted") |
| `pytest` (whole repo, Windows) | 172 passed, **2 failed** = A01 tests from R1 and R2 (R2 now confirmed on a camera machine) |
| `python -m proctor smoke` | 33/34: only `live_without_capture_module_is_not_ready` (R2: the camera now really works) |
| `contracts/tools/generate.py --check` / `verify_ownership.py --agent A02 --base 35bea4c` | PASS / PASS |

## Fixes from the real camera (each with a regression test that fails on the old code)
1. **DSHOW "busy" placeholders** (`sources.py`): with the camera held by another app DSHOW opens and returns
   ok=True + an all-black frame every ~1000 ms. The service reported `running` and analyzers would have received black
   frames (→ false `face_missing` and a wrong zone). Now a near-black frame (mean < 1) that took ≥ 0.5 s is a
   placeholder: at open → `CAMERA_BUSY` after 2 in a row, MSMF (5–6 s) is not tried; while running → dropped, health
   `camera_disconnected`, reconnect loop. Dim real frames (lens covered) and black frames that arrive on time are not
   affected. Tests: `test_dshow_busy_black_placeholder_is_busy_not_a_picture`, `test_busy_placeholder_while_running_is_a_lost_frame`,
   `test_slow_but_real_dark_frame_is_not_busy`.
2. **RSS was always `None` on Windows** (`measure.py`): ctypes default prototypes passed the pseudo-handle as 32 bit.
   Explicit argtypes/restype → memory is measured (needed for the long run). CPU model name is read from the registry
   instead of "AMD64 Family 25 …". Test: `test_rss_and_cpu_name_are_measured_on_supported_platforms`.
3. `SyntaxWarning: invalid escape sequence '\C'` printed at every start (docstring in `sources.py`) → raw docstring.
4. `record --countdown N` (default 3): camera opens first, then countdown, then `REC t = 0 s` and one line per second,
   so the person in the clip can follow a timed script (demo clips). Test: `test_record_countdown_starts_clip_after_countdown`.

## Full pipeline on REPLAY (scratch, NOT merged, nothing committed from other roles)
Scratch worktree = this branch + `proctor/phone` @ `c0275a2` (A03) + `proctor/attention` @ `4014444` (A04) +
`proctor/fusion` @ `8f763a1` (A05), unchanged; A01 `create_app` + HTTP API; storage = A01 `MemoryEvidenceStore` (A08
not overlaid), environment capabilities reported by the script (labelled `a02-scratch`). Models fetched with A03/A04's
own tools into `%LOCALAPPDATA%\QorgauExam\models` (sha256 match their manifests: yolo11n.onnx, face_landmarker.task).
Check clip without people (`record --source synthetic`, 8 s): preflight ready (phone_model `model_loaded`, face_model
ok), 242 frames, 0 dropped, phone 7.6 FPS, attention 12.8 FPS, 0 errors, e2e p95 93 ms → incidents `face_missing`
medium (correct: no face) + `monitoring_degraded` low at the end (replay ended before finish, R11/R7). This is an
interface/pipeline check, not an accuracy measurement. Script: scratch `run_replay_session.py` (zone computed by
zone-rule-1 inside the script only for verification; A05 owns `assess_session_zone`).

## Demo clips (task 3) — see `DEMO_CLIPS.md`
Commands and timed scripts for (а) green, (б) yellow, (в) red; storage outside Git: `%LOCALAPPDATA%\QorgauExam\replay`.
Scenario (б) adjusted: with A05 `a05-rules-1.0.0` short glances create nothing and a 5–6 s look down is ONE low → green
by zone-rule-1; the clip now has 3 low episodes (R10). Clips: **not recorded yet** (needs the captain + consent).

## NOT verified (honest)
* Operator checks: unmirrored view, disconnect/reconnect of the real device (integrated camera, cannot unplug; disabling
  in Device Manager is a settings change we do not make), privacy switch "Deny" → `CAMERA_DENIED`, busy by the Windows
  Camera app / Teams (only a second OpenCV process was tested). Command: `verify-live --interactive --snapshot`.
* Glass-to-glass latency (sensor → read()) is not observable from OpenCV; only read() → consumer is measured.
* The 12-min soak used SIMULATED consumers (cv2 blur load), not the real A03/A04 models. No CV accuracy is claimed.

## Interfaces provided (unchanged since checkpoint 3)
`proctor.capture.create_capture_service(settings) -> FrameCaptureService` implements `CaptureService` for `live`, `replay`,
`synthetic`; `FramePacket.image` uint8 HxWx3 BGR read-only, unmirrored; `frame_id` from 0, stable across reconnects, =
media index for replay; one worker per consumer with a size-1 mailbox; preview JPEG; ring for `get_frame`; health codes
(R5) and metrics (R6) in `DEPENDENCIES.txt`; open failures → `CaptureError` (`CAMERA_UNAVAILABLE`, `CAMERA_DENIED`,
`CAMERA_BUSY`, `REPLAY_INVALID`, `SESSION_ACTIVE`); replay format `qorgau.replay.v1` (`capture/replay.py`, `README.md`).
Tools: `python -m proctor.capture soak | verify-live | record | replay-check | replay-hash`.

## Changed paths (A02 only)
`proctoring/backend/proctor/capture/` and `proctoring/handoffs/A02/`. No shared file edited (requests: `DEPENDENCIES.txt`).

## После 9 октября
* Interactive LIVE checks with the operator (mirror, unplug/replug on a USB camera, privacy Deny, Teams/Camera app busy).
* R3/R4 (pacing per session, backend setting); MSMF-only machines: open budget vs 5–6 s MSMF open.
* 20–30 min LIVE soak with the real A03/A04 analyzers after A01 integration; glass-to-glass latency with a screen timer.
