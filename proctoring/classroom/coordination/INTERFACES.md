# Qorgau Classroom — interfaces for T02–T05 (owner: T01)

Everything here is code that runs today on `codex/proctor-T01`, except the items marked **planned (checkpoint 2)**.
Contract sources of truth:

| What | Where | Version |
|---|---|---|
| Student wire (class PC ↔ student PC) | `proctoring/contracts/class/PROTOCOL_v1.md` (A01, frozen) + additive v1.1 fields in `classroom/contracts/models.py` | `qorgau.class.v1`, extension `1.1` |
| Teacher API (REST + `/ws/teacher`) and records | `classroom/contracts/models.py` (Pydantic v2) | `qorgau.classroom` `1.0.0` |
| Generated JSON Schema | `classroom/contracts/schema/classroom.v1.schema.json` | generated, do not edit |
| Generated TypeScript | `classroom/contracts/ts/classroom-v1.generated.ts` | generated, do not edit |
| Examples (one per record/message, valid in both Pydantic and JSON Schema) | `classroom/contracts/fixtures/v1/*.json` | generated from `fixtures_src.py` |

`python -m classroom.contracts.generate --check` fails when the committed outputs do not match the models.

## 1. Rules that every role relies on

* **Unknown fields are ignored on input; output is strict.** Student messages (`_In`) accept unknown fields, so peers
  can evolve additively. Server records and teacher request bodies reject unknown fields, so typos fail loudly.
* **Every v1.1 field is optional.** A plain v1 client (C2 today: no `capabilities`, no `event_id`) works unchanged.
* **Times** are ISO-8601 with an offset. Naive datetimes are rejected.
* **Every event has `event_id`, `event_time`, `received_at`** (`ObservationEvent`).
  * Dedup key: `(student_id, event_id)`.
  * A v1.1 client sends `event_id`. For a plain v1 client the server derives `v1:<client_run_id>:<seq>`, which is the
    protocol §3.1 rule "(student_id, seq)".
  * The same `seq` with **different content** means the client lost its counter. That event is kept as well, with
    `seq_conflict: true`. Nothing is silently dropped.
  * A closed episode never reopens.
  * An update of an open episode under a new `seq` is stored, for example C2 re-sending it with `clip_available: true`.
* **A sent command is not an executed command.** `Command.status` goes:
  * `queued` → `sent` (written to the socket) → `received` (v1.1 `command_progress`);
  * then one of `succeeded` / `failed` (from the ack), `expired` (never delivered after `expires_at`), or `cancelled`
    (only while `queued`).
  * `unconfirmed: true` means no ack within `ack_window_s` (10 s, protocol §5 «команда не подтверждена»). The command is
    still `sent`, not failed.
  * After a reconnect (or a server restart) an unacknowledged command is delivered again with the same `command_id` and
    `attempt + 1`. A late ack is recorded with `late: true`.
* **Video never travels as JSON.**
  * A preview is ≤ 30 KB JPEG and is rate-limited to one per second per student.
  * The teacher stream carries metadata plus `url` (`/api/teacher/students/{id}/preview.jpg?seq=N`). Inline
    `jpeg_b64` is added for a socket opened with `?inline_previews=1`, and by default while the server hosts the T02
    class panel, because its v1 adapter renders only inline previews (DEPENDENCIES D5). This is loopback traffic, and
    `?inline_previews=0` turns it off.
  * A clip is an HTTP upload (`POST /api/student/clips/{incident_id}`, ≤ 8 MB, `video/mp4` or `video/x-msvideo`).
    `ClipMetadata` carries a URL, never bytes.
* **Simulated data is labelled.** `hello.simulated=true` makes `origin: "simulated"` on the student, card, event,
  incident, preview header `X-Qorgau-Origin`, and the simulator names students `SIM-NN (симуляция)`. A UI must show it.

## 2. Teacher API (served by `python -m classroom.server`)

Teacher UI: `GET /` serves the panel (`--ui auto|teacher-ui|class-panel|none|<dir>`; `auto` = the React shell when built,
otherwise the T02 class panel with `/config.json` = `{"adapter":"real"}`). Without the cookie `/` redirects to
`GET /login`, a server-rendered PIN form (`POST /login`, `POST /logout`), so the panel itself never handles the PIN.

