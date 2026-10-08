# Qorgau Exam — contracts v1 (FROZEN for BOOTSTRAP)

Contract id `qorgau.v1`, version `1.0.0`. Owner: **A01**. Change requests: `proctoring/handoffs/Axx/DEPENDENCIES.txt`
(what, why, which fixture shows it). A01 answers with a new contract version; nobody edits shared files directly.

Single source of truth: `proctoring/contracts/python/proctor_contracts/v1.py` (Pydantic v2).
Generated, never edited by hand (`python contracts/tools/generate.py --check` must pass):

| Artifact | Path |
|---|---|
| JSON Schema 2020-12 (all wire types under `$defs`) | `contracts/schema/v1/qorgau.v1.schema.json` |
| TypeScript wire types | `contracts/ts/qorgau-v1.generated.ts` (import as `@contracts/qorgau-v1.generated`) |
| Typed TS fixtures (`satisfies`, compiled by `npm run check:contracts`) | `contracts/ts/fixtures.generated.ts` |
| Python in-process interfaces (hand-written) | `contracts/python/proctor_contracts/interfaces.py` |
| Renderer ↔ shell bridge (hand-written) | `contracts/ts/bridge.ts` (`window.qorgau: QorgauBridge`) |
| Fixtures `<WireModel>.<case>.json` (all `synthetic`) | `contracts/fixtures/v1/` |

TypeScript types describe **fully serialized** messages: every key is present (`null` when empty). TS code that
builds a request sends every key too. Python models reject unknown fields (`extra="forbid"`).

## 1. Semantics (normative)

**Time.**
* `t_session_ms` — the ONLY ordering/duration clock. 0 = session creation (`POST /v1/sessions`). Live/synthetic:
  `(monotonic_ns − origin)/1e6` via `SessionClock`. Replay: `replay_start_t_ms + media_pts_ms` (recording timestamps,
  deterministic; pacing `realtime` by default). Session time keeps running during pause.
* `wall_time`, `*_at` — UTC ISO‑8601 with offset, derived from `SessionClock.wall_at(t)`, display/report only. Naive
  datetimes are rejected. A wall-clock jump never reorders events.
* `t_capture_mono_ns` — process-local monotonic, for latency only; not comparable across processes.
* Environment events from Electron: backend stamps `t_session_ms` on receipt; `client_wall_time`/`client_seq` keep the
  shell's view (`client_seq` = dedup key, strictly increasing per shell run).
* Freshness: an observation older than the consumer's TTL is *stale* → treated as unknown, never as "no violation".

**Coordinates and mirroring.** Normalized `[0,1]`, origin top-left, x right, y down, relative to the **unmirrored**
frame. Backend frames are never mirrored (`mirrored: false`). The UI may mirror the preview for the student; then it
draws overlays at `x' = 1 − x`. Overlays use the matching `frame_id` or show the age of the result.

