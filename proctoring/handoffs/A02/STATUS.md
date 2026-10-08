# A02 — STATUS (camera, frame stream, runtime metrics)

Role: A02, the single owner of the camera/replay/synthetic frame source.
Branch: `claude/pensive-pasteur-wnjd2n` (platform-assigned), fast-forwarded from 7bccece to the A01 BOOTSTRAP.
Contract / baseline SHA: `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (A01 BOOTSTRAP, qorgau.v1 1.0.0, frozen).
Previous checkpoint: `e4934b3b566504779bf3a72ec3d6f7d69bcb8b2d` (checkpoint 1, pushed). New SHAs are reported to the user after push.
Stage: **checkpoint 2 — tools (`python -m proctor.capture soak | verify-live | record | replay-check | replay-hash`),
module README + replay JSON Schema, fixes from self-review (close() from a listener/consumer thread no longer
self-joins; a worker that waited for a restart-overlapping callback takes the newest frame; re-entrant notify lock).**
Checkpoint 1: `create_capture_service(settings)`, live/replay/synthetic sources, bounded latest-frame fan-out,
clean close/restart, health state machine, measured metrics.

## Changed paths (A02 only)
`proctoring/backend/proctor/capture/` (`__init__.py`, `service.py`, `fanout.py`, `sources.py`, `replay.py`,
`stats.py`, `measure.py`, `verify_live.py`, `record.py`, `__main__.py`, `README.md`, `replay.schema.json`,
`examples/`, `tests/`) and `proctoring/handoffs/A02/`. No shared file edited (requests: `DEPENDENCIES.txt`).

## Interfaces provided
* `proctor.capture.create_capture_service(settings) -> FrameCaptureService` — implements
  `proctor_contracts.interfaces.CaptureService` for `live`, `replay` and `synthetic`. A01's `ModuleRegistry`
  picks it up with no change in `app.py` (verified: `isinstance(registry.loaded["capture"].impl, FrameCaptureService)`).
* `FramePacket.image`: uint8 HxWx3 **BGR**, C-contiguous, **read-only view** shared by all consumers and the ring
  (a view of a read-only base: `flags.writeable = True` raises). Never mirrored. Consumers copy before modifying
  (e.g. `cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB)` returns a new array for MediaPipe).
* `frame_id`: from 0 per session, strictly increasing; stable across camera reconnects; for replay = media index
  (minus the trimmed prefix, plus earlier loops) → identical in every run.
* `t_session_ms`: live/synthetic = `SessionClock.mono_to_session_ms(read time)`; replay =
  `replay_start_t_ms + media_pts_ms` (recording timestamps). `wall_time = clock.wall_at(t)`.
* Consumers: one worker thread each, size-1 latest-frame mailbox; `max_fps` waits BEFORE taking the frame (the
  wait never ages it); exceptions are counted (`errors`) and logged rate-limited, capture continues; a callback is
  never concurrent with itself, also across close()/open() while an old callback is still running.
* Preview: internal consumer `preview` (reserved name) at `settings.preview_fps`, JPEG `preview_jpeg_quality`,
  downscaled above 960 px width; the first preview of a run is encoded synchronously so preflight's lighting check
  sees it. `latest_preview()` is an attribute read (safe on the asyncio loop).
* `get_frame(frame_id)`: ring of `frame_ring_seconds` (timeline span) capped at 256 MB.
* Health (component `capture`) — codes in `DEPENDENCIES.txt` R5. Missing frames → `frame_stall` /
  `camera_disconnected`, never face_missing. Listener notifications are delivered in state order.
* Open failures → `CaptureError`: `CAMERA_UNAVAILABLE` (camera_not_found / camera_open_failed / open_timeout),
  `CAMERA_DENIED` (Linux EACCES; Windows privacy switch "Deny", read-only registry check), `CAMERA_BUSY`
  (opened but no frames; previous capture still releasing the device), `REPLAY_INVALID` (reason in details),
  `SESSION_ACTIVE` (already open). Health shows `unavailable/<reason>` until the next open/close.
* Live reconnect: 3 failed reads or 1 s without a frame → `camera_disconnected`, handle released, reopen with
  backoff 0.25 s → 5 s until close(); success → `running`, `details.reconnects += 1`.
* Replay format `qorgau.replay.v1` (owner A02, content A10): `settings.replay_dir/<replay_id>.json`, media inside
  `replay_dir` (git-ignored). Fields, timestamps (`container`/`fps`/`sidecar`), `pacing` (`realtime` default /
  `lockstep` deterministic), `speed`, `loop`, `start_ms`/`end_ms`, `media.mirrored`, `media.sha256`, `labels`
  (ground truth for A09/A10, ignored by capture), `provenance`. Spec: docstring of `capture/replay.py`.
  Safety: replay ids `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, relative media paths without `..`, resolved path (incl.
  symlinks) must stay inside `replay_dir`, manifest ≤ 256 KB, `extra="forbid"`.