Teacher endpoints answer only to a loopback client with a loopback `Host` and a same-origin (or absent) `Origin`. All of
them except `login` need the cookie `qorgau_teacher` (HttpOnly, SameSite=Strict). Students can never call them: a student
token is not a teacher cookie. Errors are always `{"error": {"code", "message_ru", ...}}` (`ApiError`).

| Method + path | Body → result | Owner |
|---|---|---|
| `POST /api/teacher/login` | `LoginRequest{pin}` → `{ok, teacher_id}` + cookie; 401 `pin_rejected`; 429 `pin_rate_limited` (5 wrong → 60 s) | T01 |
| `POST /api/teacher/logout` | → `{ok}`, cookie revoked | T01 |
| `GET /api/teacher/info` | `ServerInfo` (version, session, counts, mounted features with status) | T01 |
| `POST /api/teacher/session` | `SessionCreate{title}` → 201 `SessionCreated{session, join_code}` (closes the previous session) | T01 |
| `GET /api/teacher/session` · `POST /api/teacher/session/close` | `Session` or `null` | T01 |
| `GET /api/teacher/students` | `StudentCard[]`, **flat** with the v1 names the T02 REAL adapter reads: `student_id, computer_name, student_label` + status §3.1 (`exam_state, camera, monitoring, zone, zone_reasons_ru, incidents_total, incidents_by_priority, locked, mic_active`) + `connected, last_status_at, last_event_at, incidents_open, incidents_unreviewed, preview_url, preview_at, zone_reported, zone_source, stale, origin` | T01 |
| `GET /api/teacher/students/{id}` | `StudentCard` | T01 |
| `GET /api/teacher/students/{id}/events?limit=` | `ObservationEvent[]`, newest first | T01 |
| `GET /api/teacher/students/{id}/preview.jpg` | JPEG, 204 if none, `Cache-Control: no-store` | T01 |
| `POST /api/teacher/students/{id}/commands` | `CommandCreate{kind, payload, ttl_ms?}` → 202 `Command` | T01 (bus) |
| `GET /api/teacher/students/{id}/commands` · `GET /api/teacher/commands/{cid}` · `POST /api/teacher/commands/{cid}/cancel` | `Command` | T01 |
| `GET /api/teacher/students/{id}/audio` | `AudioSession[]` | T01 (core relay) |
| `GET /api/teacher/students/{id}/incidents` | core fallback `Incident[]`; replaced by T03 when mounted | T03 |
| `POST /api/teacher/students/{id}/decision` · `GET /api/teacher/clips/{incident_id}` | 501 `feature_not_installed` until T03 is mounted | T03 |
| `/api/teacher/history/*`, `/api/teacher/clips/*` | T03 | T03 |
| `/api/teacher/control/*` | T04 | T04 |
| `/api/teacher/audio/*` | T05 | T05 |
| `GET /api/teacher/openapi.json` | OpenAPI of everything mounted | T01 |

Student HTTP: `POST /api/student/ping` (core) and `POST /api/student/clips/{incident_id}` (T03). Both need
`Authorization: Bearer <resume_token>`, enforced by the server gate before any feature code runs. Bodies are limited by
`Content-Length` (clips 8 MB, otherwise 256 KB). A chunked body without a length gets 411.

### `WS /ws/teacher`

* Cookie checked on connect; without it the socket closes with **4401**.
* First messages: `hello{info: ServerInfo}`, then `snapshot{students: StudentCard[]}`.
* Every message has a per-connection `seq` (1, 2, 3 …). A gap means this client lost messages: refetch REST.
* When the client is too slow (queue 2000), the server sends one `resync_required` and drops the backlog. Refetch
  `GET /api/teacher/students`.
* Types (union `TeacherStreamMessage`):
  * `student_update{student: StudentCard}`;
  * `incident{student_id, incident, duplicate}`;
  * `preview{student_id, preview_seq, url, frame_wall, received_at, byte_length, origin, jpeg_b64?}`;
  * `command_update{command, change}`;
  * `ack` (v1 legacy);
  * `audio_session_update`;
  * `audio_signal`;
  * `session_update`;
  * `feature_event{feature, event, data}`;
  * `resync_required`;
  * `error`.
