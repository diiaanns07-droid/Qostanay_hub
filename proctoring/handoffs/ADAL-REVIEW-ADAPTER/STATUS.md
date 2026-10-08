# ADAL-REVIEW-ADAPTER

Branch: `codex/adal-review-adapter`; base `951cc8a`. Own paths: `proctoring/classreview/**` and this handoff. Integration/push belongs to root coordinator; no agent push requested.

## Checkpoint 1

Implemented production factory `classreview.classroom_feature:create_classroom_feature` (`name=history`, owner T03), authenticated allowlisted assets under `/api/teacher/history/assets/`, actual teacher/student guards, accepted C1 event backfill and incremental ingest, request lifecycle hooks before student send, persisted command/clip timeout states, and teacher-card unreviewed count.

Review schema v2 records immutable canonical C1 event provenance and maps event identities to local ordering keys transactionally. Raw transport seq never deduplicates accepted C1 events across runs. Episode source stays first-event source; each accepted event keeps own run/source/session. Clip headers cannot upgrade replay/synthetic/unknown evidence to live. History UI displays source labels.

Validation: existing store/router/media tests **69 passed** on Windows/Python 3.14 with explicit PYTHONPATH and external writable `--basetemp`. Initial default pytest temporary directory was inaccessible; rerun using external task scratch succeeded. Production C1 integration tests are next and not yet claimed.

Root integration requested: enable default feature spec with audio; map history UI to `/api/teacher/history/assets/register.js`; allow `media-src 'self' blob:`; refresh metadata on `feature_event` event `history.changed`. Adapter does not modify shared server or panel files.

Known limits: historical C1 log retains snapshot presence but omits JPEG bytes; unavailable old snapshot bytes cannot be recovered. Live hook persists exact accepted-envelope snapshot bytes. Hardware camera/microphone and OS restrictions are not part of these checks.

Next: real-process C1 auth/join/backfill/run-reset/decision/request/upload/range/provenance tests, then browser seek if feasible. Push status: not pushed (coordinator owns publication).

## Checkpoint 2 — resumed production verification

Added `classreview/tests/test_classroom_feature.py`: **4 real C1 subprocess tests passed** (9.32 s), covering authenticated join, accepted-event backfill, backend run sequence reuse and conflicts, immutable live/replay/synthetic/unknown provenance, request/upload/HTTP Range, snapshot persistence, decision audit history and restart, forged token/foreign student/path/oversize denial, allowlisted assets, and persisted upload expiry. No hardware or native guards were activated. Clips are burned-in synthetic fixtures.

The previously untested startup defect in `expire_requests` and ignored command issue timestamp are fixed by upstream `a097ac1`; reuse that version through coordinator baseline `90ce179` rather than duplicate the correction. Initial sandbox execution could not launch child servers or access default temporary directories; scoped escalated localhost tests with TEMP/TMP and pytest basetemp inside task scratch worked. One existing harness shutdown ping race produced a `ConnectionClosedOK` thread warning after successful checks; no feature hook errors occurred.

Next: merge coordinator baseline and test actual Chrome playback/seeking under production panel CSP and same-origin login, then rerun relevant review tests. Checkpoint is local; publication remains the coordinator's responsibility.
