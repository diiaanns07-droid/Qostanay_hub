# proctor.capture — camera, frame stream, runtime metrics (owner: A02)

The single source of frames for the whole product. Only this package opens `cv2.VideoCapture` on a device.
Phone (A03) and attention (A04) get `FramePacket`s through `CaptureService.add_consumer` (A01 registers them);
the renderer (A07) gets the preview over `/v1/preview` and never opens a camera.

```python
from proctor.capture import create_capture_service      # factory used by proctor.app.ModuleRegistry
capture = create_capture_service(settings)              # -> proctor_contracts.interfaces.CaptureService
```

| File | What |
|---|---|
| `service.py` | `FrameCaptureService`: state machine, capture thread, watchdog, reconnect, ring, preview, health, metrics |
| `fanout.py` | one worker per consumer, size-1 latest-frame mailbox, rate limit, lockstep delivery |
| `sources.py` | `CameraSource` (live), `SyntheticSource` (tests), BGR normalization |
| `replay.py` | `qorgau.replay.v1` manifest + `ReplaySource` (realtime / lockstep) |
| `stats.py` | rolling windows, percentiles (measured only) |
| `measure.py`, `verify_live.py`, `record.py`, `__main__.py` | tools: `python -m proctor.capture …` |
| `replay.schema.json` | JSON Schema of the replay manifest (generated from `ReplayManifest`) |

## Rules for consumers (A03/A04)

* `frame.image`: uint8 H×W×3 **BGR**, C-contiguous, **read-only**, shared with every other consumer and the ring.
  Never write to it; anything you need to modify, copy first. `cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB)`
  returns a new array (e.g. for MediaPipe `mp.Image`).
* Frames are **unmirrored** (`meta.mirrored == False`). Normalized coordinates are relative to this frame.
* Your callback runs on its own thread, never concurrently with itself. If you are slower than capture you get the
  newest frame when you become free (older ones are `frames_skipped`), so return observations for
  `frame.frame_id` / `frame.t_session_ms` of the packet you were given.
* Exceptions are counted in `ConsumerMetrics.errors`; capture continues. Do not block forever: `close()` waits
  `timeout_s` (default 3 s) and then reports the thread as leaked.
* `frame.meta.source_mode` is the session's mode. A replay frame is `replay` everywhere downstream.

## Queues and metrics

Every consumer queue is a size-1 mailbox by construction (queue depth ≤ 1, nothing can pile up); pressure shows as
`ConsumerMetrics.frames_skipped` (a pending frame replaced by a newer one) and as `frame_age_ms` (capture → callback
start). `frames_dropped` counts frames discarded before fan-out (replay late-skip, undecodable/unusable frames).
`e2e_latency_ms` = callback return − `t_capture_mono_ns` over analysis consumers (not `preview`). All values are
measured on the monotonic clock over the last 5 s; an empty window is `null`, never a nominal number.

## Overlays (A07)

Draw a result on the preview only when `observation.frame_id == PreviewFrameMeta.frame_id`; otherwise show its age
`preview.t_session_ms − observation.t_session_ms`. When the student view is mirrored, draw at `x' = 1 − x`.

## Health codes (component `capture`)

`stopped/idle` → `starting/opening` → `ok/running`; `degraded/frame_stall` (no frame for max(1 s, 4 intervals));
`unavailable/camera_disconnected` (reconnect loop with backoff 0.25 → 5 s until close; `details.reconnects`);
`stopped/replay_ended`; `stopped/closed`; `error/capture_error`. A failed `open()` raises `CaptureError` and leaves
`unavailable/<reason>`: `camera_not_found`, `camera_open_failed`, `camera_permission_denied`,
`camera_privacy_denied`, `camera_no_frames` (busy), `previous_capture_releasing`, `open_timeout`, `opencv_missing`,
or a replay reason (`manifest_missing`, `manifest_invalid`, `media_sha256_mismatch`, …). Missing frames are a
capture health problem, never `face_missing`.

## Replay format `qorgau.replay.v1` (content: A10)

`<settings.replay_dir>/<replay_id>.json` (default `proctoring/demo/replay/`), media **inside** `replay_dir`,
usually `media/` — media files are git-ignored (`*.avi`, `*.mp4`, …). Example:
[`examples/replay_manifest.example.json`](examples/replay_manifest.example.json); schema: `replay.schema.json`.

* `replay_id`: `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, equal to the file name.
* `media.kind`: `video` (any FFmpeg-decodable file) or `image_sequence` (directory of .jpg/.png/.bmp, name order).
* `media.timestamps`: `container` (decoder PTS) · `fps` (`index·1000/fps`) · `sidecar` (`{"pts_ms": [...]}`,
  non-decreasing, written by `record`; equal neighbours → +1 ms, counted). Other non-increasing values are repaired (+1 interval) and counted.
* `t_session_ms = replay_start_t_ms + media_pts_ms` (relative to `start_ms`, + loop period), `frame_id` = media
  index from 0 → identical across runs.
* `pacing`: `realtime` (default; late frames dropped and counted, never silently) or `lockstep` (deterministic:
  every consumer gets every frame it wants, decimated by `max_fps` in media time). `speed` (realtime), `loop`,
  `start_ms`/`end_ms`, `media.mirrored` (flip back a mirrored recording), `media.sha256` (verified at open).
* `labels`: ground-truth intervals for evaluation (A09/A10); capture ignores them. `provenance`: who/what/consent.

Commands (from `proctoring/`):
```
python -m proctor.capture record --replay-id phone_raise_01 --seconds 20 --consent "who agreed, how" --title "…"
python -m proctor.capture replay-check phone_raise_01          # validate + decode all frames
python -m proctor.capture replay-hash demo/replay/media/x.avi  # sha256 for the manifest
```
Regenerate the schema after changing `ReplayManifest`:
`python -c "import json; from proctor.capture.replay import ReplayManifest as M; s=M.model_json_schema(); print(json.dumps(s, indent=2))"`
(then add `$schema`, `$id`, `title` as in the committed file; the test ignores those three keys).

## LIVE verification on the target machine (Windows)

Not verifiable in the cloud container (no camera). On the demo laptop, with Qorgau Exam closed:
```
.venv\Scripts\python -m proctor.capture verify-live                      # enumeration, open, format, preview, 30 s load, restart x3
.venv\Scripts\python -m proctor.capture verify-live --interactive --snapshot   # + mirror, unplug/replug, busy, privacy
.venv\Scripts\python -m proctor.capture soak --source live:0 --minutes 20 --out %LOCALAPPDATA%\QorgauExam\a02-live\soak.json
```
Manual checklist (record PASS/FAIL/NOT RUN with the exact Windows build and camera model):
1. `verify-live` overall PASS for the non-interactive part; note backend (DSHOW/MSMF), open time, actual size/fps.
2. Mirror: raise the RIGHT hand → it appears on the LEFT of the unmirrored snapshot.
3. Unplug (or disable in Device Manager) → `camera_disconnected` within ~1–3 s; replug → `running`, frame ids continue.
4. Camera in use by the Windows Camera app → open refused (`CAMERA_BUSY`/`CAMERA_UNAVAILABLE`) or shared (note which).
5. Privacy switch "Let desktop apps access your camera" OFF → `CAMERA_DENIED` (`camera_privacy_denied`) — turn it back ON.
6. 20–30 min `soak --source live:0`: FPS, p50/p95 e2e latency, RSS growth after warm-up, threads released.
If MSMF is the only working backend and opens slowly, try `--backend dshow` / `--backend msmf` and report both.
