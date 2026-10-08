# T04 — STATUS (exam sessions, policies, teacher commands)

Role: T04, exam management and teacher commands. Branch `codex/proctor-T04`, base `65c8c17` (qorgau.class.v1; T01
baseline not published, see DEPENDENCIES R1). Stage: **checkpoint 1 — server-side core + simulator + dev server.**
UI (checkpoint 2) and the student-client instruction (STUDENT_CLIENT.md) follow.

## Delivered (checkpoint 1)
* `proctoring/backend/proctor_classctl/`: `ClassControl` facade, exams + policies (url/app, allow-lists, sign-in
  domains, versions, revision conflicts), assignment of a policy to selected students, commands (command_id,
  expiry, client confirmation, idempotency, superseding, cancellation, reconnect re-delivery), per-exam roles,
  append-only journal (JSONL), FastAPI router `/api/teacher/control/*`, SIMULATOR of student clients, loopback DEV server.

## Checks
| Command (from `proctoring/`) | Result |
|---|---|
| `.venv/bin/python -m pytest -q backend/proctor_classctl` | 73 passed |