* Metrics: measured on the monotonic clock over a 5 s window (effective window reported in `window_s`); empty
  window → `None`, never a nominal number. Definitions: `DEPENDENCIES.txt` R6.

## Checks run (Linux x86_64 cloud container, Xeon 2.8 GHz 4 vCPU, Python 3.12.3, opencv-contrib 4.13.0; NO camera)
| Command (from `proctoring/`) | Result |
|---|---|
| `.venv/bin/python -m pytest -q backend/proctor/capture/tests` | 86 passed (fan-out 10, lifecycle 21, camera 17 on a FAKE device, replay 25, app integration 5, tools 8) |
| `.venv/bin/python -m pytest -q` (whole repo) | 149 passed, **1 failed**: A01 `test_health_reports_missing_modules_honestly` (expects capture "module_not_integrated"; R1) |
| `python -m proctor.capture soak --source synthetic --seconds 15` | 15.0 s, 451 frames, 30.06 FPS, phone_sim 8.0 FPS e2e p95 92 ms, attention_sim 14.9 FPS e2e p95 57 ms, threads released (short run; long run pending) |
| `python -m proctor.capture verify-live` (container) | FAIL camera_enumeration: no camera (expected here; success path tested on a FAKE device only) |
| `.venv/bin/python -m proctor smoke` | 34/34 PASS — now through the real A02 capture module (synthetic) |
| `.venv/bin/python contracts/tools/generate.py --check` | PASS |
| `.venv/bin/python coordination/verify_ownership.py --agent A02 --base 35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` | see commit report |

Acceptance cases covered by tests: slow consumer (capture keeps ~30 FPS, slow consumer starts on frames < 80 ms
old), out-of-order completion across consumers (attributed by frame_id, ring lookup matches), disconnect +
reconnect (frame ids continue), open failure (not found / denied / Windows privacy / busy / timeout), stop during a
blocking read (close returns within its timeout; device stays owned → next live open `CAMERA_BUSY` until released),
restart ×6 without thread leaks, invalid replay files (bad id, JSON, schema, traversal, symlink escape, sha256,
undecodable, empty range, bad sidecar), deterministic lockstep replay (identical frame ids, timestamps, pixels).

## NOT verified (honest)
* **LIVE: no physical camera was available.** All camera tests use an emulated VideoCapture. DirectShow/MSMF
  behaviour, the Windows privacy-registry diagnosis, real disconnect timings, driver mirroring — unverified.
  LIVE is NOT declared PASS.
* No long run yet (target 20–30 min after the first A03/A04 integration). No CV model ran.
* Windows install/run not tested by A02.

## Integration order / notes for others
A02 is first in `integration_order`. Merge needs no app.py change; A01 must apply R1 (and R2 for camera machines).
A03/A04: get frames only via `add_consumer` (A01 registers them); never open cv2.VideoCapture. A07: overlay with the
observation whose `frame_id` == `PreviewFrameMeta.frame_id`, otherwise show age = preview.t_session_ms − obs.t_session_ms.
A10: replay manifests in `demo/replay/` per the format above; media files outside Git.

## Tools (see `backend/proctor/capture/README.md`)
`soak` (long run, JSON report: hardware, resolution, FPS, frame age/e2e p50/p95/p99, RSS, threads; targets kept
separate from measurements), `verify-live` (Windows checklist: enumeration, open, format, preview, 30 s load,
restart ×3; `--interactive`: mirror, unplug/replug, busy, privacy → PASS/FAIL/MANUAL/NOT_RUN), `record`
(consented clip via the same service, sidecar timestamps, sha256, provenance), `replay-check`, `replay-hash`.

## Next (A02)
Adversarial multi-agent review of threading/shutdown/replay/contract (running), long synthetic+replay soak on this
container, then integration runs with A03/A04 via contracts.