* Teacher → server: `TeacherAudioSignal{type:"audio_signal", audio_session_id, sdp?|ice?}` (core relay). T05 message
  types: **planned (checkpoint 2)**, see §5.

### `WS /ws/student` (C2 and the simulator)

* Exactly `PROTOCOL_v1.md`: `hello` within 10 s → `welcome` (`session_id`, `resumed` in v1.1) or
  `error{code: join_rejected | resume_rejected | join_rate_limited}`.
* 5 wrong codes per IP → 30 s pause.
* `ping` every 5 s. Any received message counts as alive. 15 s of silence → close **4408**, and the student goes offline
  and grey.
* A second socket with the same token closes the first with **4409**.
* Unknown message types are ignored; invalid ones get `error{code:"invalid_message"}` and the socket stays open.
* v1.1 (optional, all additive):
  * `hello.client_run_id`, `hello.simulated`, `hello.capabilities{commands[], command_progress, command_expiry, …}`;
  * `incident.event_id`;
  * `ack.code | executed_at | result | seq`;
  * the `command_progress` message;
  * `command.issued_at | expires_at | ttl_ms | attempt`;
  * kind `apply_policy`, sent only to clients whose `capabilities.commands` lists it.

## 3. Server plug-in API — how T03/T04/T05 add server code

You do not edit the server. You write a factory **in your own path** and the server loads it:

```bash
QORGAU_CLASS_FEATURES="classreview.classroom_feature:create,proctor_classctl.classroom_feature:create" \
  python -m classroom.server            # or: --features "pkg.mod:factory,..."
```

`factory(ctx: FeatureContext) -> feature`. The feature is any object. Everything is optional except `name` and `owner`:

```python
class MyFeature:
    name = "history"            # [a-z0-9_]
    owner = "T03"               # must match your OWNERSHIP role
    router: fastapi.APIRouter   # routes only under ROUTE_RESERVATIONS[owner] (+ V1_DELEGATED for T03), else startup refuses it
    def migrations(self) -> list[Migration]: ...          # ids "t03_0001_name", tables "t03_*"; edited migrations are refused (checksum)
    def on_student_connected(self, student: Student, hello: dict) -> dict | None: ...  # return a welcome.exam override (T04)
    def on_student_message(self, student_id: str, message: dict) -> None: ...  # every validated status/incident/ack/command_progress/audio_signal
    def on_student_disconnected(self, student_id: str) -> None: ...
    def on_command_update(self, command: Command) -> None: ...                 # every lifecycle change of a core-bus command
    def incidents_unreviewed(self, student_id: str) -> int | None: ...         # T03 fills StudentCard.incidents_unreviewed
    def tick(self) -> None: ...                                                # ~every 0.5 s; must not block
    def close(self) -> None: ...
```

`FeatureContext` (what you may use):

| Field | Meaning |
|---|---|
| `db` | the server's SQLite (`execute`, `executemany`, `query`, `transaction()`); only your `t0N_*` tables |
| `data_dir`, `feature_dir(name)` | your own directory under the server data dir (clips, journals) |
| `publish(event, data)` | sends `feature_event` to every teacher socket; name events `t03.*` / `t04.*` / `t05.*` (the prefix becomes `feature`) |
| `submit_command(student_id, kind, payload, issued_by=, ttl_ms=)` | the T01 command bus (lifecycle, re-delivery, expiry, persistence); returns `Command` |
| `send_raw_to_student(student_id, message) -> bool` | send one contract-valid message on the live socket (validated against `ServerToStudentMessage`); for a role that runs its own lifecycle (T04 `ClassControl` transport) |
| `students()`, `student(id)`, `current_session_id()` | read the roster |
| `teacher(request)` | the authenticated `TeacherPrincipal` (the gate already rejected everyone else) |
| `student_from_request(request)`, `student_by_token(token)` | the student behind a `Bearer` token (T03 clip upload, `StudentResolver`) |
| `config`, `command_kinds` | read-only server config, the command kinds of this contract |

