# C2 — STATUS (student uplink, qorgau.class.v1, student backend side)

Branch: `codex/class-C2`, created from `codex/proctor-integration` @ `64354c014c4ad51054e8bb67fe841580771d43e5`
(contains A02 clips 190a70d, A05 zones, A08, T02/T03/T04). Protocol: `proctoring/contracts/class/PROTOCOL_v1.md`
(frozen 2026-10-08 14:45, not changed by C2). Paths: `proctoring/backend/proctor/uplink/`, `proctoring/handoffs/C2/`
+ the minimal `backend/proctor/app.py` change allowed by A01 (below).

## How to run
```powershell
$env:QORGAU_CLASS_SERVER = "192.168.1.10:8765"   # teacher PC, host:port
$env:QORGAU_CLASS_CODE   = "123456"              # join code from the teacher panel
$env:QORGAU_CLASS_LABEL  = "Студент 1"           # optional, default = computer name
# then start Qorgau Exam / the backend as usual
```
Without both SERVER and CODE the uplink is not created and the backend works locally exactly as before.
A malformed value is logged (`class uplink disabled: …`) and the backend still starts.

## app.py change (A01-approved, minimal) — 4 lines
* `from .uplink import start_uplink`
* lifespan, after the calibration task: `state["uplink"] = start_uplink(settings, lambda: state.get("manager"), hub)`
* lifespan `finally`, before `manager.shutdown`: `if state.get("uplink") is not None: await asyncio.to_thread(state["uplink"].stop)`
Nothing else in A01 code is touched. `state["uplink"]` is `None` when not configured.

## What the uplink does (protocol §2–§5)
| Protocol | Implementation |
|---|---|
| `hello` → `welcome` | first message on `ws://<server>/ws/student`; `join_code` or `resume_token` (never in the URL, never logged). `resume_token` + `student_id` persisted in `<data_dir>/class_uplink/outbox.sqlite` (table `meta`, per server) |
| wrong code | `error{code:"join_rejected"}` → connection state `rejected`, **no further attempts** (exam continues locally, Electron gets `class_state` with `message_ru`). A rejected `resume_token` → one immediate retry with the join code |
| `status` | every 2 s and on any change: `exam_state` (created→idle, ready→preflight, aborted/failed→finished), `camera` (capture health ok→ok, camera_no_frames→busy, stopped→off, else unknown), `monitoring`, `zone` + `zone_reasons_ru` (A05 `assess_session_zone`, see coverage note), `incidents_total`, `incidents_by_priority`, `locked`, `mic_active` |
| `incident` | on open and on close (A08 `list_incidents` polled every 0.5 s); fields `seq, incident_id, rule_id, category, priority, state, t_start_wall, duration_ms, explanation_ru, clip_available`. When the clip becomes ready while the episode is open, the same incident is sent again (`state:"open"`, `clip_available:true`, new `seq`) |
| `preview` | while `running`: every 2 s, A02 preview re-encoded to fit 320×240 (4:3 → 320×240), JPEG ≤ 30 KB (quality steps 70→25) |
| `ping` → `pong` | immediately |
| `command` → `ack` | exactly one `ack` per command (also for unknown kinds: `ok:false`) |
| queue | `incident` + `ack` in SQLite outbox with client `seq` (from 1, survives restarts), ≤ 1000 (oldest dropped, counted), sent in `seq` order after reconnect; removed only after a successful send. `status`/`preview` are not queued |
| reconnect | 1 → 2 → 4 → 8 → 15 s (max), with `resume_token`; the exam is never affected (own thread + own asyncio loop) |
| clips (R16) | at the FIRST sight of an episode (not `monitoring_degraded`) while `running`: `capture.export_clip(t_start_ms, 5, 5)` in a background thread (A02), also while offline. `request_clip{incident_id}` → `POST http://<server>/api/student/clips/{incident_id}`, `Authorization: Bearer <resume_token>`, `Content-Type: video/x-msvideo`, waits ≤ 15 s if the clip is still being written; ack `ok:false` + `error_ru` if unavailable |

