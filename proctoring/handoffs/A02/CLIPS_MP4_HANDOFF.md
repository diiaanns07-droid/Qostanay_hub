# A02 H.264 MP4 evidence clips — captain handoff

Base: `7d9852457788420a7d1ff7c2d3580256935e4e8a` (`7d98524`), verified as an ancestor
of remote `codex/proctor-integration`. Feature: `codex/proctor-clips-mp4`.
Work was done in a separate clean clone inside PythonProject99's ignored `tmp/`;
the standalone Privacy-First Offline AI checkout and its branch were left untouched.

## Scope and changed files

* `proctoring/backend/proctor/capture/clips.py`: prefer H.264 MP4, retain AVI fallback.
* `proctoring/backend/proctor/capture/tests/test_capture_clips.py`: keep the existing
  AVI/size regressions explicit; adapt their private baseline to the strict 8 MiB cap.
* `proctoring/backend/proctor/capture/tests/test_capture_mp4.py`: focused media,
  context, failure, size, offline, watchdog and authorized T03 endpoint checks.
* `proctoring/backend/proctor/uplink/client.py`: actual-extension MIME selection only.
* `proctoring/backend/proctor/uplink/tests/test_uplink_app.py`: permit both existing
  contract media types in the real-backend integration test.
* `proctoring/backend/proctor/uplink/tests/test_clip_mime.py`: assert exact MIME for each format.
* `proctoring/handoffs/A02/DEPENDENCIES_MP4.txt`: dependency request to A01.
* This new handoff document. Existing teammate handoffs were not edited.

The three C2 paths were explicitly authorized by the human user after the hardcoded
AVI MIME blocker was reported. All other changes are within A02's owned paths.
No student app redesign, API/auth/event/database/session/dashboard changes, dependency
manifest/lockfile edits, branch merges, history rewrites or real recordings.

## Encoding and failure behavior

The existing rolling JPEG buffer and export worker remain. Decode and feed one frame
at a time into a local FFmpeg subprocess resolved by `imageio-ffmpeg==0.6.0`.
No raw-frame list or long-held capture lock is introduced.

Parameters: MP4, `libx264`, `main` profile, `yuv420p`, `veryfast`, CRF 26, two encoder
threads, no audio, `+faststart`. Even dimensions and the existing aspect-preserving
640x360 maximum are retained. CRF can be configured with `QORGAU_CLIP_H264_CRF`
(clamped 18–40); `QORGAU_CLIP_H264_PRESET` accepts ultrafast/superfast/veryfast/faster/
fast/medium/slow. Invalid settings are logged and use AVI, never an internet download.

Internal helpers: `_write_mp4`, `_check_video` (reuses A02's existing replay file
decoder), `_thin_frames` (keeps both timeline endpoints in the AVI half-FPS retry).
Public signatures and every `ClipResult` field remain unchanged. MP4 explicitly returns
`content_type="video/mp4"`; AVI keeps `video/x-msvideo`. `export_clip` still returns `Path`.

Write to `.part.mp4`, wait for FFmpeg exit 0, validate finalized file/frame count/readability
and final size, then atomically rename to `.mp4`. A 30-second watchdog bounds one encoder
attempt, including blocked stdin writes and finalization; exceptional exits reap the child.
On missing package/binary, startup/write/finalization/decode failure, log the reason,
remove the partial MP4 and retry the SAME buffered JPEGs through the existing MJPG AVI
writer. The AVI path really ends in `.avi`; no Chrome playback claim is made for AVI.
Failed temporary files are removed. No successful path is returned on terminal failure.

## Size and recording window

Successful artifacts are at most **8,388,608 bytes (8 MiB)**, even if a caller supplies
a larger limit. A smaller positive caller limit is respected. There is no FFmpeg `-fs`
or time cutoff that would silently remove required context.

MP4 tries at most three encodes: configured CRF/full size, CRF+6/full size, CRF+10/half
size. Every MP4 attempt retains all frames, order and first/last timestamps. VBV rate
is budgeted from the complete clip duration with container headroom; finalized size is
still authoritative. If MP4 cannot fit, use the existing finite AVI quality/half-FPS/
half-size ladder. Half-FPS retains first AND last frames and recomputes average FPS.
If neither format fits, raise existing `ClipError("too_large")`; no footage is advertised.

The established episode semantics are unchanged: **5 seconds before and 5 seconds after
the episode START**, not after the end of a long event. Default ring is 10 seconds,
24 MiB JPEG memory cap, 600 frames; export is started at incident open from C2's worker.
Requests whose before+after exceed the ring still raise `invalid_argument`. Missing
capture context is still reported by `partial=True`. A late request cannot recover
frames already evicted from the ring. This patch does not invent longer episode recording.

As before, video uses constant average FPS; irregular capture timestamps are represented
approximately, not as a new variable-PTS contract. Container duration includes the final
frame interval (151 frames at 15 FPS = 10.066667 s for timestamps 0..10000 ms).

## Offline installation and A01 action

See `DEPENDENCIES_MP4.txt` for exact wheelhouse/install/encoder-preflight commands.
A01 must update the owned dependency manifests/lockfile and Windows package before
deployment; this branch intentionally does not do that. Until provisioned, AVI still
works, but it is not a reliable Chrome playback format.

