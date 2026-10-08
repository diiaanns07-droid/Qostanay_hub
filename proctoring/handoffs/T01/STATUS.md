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

## Checkpoint 1 — what runs
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
* `pytest classroom/` → **108 passed**:
  * 63 contract tests;
  * 45 real-process server tests: registration, client isolation, reconnect/resume, dedup, persistence after a graceful
    restart and after `kill -9`, commands, heartbeat, audio, simulator, feature isolation, migrations.
* `verify_ownership.py --self-test` → PASS.

## Limits (honest)
* Not run on Windows, not over a real LAN or Wi-Fi, not with real cameras.
* **The simulator proves protocol handling, not 100 cameras.**
* Not yet run end-to-end with the real C2 uplink or the T02 REAL panel. That is the next checkpoint.
* T03/T04/T05 modules are not mounted yet (adapters are next). The React teacher-ui shell is not built yet.

## Next (in this order)
1. Minimal C1 chain: C2 connects → student in the T02 REAL panel served by the server (login page, `config.json` real)
   → incident in the panel.
2. Mount T03 (history, clips), T04 (exams, commands), T05 (audio) through thin feature adapters.
3. React/TS teacher-ui shell with FeatureModule slots (INTERFACES.md §4).
