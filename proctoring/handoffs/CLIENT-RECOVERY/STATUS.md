# Client recovery: stale resume token and renderer state snapshot

Branch: `codex/classroom-client-recovery`.
Base: `23c6c3b` (classroom acceptance integration).
Full delivery SHA is reported after commit/push.

Latest checkpoint: source provenance (below). Recovery checkpoint was
`3badef26adf9727eb86f7d10a9c2d17c4cf2b5f5`, pushed successfully.

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
- Source provenance was unresolved at the recovery checkpoint; the subsequent
  coordinator-approved provenance checkpoint below fixes it without a forced
  test-process flag. Acceptance remains synthetic, not a camera quality test.
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

## Subsequent checkpoint — source provenance

The root coordinator explicitly approved additive classroom contracts plus
bounded C1 provenance handling and preview header changes. T01 feature adapters
remain untouched. The local backend `proctor_contracts.v1` schema is unchanged.
Details are in `classroom/contracts/CHANGELOG.md`.

- Teacher contract `qorgau.classroom` 1.1.0; unchanged `qorgau.class.v1` wire with
  optional extension 1.2 fields `source_mode` and `source_session_id` on hello,
  status, incident and preview.
- C2 starts unknown. It derives current status source from the local session,
  queued incident source from the incident, and preview source/time from frame
  metadata. These remain independent across source transitions.
- C1 exposes origins `unknown`, `simulated`, `replay`, `real`; real requires
  an explicit live source declaration, not just connection/camera status.
  This is provenance from the client, not hardware attestation.
- Initial/legacy-unmarked clients remain unknown. The original explicit
  simulator hello marker/version prefix still marks its data simulated.
- Card origin changes are persisted; resume without current source does not
  reuse an old live claim. Events retain their own origin through offline
  delivery, irrespective of newer status. Preview stream and HTTP header use
  stored frame origin, not current card origin.
- No database schema migration. Historical records are not retroactively
  rewritten because their original source cannot be reconstructed reliably.

Validation on Windows/Python 3.12:

- Added provenance regressions before implementation: **7 failed, 2 passed**.
- Same focused regressions after implementation: **9 passed**.
- `pytest classroom backend/proctor/uplink -q -p no:cacheprovider`: **150 passed**
  in 59.23 seconds. Warnings: existing TestClient deprecation and a close/pong
  race in the existing classroom test client (ConnectionClosedOK after normal
  shutdown in test_expired_command_is_never_delivered). No assertion failed.
- Full C2 backend synthetic test additionally checks the outgoing incident,
  status and preview fields against the actual local session ID/source.
- Generated schema and **42 fixtures** pass `generate --check` and Pydantic /
  JSON Schema validation. Generated TS passes standalone strict tsc noEmit.
- No camera/microphone/native-hook execution.

Integration notes:

1. Root owns the panel changes: its normalization/rendering must support
   unknown and replay, keeping simulated visibly distinct from live.
2. T03 standalone currently does not expose origin/source metadata. T01's
   future feature adapter must preserve the new per-event fields when mounting
   T03; this checkpoint does not alter that active author's files.
3. Re-run the root's complete C1 + two full synthetic-backend acceptance. It
   should now see simulated on cards, incidents, event records and preview HTTP
   headers without any special synthetic environment flag.

## Subsequent checkpoint — shell integration test after session finish

Root assigned `desktop/main/src/__tests__/backend.integration.test.ts` to fix a
reproduced stale expectation. No shell or backend production code changed.

The test expected `focus_lost` immediately after finish to be rejected and
counted in `EnvironmentEventQueue.dropped`. Backend `SessionRuntime` explicitly
accepts final `focus_lost`, `focus_regained`, `exam_mode_released` for 5000 ms
after finish/abort (`LATE_ENV_ACTIONS`, `LATE_ENV_GRACE_MS`, `environment_events`;
A06 #6) so those final observations reach the report. The emitted focus event
was correctly accepted, yielding dropped=0 rather than 1.

The negative-path assertion now emits `shortcut_ctrl_v`, which is forbidden
after finish both inside and outside that grace period. It still asserts an
empty queue and exactly one dropped event; no delay, weaker count or production
behavior change was added. The QA scenario independently uses `shortcut_ctrl_c`
for the same post-finish 409 check.

Validation: bundled this worktree's source using the pinned esbuild from the
integration checkout, with `nodePaths` for its existing dependencies. Explicit
`QORGAU_PYTHON` uses the full dependency venv; `PYTHONPATH` points to this worktree.
Real backend, synthetic sessions, FakeGuard; no Electron/device/native hooks.

- Before: backend integration file **10 passed, 1 failed, 1 skipped**; identical
  compiled line 6530, actual dropped=0 expected 1.
- After: **11 passed, 0 failed, 1 skipped** in 5.60 seconds. The skip is the
  existing non-Windows SIGTERM-ignoring process test.
- Expected negative-path logs (missing Python/READY timeout) remain assertions
  inside passing tests, not unexpected process failures.

Root should merge this checkpoint and rerun its complete shell suite.

## Subsequent checkpoint — refuse audio until the media endpoint exists

Base: clean worktree fast-forwarded to integration `6a2e0ac`. Root authorized
only the audio truthfulness fix from upstream C2 `73f3b7a`; no device access,
guard changes or server adapter work. This checkpoint is local for root to
publish as part of the integrated branch.

`audio_start` previously returned success and raised `mic_active` immediately,
despite no WebRTC endpoint, microphone track or renderer confirmation. The
uplink now rejects `audio_start` and `audio_update` with `ok:false`,
`code:unsupported`, the T05 additive `error_code:not_supported`, and a clear
Russian explanation. `audio_stop` remains idempotently successful. Published
and wire state remain `mic_active:false`, with no audio direction.

Only this semantic fix was ported; upstream's periodic 5-second class-state
republishing was not copied. Existing sticky replay, resume-token recovery and
per-message provenance are preserved. Lock acknowledgements still precede
renderer confirmation: that separate gap is unchanged and was reported to root.
There is still no live audio feature; this fix prevents its false appearance.

Validation (existing Python 3.12 dependency environment, explicit PYTHONPATH
to this checkout, loopback fake server and synthetic inputs, no devices):

- Audio regressions before production edit: **7 failed** (false start success
  and missing T05 error on update). After: **7 passed**.
- Six parameter combinations cover start/update and listen/talk/both, status
  and renderer events never claiming microphone activity, and idempotent
  command redelivery. The existing lock/unlock/audio-stop checks remain.
- Full `backend/proctor/uplink` plus C1 `test_persistence_audio.py`: **32 passed**
  in 35.00 s, including recovery/provenance and actual synthetic backend tests.
  Warnings: existing Starlette TestClient deprecation and test-harness pong
  after normal socket close (`ConnectionClosedOK`) during C1 restart.
- Initial pytest attempt hit the sandbox's denied shared Temp directory;
  reruns use fresh unique basetemp directories under the writable scratch root.
