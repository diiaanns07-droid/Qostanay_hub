# T04 — STATUS (exam sessions, policies, teacher commands)

Role: T04, exam management and teacher commands. Branch `codex/proctor-T04`, base `65c8c17` (qorgau.class.v1; the T01
baseline is not published, see DEPENDENCIES R1). Previous checkpoint: `751872439a379a948793242fb0d5b554e3a70912`.
Stage: **checkpoint 2 — teacher UI + student-client instruction** on top of the server-side core.

## Delivered
* `proctoring/backend/proctor_classctl/` (checkpoint 1): `ClassControl` facade; exams + policies («Внешний сайт»: start URL,
  allow-list grammar, sign-in domains with explanation; «Отдельная программа»: exe allow-list), versions, revision conflicts;
  assignment of a policy to selected students respecting client capabilities; commands start_exam / lock(reason) / unlock /
  finish_exam / apply_policy with command_id, expires_at + ttl_ms, client ack, command_progress, idempotent teacher requests,
  superseding, cancel, re-delivery with the same id after reconnect, expired commands never delivered; per-exam roles;
  append-only journal (JSONL); FastAPI router `/api/teacher/control/*`; SIMULATOR of student clients; loopback DEV server.
* `proctoring/class-control-ui/` (checkpoint 2): teacher page (exam create/edit, policies, assignment, students table with
  the five command states, lock label only from the client, unavailable actions disabled with reasons, lock dialog with the
  site-timer note, cancel, history), journal tab, SIMULATOR panel, connection-loss banner, T02 slot module `t02-module.js`.
  `API_NOTES.md` lists the API gaps found while building it.
* `handoffs/T04/STUDENT_CLIENT.md`: exact command rules for the student client (dedup by command_id, double expiry check,
  ack only by fact, one ack + command_progress, capabilities, per-kind semantics, URL matching, offline queue, checklist).

## Checks (Linux container, Python 3.12.3, Node 22.22.0, Chromium via global Playwright 1.56.1)
| Command | Result |
|---|---|
| `cd proctoring && .venv/bin/python -m pytest -q backend/proctor_classctl` | 73 passed |
| `cd proctoring && .venv/bin/python -m pytest -q` (whole repo) | 986 passed, 36 skipped (baseline 913 + 73) |
| `cd proctoring/class-control-ui && node --test tests/*.test.mjs` | 35 passed |
| `cd proctoring/class-control-ui && NODE_PATH=$(npm root -g) node tests/e2e.cjs` | 22 passed, 0 failed (real Chromium + DEV server + simulator) |

## Not verified / open
* No real class server (T01/C1) or student client exists yet: everything runs against the SIMULATOR; nothing is shown
  to work on real student computers. Windows restrictions and the lock screen are the student-side developer's job.
* An adversarial review of the core is in progress (findings about stale lost/unconfirmed commands pinning the lock card,
  a malformed student message breaking the teacher API, and resume-before-old-socket-close). Fixes go into checkpoint 3.
* Protocol additions (expiry fields, command_progress, capabilities, apply_policy) await T01 approval (DEPENDENCIES R2).
* No external-site timer integration exists: the UI shows "pause site timer" as unavailable and says so.

## Integration (for T01)
Mount per DEPENDENCIES R3; serve `class-control-ui/` at `/ui/` and the page at e.g. `/control/` (API_NOTES A9).
