# Client recovery: stale resume token and renderer state snapshot

Branch: `codex/classroom-client-recovery`.
Base: `23c6c3b` (classroom acceptance integration).
Full delivery SHA is reported after commit/push.

## Delivered

1. `proctor.uplink.client.Uplink._handshake` handles C1's `resume_rejected`
   alongside the older `join_rejected`. A rejected stored token is cleared and
   the next connection uses the configured join code. A rejected join code still
   terminates attempts; rate limiting does not discard the stored identity.
2. `proctor.app.StreamHub` retains the latest **global** `class_state` on its
   event loop, even with no subscribers. New/refreshing renderer subscriptions
   receive the usual `hello` followed by this snapshot, then live updates.
   No polling or timing sleeps; subscription and snapshot replay are ordered on
   the same loop. Other events are not retained. Existing authentication,
   envelope shape, sequence numbering and session filters are unchanged.

The root integrator explicitly assigned the small `app.py` StreamHub change;
no C1 server, contracts, shared dependencies or student UI files were edited.

## Validation

Windows, Python 3.12, project full-dependency venv. `PYTHONPATH` points to this
worktree's `backend`, `contracts/python`, and `proctoring`, in that order.

- Added `backend/proctor/uplink/tests/test_recovery.py`.
- Before source fixes: **3 failed, 2 passed**. Failures reproduce C1 error-code
  mismatch and absent initial/refresh state snapshots.
- After fixes: **5 passed**.
- Combined suite: **51 passed**, one existing FastAPI TestClient deprecation
  warning, in 35.34 seconds:

  ```text
  python -m pytest backend/proctor/uplink backend/tests/test_lifecycle_api.py backend/tests/test_qa_regressions.py -q -p no:cacheprovider
  ```

Tests use isolated temporary directories (explicit `--basetemp` on this host).
The C2 suite exercises the full local backend in synthetic mode against its
fake class server. Snapshot tests use the actual StreamHub and worker-thread
publication. No camera, microphone, Electron UI or native hooks were activated.

## Remaining integration limits

- A successful C2 lock/audio acknowledgement still reflects its internal flags,
  not an Electron execution/media acknowledgement. Snapshot recovery fixes
  delivery of those flags; it does not prove enforcement or audio capture.
- Synthetic provenance remains unresolved: C2 joins before a local session is
  created, sends a fixed normal app version and no `hello.simulated`, and may
  change local source mode while retaining the connection. C1 then stores the
  connection's origin as real. Merely hard-coding `simulated=true` would mislabel
  live sessions. Coordinate a per-session/source provenance field and handling
  with C1/A01, or a deliberately restricted synthetic process mode. Until then
  acceptance clients/results must explicitly say SYNTHETIC. No origin contract
  was silently changed in this fix.
- The existing outbox is preserved on rejoin, as before; policy for old queued
  events when a student moves to a different classroom session needs a separate
  coordinator decision. Use fresh student data directories for independent
  acceptance scenarios; do not claim cross-class event isolation from this fix.
- C1 adapters for T03/T04/T05 remain the active T01 author's responsibility.

## Next verification by integrator

Merge the delivery commit. Run the real C1 with two independent full backends in
synthetic mode, using unique data directories. Verify join, start, events,
previews, independent finish and reconnect. Subscribe/refresh `/v1/stream`
after C2 is already connected and confirm `class_state` follows `hello`.
Finally test stale token -> current valid join code using the real C1.