Commands: `start_exam` → `SessionRuntime.start()` only if the local session is `ready` (otherwise `ok:false`,
«Экзамен ещё не готов…»; `running` → ok); `finish_exam` → `SessionRuntime.finish()`; `lock {reason_ru 1–200}` /
`unlock` / `audio_start {direction}` / `audio_stop` → uplink state + `class_state` event. **The microphone itself is
not opened by the backend**: Electron (renderer) owns getUserMedia/WebRTC (protocol §6) — not implemented.

## Stream message for Electron: `class_state` (WS `/v1/stream`, envelope `message`)
Published with `session_id = null` (every stream client receives it) on: connection change, welcome, every
lock/unlock/audio/start/finish command.
```json
{
  "type": "class_state",
  "connection": "connecting | connected | reconnecting | rejected | stopped",
  "server": "192.168.1.10:8765",
  "student_id": "st-1 | null",
  "locked": true,
  "lock_reason_ru": "Телефон на столе | null",
  "mic_active": false,
  "audio_direction": "listen | talk | both | null",
  "exam": {"exam_id": "...", "title": "...", "mode": "url|app", "allowed_urls": [], "allowed_apps": [], "instructions_ru": "..."},
  "last_command": {"command_id": "uuid", "kind": "lock", "ok": true},
  "message_ru": "string | null"
}
```
Model: `proctor.uplink.client.ClassStateMsg`. It is an ADDITIVE type, not part of contract `qorgau.v1` 1.0.0
`StreamPayload`: a strict renderer validator must accept/ignore unknown `type` (A07/next agent). Lock screen and mic
indicator are the next agent's job: show the lock screen while `locked`, the banner «Микрофон включён преподавателем
для проверки» while `mic_active`.

## Zone while the exam runs (fix inside C2, note for A05/A08)
A08 `summary()` → A05 `coverage_from_summary` needs `session.finished_at`, which is `None` while running → every
running student without episodes would be **grey**. The uplink passes `coverage = observed_ms / (now − started_at −
paused_ms)` (1.0 during the first 5 s) to `assess_session_zone(..., coverage=…)`. Server-side grey (no status 10 s /
camera ≠ ok) is unchanged.

## Checks (Windows 11 demo laptop, Python 3.12.14, env from uv.lock)
| Command (from `proctoring/`) | Result |
|---|---|
| `pytest backend/proctor/uplink` | **14 passed**: config; hello/welcome/status/preview/pong + token/code not in logs; wrong code → rejected, 1 attempt only; server down at start → connects later + queued incident delivered; drop + restart → resume_token, resend in seq order, seq keeps growing; duplicate seq (unconfirmed send) → same seq+msg_id, server keeps one; outbox seq survives restart + 1000 cap; lock/unlock/audio → class_state + ack, invalid lock / unknown command → ack ok:false; clip at OPEN (< 1 s) + upload on request_clip (Bearer, video/x-msvideo); start/finish commands; **create_app end-to-end** (synthetic): start_exam → running, lock → `class_state` on `/v1/stream`, A05/A08 incident → uplink → real A02 clip uploaded, finish_exam; no env → no uplink; server unavailable → exam runs locally, shutdown not blocked |
| `pytest` (whole repo) | 1006 passed, 40 skipped; failures not in C2 code: 3 A03 phone tests (identical on the clean baseline, Windows path separator) and 1 A08 test (`test_purge_expired_media_keeps_metadata`) that passes alone (flaky under load) |
Fake server for tests: `backend/proctor/uplink/tests/fake_server.py` (FastAPI+uvicorn, NOT the real class server).

## NOT verified
* Against the real class server (T01 `/ws/student` does not exist yet) and the real teacher panel; over a real LAN/Wi-Fi.
* WebRTC audio (`audio_signal`), Electron lock screen / mic indicator, `snapshot_jpeg_b64` (optional, not sent).
* Long run (hours) and 1000-item outbox overflow under a real outage (unit-tested only).

## После 9 октября
* `audio_signal` relay to the renderer; snapshot in `incident`; outbox compaction for long outages.
* Contract: add `class_state` to `StreamPayload` (A01).