**Directions.** `left/right` are **subject-centric** (the student's own left/right). `HeadPose`: +yaw = turns to their
right, +pitch = up, +roll = tilts toward their right shoulder (degrees).

**Confidence vs quality vs priority.**
* `confidence` ∈ [0,1] — the estimator's score for that specific claim (detector score, track quality…). NOT a
  probability of cheating, not calibrated unless stated.
* `quality` ∈ [0,1] + `quality_flags` (`low_light`, `blur`, `partial_face`, `small_object`, `uncalibrated`, `stale`,
  `synthetic`, …) — input/measurement quality.
* `ReviewPriority` low/medium/high — priority of **human review** from transparent rules. Never shown as guilt.

**Unknown.** `status: unknown|degraded|error`, `null` values, `SignalState.unknown|insufficient_evidence`,
`Direction.unknown` mean "not determined". They never become "violation" and never become "all clear".
`face_count: null` ≠ `0`. Missing frames are a **capture health** problem, not `face_missing`.
`possible_screen_capture` returns `insufficient_evidence` when the view cannot show it (coverage gap, reported).

**Source mode.** Every record carries `source_mode` = the session's mode (`live|replay|synthetic`), including
environment and health observations. LIVE is never silently replaced: a session cannot change mode; missing modules
make live preflight fail instead of falling back. Bootstrap parts are labelled `producer.module = "bootstrap.*"`.

**Versions.** `Producer{module, version, model_id, model_sha256, config_version}` on every observation; incidents carry
`rule_version` + `config_version`; `ModelManifest` (sha256 of the actual file) per model; reports embed all of them.

**Identifiers.** `Id` = `^[A-Za-z0-9._:-]{1,128}$`, opaque, never a filesystem path. `observation_id` unique per
session (re-delivery keeps the id); incidents are idempotent by `(incident_id, update_seq)`.

**Errors.** Every non-2xx body is `ApiError{error:{code, message, retryable, details}}` with `ErrorCode`:
401 UNAUTHORIZED · 403 FORBIDDEN_ORIGIN · 404 SESSION_NOT_FOUND/NOT_FOUND · 409 INVALID_STATE/SESSION_ACTIVE/
PREFLIGHT_FAILED/SESSION_MISMATCH · 411/413 PAYLOAD_TOO_LARGE · 422 INVALID_ARGUMENT · 501 NOT_IMPLEMENTED ·
503 CAMERA_*/MODEL_*/STORAGE_ERROR/MODULE_NOT_INTEGRATED · 500 INTERNAL (no stack traces in bodies).
Python modules raise `proctor_contracts.interfaces.ProctorError` subclasses (`CaptureError`, `ModelError`, …).

**Privacy.** Default = metadata only. Media (snapshots/clips) only when the session has `retain_media: true`.
`EnvironmentDetail` is allow-listed: process basename, shortcut, duration — no window titles, typed text, clipboard.
No face embeddings/identity. Calibration data is per-session and dropped in `end_session()`.

## 2. Lifecycle

```
created ──preflight──► preflight ──calibration/start──► calibrating ──calibration/finish(ok)──► ready
                         │  ▲                             │  └─calibration/cancel──► preflight
                         └──┴──calibration/skip(reason)───┴─────────────────────────► ready
ready ──start──► running ◄──pause/resume──► paused
any non-terminal ──finish──► finished      any non-terminal ──abort──► aborted      (failed = internal fatal)
```
* **One non-terminal session per backend** (`SESSION_ACTIVE` otherwise) → one camera owner, no cross-session mixing.
* Capture opens at `preflight`, closes at `finish/abort`. Analyzers get `start_session` before capture opens and
  `end_session` after it closes. A new `IncidentEngine` + fusion thread per session, created at `start`.
* `start` requires preflight `ready` (all *required* checks pass) and calibration `completed` or explicitly `skipped`.
* Required checks: live/replay → camera, phone_model, face_model, fusion, storage; **live** also
  environment_protection (shell must report capabilities). Synthetic → camera only (others WARN, labelled bootstrap).
* **Pause** (operator only; the student UI has no pause): CV observations are not fed to fusion/storage, open incidents
  close with `end_reason=session_paused`, the interval is a coverage gap (`paused_total_ms`), answers are rejected
  (409), the shell releases restrictions until `resume`. Preview may continue for the operator.
* `finish`: stop capture (joins consumer threads) → drain the fusion queue → `engine.finish()` closes every open
  incident → analyzers `end_session()` → `finished`. Idempotent. After finish no answers/observations are accepted.
* `abort` = guaranteed emergency exit with the same cleanup (`end_reason=session_aborted`). Backend shutdown aborts the
  active session. The shell treats `paused|finished|aborted|failed` (and backend loss) as "release all restrictions".

## 3. HTTP / WebSocket API v1

Loopback only (`127.0.0.1`, ephemeral port). Every request: `Authorization: Bearer <token>` (≥32 chars). `Host` must
be `127.0.0.1`/`localhost`; requests with an `Origin` header are rejected unless `QORGAU_DEV_ALLOW_ORIGIN` matches
(browser dev only). Bodies ≤ 1 MB, no chunked bodies. Prefix `/v1`.

| Method & path | Body → Response | Owner |
|---|---|---|
| GET `/health` | → `HealthReport` | A01 |
| POST `/sessions` | `SessionCreate` → 201 `SessionInfo` | A01 |
| GET `/sessions/{sid}` | → `SessionInfo` | A01 |
| POST `/sessions/{sid}/preflight` | → `PreflightReport` | A01 |
| POST `/sessions/{sid}/calibration/start` · `/finish` · `/cancel` | → `CalibrationState` | A01 → A04 |
| POST `/sessions/{sid}/calibration/target` | `CalibrationTargetRequest` → `CalibrationState` | A01 → A04 |
| POST `/sessions/{sid}/calibration/skip` | `CalibrationSkipRequest` → `CalibrationState` | A01 → A04 |
| GET `/sessions/{sid}/calibration` | → `CalibrationState` | A01 → A04 |
| POST `/sessions/{sid}/start` · `/resume` · `/finish` | → `SessionInfo` | A01 |
| POST `/sessions/{sid}/pause` | `PauseRequest` → `SessionInfo` | A01 |
| POST `/sessions/{sid}/abort` | `AbortRequest` → `SessionInfo` | A01 |
| GET `/sessions/{sid}/exam` | → `ExamDefinition` (content: A10 `demo/exams/demo_exam.json`) | A01 |
| GET `/sessions/{sid}/metrics` | → `RuntimeMetrics` | A01 → A02 |
| GET `/sessions/{sid}/preview.jpg` | → `image/jpeg` + header `X-Qorgau-Preview-Meta: PreviewFrameMeta` (204 if none) | A01 → A02 |
| PUT `/environment/capabilities` | `EnvironmentCapabilities` → same | A01 (called by A06) |
| GET `/environment/capabilities` | → `EnvironmentCapabilities \| null` | A01 |
| POST `/sessions/{sid}/environment/events` | `EnvironmentEventBatch` → `EnvironmentEventAck` | A01 (called by A06) |
| GET `/sessions` | → `SessionInfo[]` (history, newest first) | **A08** |
| GET `/sessions/{sid}/incidents` | → `Incident[]` (by `t_start_ms`) | **A08** |
| GET `/sessions/{sid}/incidents/{iid}` | → `IncidentDetail` | **A08** |
| POST `/sessions/{sid}/incidents/{iid}/reviews` | `HumanReviewCreate` → `HumanReview` (append-only) | **A08** |
| GET `/sessions/{sid}/evidence/{eid}` | → media bytes (opaque id, session ownership checked) | **A08** |
| PUT `/sessions/{sid}/answers/{qid}` | `AnswerUpsert` → `AnswerRecord` (only while `running`; last-writer by `client_seq`) | **A08** |
| GET `/sessions/{sid}/answers` | → `AnswerRecord[]` | **A08** |
| GET `/sessions/{sid}/summary` | → `SessionSummary` | **A08** |
| GET `/sessions/{sid}/report.html` | → self-contained escaped HTML (no external URLs/JS) | **A08** |
| GET `/sessions/{sid}/report.json` | → JSON export incl. `ExportManifest` | **A08** |
| DELETE `/sessions/{sid}` | → `{"deleted": true}` (409 if active) | **A08** |
| WS `/stream[?session_id=]` | JSON text frames `StreamEnvelope` (hello, session_state, observation, incident, health, metrics, calibration, error) | A01 |
| WS `/preview[?session_id=]` | binary: `uint32 BE header_len` + `PreviewFrameMeta` JSON + JPEG | A01 → A02 |

WebSocket auth: header `Authorization: Bearer` (Electron main, `ws` package) or, for browser dev only, subprotocols
`["qorgau.v1", "qorgau.bearer.<token>"]`. Never put the token in a URL. `StreamEnvelope.seq` is per connection; a gap
means a slow client dropped messages (refetch via REST). The A08 router is mounted by A01 under `/v1` behind the same
auth; it must not add auth bypasses and gets a `BackendContext` (session lookup, active session id, capture).
Until A08 lands, an in-memory bootstrap router serves the same paths for synthetic sessions (report/evidence → 501).

## 4. Backend process handshake (A06 ↔ A01)

```
spawn: <python> -m proctor serve --token-stdin --port 0      (cwd = proctoring/, env QORGAU_* settings)
stdin line 1: <token>\n            # 32+ random bytes hex, generated per launch by main, never logged
stdout: QORGAU_READY {"contract":"qorgau.v1","contract_version":"1.0.0","backend_version":"0.1.0","port":N,"pid":P}
stderr: logs only
stdin EOF or line "shutdown"  →  backend aborts the active session and exits (≤ ~5 s graceful)
```
Main then polls `GET /v1/health`, sends `PUT /v1/environment/capabilities`, opens `WS /v1/stream` and `/v1/preview`.
The renderer never learns port/token: it only calls `window.qorgau` (bridge.ts), each method = one fixed route.

## 5. Python in-process interfaces (`proctor_contracts.interfaces`)

| Factory (package `__init__.py`) | Returns | Owner |
|---|---|---|
| `proctor.capture.create_capture_service(settings)` | `CaptureService` | A02 |
| `proctor.phone.create_phone_analyzer(settings)` | `FrameAnalyzer` (`name="phone"`) | A03 |
| `proctor.attention.create_attention_analyzer(settings)` | `AttentionAnalyzer` (`name="attention"`) | A04 |
| `proctor.fusion.create_incident_engine(session_id, source_mode, settings)` | `IncidentEngine` | A05 |
| `proctor.evidence.create_evidence_store(settings)` | `EvidenceStore` | A08 |

`FramePacket(meta: FramePacketMeta, image: np.ndarray)` — uint8 HxWx3 **BGR**, C-contiguous, **read-only**
(`flags.writeable=False`), shared without copies; consumers copy before modifying. `frame_id` strictly increasing per
session from 0.

**Threads (normative).** CaptureService owns capture threads and one worker per consumer with a size‑1 latest-frame
mailbox (stale pending frames are replaced and counted as skipped). `FrameAnalyzer.process()` runs only on its consumer
thread; its exceptions are caught/counted by capture. `AttentionAnalyzer.calibration_*()` are called from API threads →
thread-safe. `IncidentEngine` is called only from the session's fusion thread (A01), `advance()` every
`settings.fusion_tick_ms` (250 ms). `EvidenceStore.record_*` from the fusion thread, router handlers from FastAPI
threads → thread-safe. Nobody blocks the asyncio loop. Analyzers `load()` at backend startup (no downloads; missing
weights → `UNAVAILABLE` health, not an exception). `end_session()` must be idempotent.

Calibration: 5 targets `center,left,right,up,down`; samples come from frames delivered to `process()`; completion is
decided by sample count/quality, never a timer; failure is explicit (`phase=failed`, per-target `message_code`).

## 6. Fixtures and compatibility checks

* `python -m pytest contracts/tests` — every fixture validates in Pydantic **and** JSON Schema, round-trips, invalid
  payloads are rejected (traversal ids, inverted bbox, naive datetimes, extra fields).
* `npm run check:contracts` (in `desktop/`) — generated TS + typed fixtures compile with `strict`.
* Module owners add their own golden fixtures inside their package; shared fixtures change only via A01.