* A hook that raises is logged and isolated. The feature shows as `degraded` in `/api/teacher/info` and the server keeps
  running.
* A factory that fails shows as `failed`; a module that is missing shows as `not_installed`.
* Template: `classroom/server/tests/sample_feature.py`. Tests: `classroom/server/tests/test_simulator_features.py`.

### Role mapping onto the existing code (checked against the branch heads of 2026-10-08)

| Role | Your code today | Adapter shape (`<your path>/classroom_feature.py`) |
|---|---|---|
| T03 `classreview` | `create_review_router(store, teacher_guard, student_resolver, status_provider)`, `ReviewStore.ingest_incident`, `note_clip_requested`, `note_command_ack` | `router = create_review_router(store, teacher_guard=lambda r: ctx.teacher(r).teacher_id, student_resolver=lambda tok: (s := ctx.student_by_token(tok)) and s.student_id, status_provider=…)`; `on_student_message`: `incident` → `ingest_incident(..., class_session_id=ctx.current_session_id())`, `ack` → `note_command_ack`; clip request → `ctx.submit_command(sid, "request_clip", {"incident_id": …})` then `note_clip_requested(sid, iid, cmd.command_id)`; `incidents_unreviewed` from your store |
| T04 `proctor_classctl` | `ClassControl(transport=…, journal_path=…)`, `student_connected / handle_student_message / student_disconnected / tick`, `create_teacher_router(control, get_principal)` | `transport = ctx.send_raw_to_student`; `journal_path = ctx.feature_dir("exams") / "journal.jsonl"`; `on_student_connected` returns `control.student_connected(...)` (welcome.exam override); `get_principal = lambda r: Principal(ctx.teacher(r).teacher_id)`; acks of your own `command_id`s reach you through `on_student_message` (the core ignores acks it did not issue) |
| T05 `class-audio` | `AudioHub` (teacher/student online/offline, `on_teacher_message`, `on_student_ack`, `on_student_message`, `teacher_auth_lost`, `shutdown`) | needs teacher-socket hooks and claimed message types: **planned (checkpoint 2)**, see §5 |

## 4. Teacher UI plug-in API (`classroom/teacher-ui`) — **planned (checkpoint 2)**

The React/TypeScript shell gives you these, and you never edit them (T01 owns them):

* `teacher-ui/src/app` — routing and login;
* `teacher-ui/src/api` — the typed client over the generated types and the stream subscription with reconnect/backoff
  and seq-gap resync;
* `package.json` and its lockfile.

You add exactly one directory, `teacher-ui/src/features/<class|history|exams|audio>/index.ts`, which the shell
discovers with `import.meta.glob`:

```ts
export default {
  id: "history", owner: "T03", title_ru: "История",
  slots: ["route", "student-panel"],          // where the module appears
  mount(el: HTMLElement, ctx: FeatureContext): () => void,   // imperative (vanilla JS panels such as class-panel) …
  // … or component: React.ComponentType<{ ctx: FeatureContext }>
} satisfies FeatureModule;
```

`ctx` will carry the API client, `subscribe(type, handler)`, the selected student, and `navigate`. The existing vanilla
modules (T02 `class-panel`, T03 `classreview/ui/review-module.js`, T05 `class-panel-module.js`) are hosted through
`mount`, so none of them has to be rewritten.

## 5. Change requests

* Write `proctoring/handoffs/<you>/DEPENDENCIES.txt`: what, why, and which fixture or test shows it. T01 answers in
  `proctoring/handoffs/T01/STATUS.md` and changes the contract **additively**. A new field is optional, and a new
  message/kind is offered only to peers that announce it.
* Accepted and planned for checkpoint 2:
  * T05 `qorgau.class.audio.v1` (`PROTOCOL_AUDIO.md`): feature-claimed student and teacher message types (`audio_media`,
    `audio_request/update/stop`), a teacher-socket hook (`on_teacher_connected / on_teacher_message /
    on_teacher_disconnected`), `teacher_auth_lost` on logout, command kind `audio_update`.
  * T04 `/ws/teacher` `command_update` for T04 commands (through `ctx.publish("t04.command_update", …)`, already possible).