## Verification

Focused suite: **27 passed**, one installed Starlette/httpx deprecation warning,
zero failures/skips (36.15 seconds). It covers actual H.264/yuv420p/MP4 decoding,
5+5 second context, startup/write/finalization failure, AVI extension, size/retries,
clean partials, offline guards, stalled encoder termination, signatures, correct C2 MIME,
and existing T03 upload/auth/Range response.

Final capture + C2 regression suite: **173 passed, 1 skipped, 1 warning**, zero
failures/errors, 227.06 seconds. Skip: the existing no-physical-camera expectation
cannot be asserted on a machine where a camera may be present. T03's unchanged
regression suite: **72 passed, 1 skipped, 1 warning**, zero failures/errors, 33.78 seconds.
Its Node/Playwright/Chromium harness was unavailable; actual in-app browser playback
was verified separately below. Each warning is the installed Starlette/httpx deprecation.
Compilation and Git whitespace checks passed.
The seeded high-entropy 10-second MP4 regression produced **3,093,843 bytes**,
below 8 MiB, retaining every frame in the requested window.

Initial broad run before installing the existing websockets test dependency:
152 passed / 1 skipped / 4 failed / 17 setup errors. C2 could not start its test server
without `websockets`; A02's static camera boundary guard also found a new direct file
VideoCapture call. That call was replaced with reuse of the existing replay file decoder.
No unrelated test or production module was changed to hide those failures.

Browser: generated synthetic-only **72,504-byte H.264 MP4**, 151 frames, 640x360,
first=0 ms, last=10000 ms, FPS=15, duration=10.066667 s. Served through T03's unchanged
authenticated `/api/teacher/clips/inc-synthetic` endpoint and actual review UI. In-app
browser reached the final frame without media error and successfully sought to 6.140826 s.
This is **not** a Google Chrome result. Google Chrome was unavailable to the browser
automation API: **actual Chrome playback NOT VERIFIED**. FFmpeg decode and codec/pixel
format checks passed. Browser proof and all synthetic recordings are outside the commit.

Supported-environment limitation: checks ran on Windows/Python 3.13.3 with OpenCV 4.14.0,
not A01's declared Python 3.12/OpenCV-contrib 4.13.0.92. No physical student footage or LAN
classroom run was used. Do not interpret these tests as phone-detection accuracy testing.

### Exact local test commands used

```powershell
Set-Location 'C:\Users\user\PycharmProjects\PythonProject99\tmp\adal-clips-mp4\proctoring'
$env:TEMP = 'C:\Users\user\PycharmProjects\PythonProject99\tmp\clips-runtime-temp'
$env:TMP = $env:TEMP
$env:PYTHONPATH = 'C:\Users\user\PycharmProjects\PythonProject99\tmp\clips-test-deps'
& 'C:\Users\user\PycharmProjects\PythonProject99\.venv\Scripts\python.exe' -c "import cv2,pytest; cv2.setNumThreads(2); raise SystemExit(pytest.main(['backend/proctor/capture/tests','backend/proctor/uplink/tests','-q','--basetemp=C:/Users/user/PycharmProjects/PythonProject99/tmp/clips-tests-07','--junitxml=C:/Users/user/PycharmProjects/PythonProject99/tmp/clips-tests-07.xml']))"
```

For another checkout, use A01's prepared Python 3.12 environment and run the same two
test directories from `proctoring/`; choose a NEW basetemp directory on each run.

### Manual Google Chrome verification on Windows

In A01's prepared environment, from `proctoring/`:

```powershell
New-Item -ItemType Directory -Force .\data | Out-Null
& .\.venv\Scripts\python.exe -m pytest backend/proctor/capture/tests/test_capture_mp4.py -k test_mp4_is_h264_yuv420p_decodable_and_preserves_5s_context -q --basetemp=.\data\mp4-chrome-qa-01
Get-ChildItem .\data\mp4-chrome-qa-01 -Recurse -Filter *.mp4 | Select-Object FullName,Length
```

Use a fresh `mp4-chrome-qa-XX` directory for another run. Open Google Chrome, press
Ctrl+O, and select the displayed synthetic `.mp4`. Play past the first frame and seek
to ~6 seconds; confirm moving frames and no playback error. For the full class flow,
use the existing Start-Teacher/Start-Student launchers in A01's Python 3.12 environment,
request an A02 clip in the authenticated teacher dashboard, confirm MP4/H.264 metadata,
play and seek there. AVI fallback should be labeled/downloaded as AVI, not promised playable.

## Integration for the captain

Review the feature diff against **7d98524**, not today's moving integration HEAD.
The branch intentionally starts at the exact requested commit. Integration is through
team review; this task performs no merge. C2 now sends the actual MIME; T03 already
accepts/parses MP4 and AVI and provides authenticated byte ranges, so no T03 patch is needed.
While this task ran, integration advanced to `2799163` and already added an equivalent
C2 MIME correction (`c3a3f2d`). The required base remains unchanged; the captain should
retain the current MIME fix when integrating and avoid duplicating that change. No merge
or rebase onto the moving branch was performed here.
The standard A02 ownership checker reports the three human-approved C2 exceptions;
the remaining paths are A02-owned. Shared dependencies remain A01's responsibility.
