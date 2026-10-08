# T01 — STATUS (class server C1, contracts, coordination)

Role: T01, coordinator of the Qorgau Classroom server side. In the global `proctoring/coordination/OWNERSHIP.json` this
is role **C1**, path `proctoring/classroom/`.

* **Branch:** `codex/proctor-T01`.
* **Base:** `codex/proctor-integration` @ `64354c014c4ad51054e8bb67fe841580771d43e5`.
  * It contains the frozen `qorgau.class.v1` (65c8c17) and T02 @ 1363e3c, T03 @ ab3faaf, T04 @ 7518724, all integrated by
    A01.
  * C2 (`codex/class-C2`) is built on the same commit.
  * There is no `main` on the remote, and the integration branch is the captain's.
  * Not in the base yet: T03 37d7078, T05 574136f, C2 d6752e6.
* The SHA of this checkpoint is reported in the final message, because a commit cannot contain its own SHA.
* Checkpoints: 1 = `668219b` (server, contracts, ownership). 2 = this commit (minimal C1 chain).

## Checkpoint 2 — minimal C1 chain: C2 → student in the T02 REAL panel → event
* The server hosts the T02 class panel as-is.
  * `--ui auto`: the React shell when built, otherwise `proctoring/class-panel`.
  * `/config.json` answers `{"adapter":"real"}`.
  * `/login` is a server-rendered PIN form; `/` redirects there without the cookie.
  * Previews are inline for this panel (v1 adapter; DEPENDENCIES D5).
* `StudentCard` is now **flat**, with the v1 field names that T02 `normalizeStudent` reads. The nested shape of
  checkpoint 1 was replaced before anyone consumed it, and the contract is frozen from here on (additive changes only).
* v1 dedup follows §3.1 `(student_id, seq)`. C2 re-sends an open episode with `clip_available:true` under a new `seq`;
  that is now stored as an update instead of being swallowed as a duplicate (test added).
* `Ack.seq` was added (C2 D4). `ctx.student_by_token` was added for the T03 `StudentResolver`.
* `Referrer-Policy: same-origin`, because with `no-referrer` Chromium sends `Origin: null` on the login form POST.
* Browser chain check `classroom/server/tests/chain/chain.e2e.mjs`. It runs the **real C2 uplink code** (codex/class-C2
  @ d6752e6, `proctor.uplink.client.Uplink`) with a synthetic, labelled data source instead of the camera/CV backend.
  It goes through the T01 server and the T02 panel in headless Chromium 141, and gets **12/12 PASS**:
  * login redirect;
  * wrong PIN refused;
  * REAL mode;
  * join code;
  * the C2 student appears;
  * online;
  * episode counter = 1;
  * C2 preview on the card;
  * episode text in the drawer;
  * the event is stored once with `event_id`, `event_time`, `received_at`;
  * C2 exit 0;
  * no browser errors.

## Checkpoint 1 — what runs (still true)
* `python -m classroom.server` (RUN.md):
  * pairing by a 6-digit code, with resume tokens and supersede (4409);
  * a teacher PIN → HttpOnly/SameSite=Strict cookie, teacher API on loopback only, Host/Origin checks, brute-force limits
    for both the PIN and join codes;
  * heartbeat: ping every 5 s, 15 s of silence → offline + grey (close 4408); grey also on a stale status or camera≠ok;
  * event intake with dedup (§3.1 `(student_id, seq)` + v1.1 `event_id`, seq conflicts kept and flagged, closed episodes
    never reopen);
  * SQLite (WAL) persistence that survives a restart and `kill -9`;
  * the command bus (sent ≠ executed, unconfirmed after 10 s, expiry, re-delivery with `attempt+1`, late ack);
  * the teacher stream `/ws/teacher` (seq, resync on overflow, previews as metadata + URL);
  * the audio signaling relay bound to the live audio session;
  * feature plug-ins with route and table reservations per role.
* `python -m classroom.simulator`: labelled SIMULATED students.
* Contracts `qorgau.classroom` 1.0.0: Pydantic models → JSON Schema + TS types + 36 fixtures.
  * Covered: Student, DeviceStatus, Session, ObservationEvent, Incident, ClipMetadata, ExamPolicy, Command, CommandAck,
    AudioSession, every student/teacher wire message.
* Ownership: `classroom/coordination/OWNERSHIP.json` + `verify_ownership.py`.

## Checks (Linux container, Python 3.12, .venv from requirements)
* `python -m classroom.contracts.generate --check` → up to date.
* `pytest classroom/` → **111 passed** at checkpoint 2 (108 at checkpoint 1 + 3 panel-serving tests). The checkpoint 1 breakdown:
  * 63 contract tests;
  * 45 real-process server tests: registration, client isolation, reconnect/resume, dedup, persistence after a graceful
    restart and after `kill -9`, commands, heartbeat, audio, simulator, feature isolation, migrations.
* `verify_ownership.py --self-test` → PASS.

## Limits (honest)
* Not run on Windows, not over a real LAN or Wi-Fi, not with real cameras.
* **The simulator proves protocol handling, not 100 cameras.**
* The C2 chain uses C2's real network client, but the student data is synthetic. It is not the full student app
  (Electron, camera, CV), and it was not run on Windows or over a LAN.
* No session UI yet: create the session with `POST /api/teacher/session` (curl in RUN.md) until the T04 exams module is
  mounted.
* T03/T04/T05 modules are not mounted yet (adapters are next). The React teacher-ui shell is not built yet.

## Next (in this order)
1. Mount T03 (history, clips, decisions), T04 (exams/session UI, commands), T05 (audio) through thin feature adapters,
   plus their T02 panel modules (`window.QorgauClassPanelModules`).
2. React/TS teacher-ui shell with FeatureModule slots (INTERFACES.md §4).
