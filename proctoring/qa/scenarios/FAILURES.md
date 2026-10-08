# Failure scenarios (prompt item 2) — expected behaviour, how it is checked, status

Owner: A09. Expected behaviour is taken from `coordination/CONTRACTS.md` (frozen v1). "Auto" = executable in
`qa/tests` today against a real backend process. "Candidate" = to run on A01's integration SHA with the real
modules. "Manual" = needs Windows / camera / hardware (procedure in `WINDOWS.md`).

| ID | Failure | Expected (contract) | Check | Status @35bea4c |
|---|---|---|---|---|
| F1 | Camera absent / wrong index | live preflight `camera=fail` with a `CAMERA_*` code, `ready=false`; no fallback to synthetic; session can be aborted; new session possible | Auto: live preflight without capture module (`test_lifecycle_matrix`, A01 smoke). Candidate: `QORGAU_CAMERA_INDEX=15`. Manual W-FAIL-1 | PASS (module-missing path); real camera BLOCKED (A02) |
| F2 | Camera unplugged mid-exam | `HealthObservation` (capture degraded) on stream, coverage gap in summary, **no** `face_missing` episode from missing frames; recovery or clear error when replugged | Candidate: unplug USB camera during replay/live. Manual W-FAIL-2 | BLOCKED (A02/A05) |
| F3 | Camera busy (other app) | `CAMERA_BUSY` in preflight, retryable | Manual W-FAIL-3 (open Windows Camera app first) | BLOCKED |
| F4 | Model weights missing | analyzer `load()` → health `UNAVAILABLE`/`model_missing`, backend starts; live preflight `phone_model`/`face_model` FAIL (required); no download attempt | Candidate: start with empty `QORGAU_MODELS_DIR` under the network guard | BLOCKED (A03/A04) |
| F5 | Model weights corrupted / checksum mismatch | `MODEL_INVALID`, manifest SHA256 mismatch reported, never silently used | Candidate: truncate the `.onnx`/`.task` copy in a temp models dir | BLOCKED (A03/A04) |
| F6 | Backend crashes (killed) mid-exam | shell detects loss, releases all restrictions; restart works; port freed; no stale active session | Auto (backend side): `test_crash_mid_exam_then_restart_on_same_data_dir`. Manual W-ENV-11 (keyboard/focus back after shell/backend kill) | Backend PASS (Linux); shell NOT RUN |
| F7 | Parent (Electron) dies | backend sees stdin EOF, aborts the active session, exits ≤ ~5 s | Auto: `test_stdin_eof_during_running_exam_aborts_and_exits`, `test_shutdown_line_stops_backend` | PASS |
| F8 | SQLite unavailable (read-only dir, locked, disk full) | `STORAGE_ERROR` (503), storage preflight FAIL for live, no silent data loss, exam not started on a store that cannot write | Candidate: `QORGAU_DATA_DIR` = read-only dir / file path; DB locked by a second connection | BLOCKED (A08) |
| F9 | Port busy | ephemeral port 0 avoids conflicts; an explicit busy port fails fast, non-zero exit, no READY | Auto: `test_port_already_in_use_fails_fast_without_ready`, `test_ephemeral_port_avoids_busy_port` | PASS (Linux; Windows `SO_EXCLUSIVEADDRUSE` path NOT RUN) |
| F10 | Stale results | observation older than consumer TTL → `unknown/stale`, not "no violation"; UI shows result age | Candidate + fault injection (delayed analyzer) | NOT RUN (needs A05/A07) |
| F11 | Slow inference | latest-frame mailbox drops stale frames (counted as skipped), API and preview stay responsive, latency reported honestly | Candidate: CPU-throttled run (`protocols/PERFORMANCE.md`); fault injection planned | NOT RUN |
| F12 | Invalid payloads | 4xx `ApiError`, never 500, backend stays usable | Auto: `test_payload_negative.py` (≈100 cases) | **FAIL**: QA-BUG-002 (over-long ids → 500 + connection reset), QA-BUG-003 (4xx codes returned as HTTP 500) |
| F13 | Replay instead of live | replay is a separate, visibly labelled mode (`source_mode=replay` on every record); a session cannot change mode; replay without A02 fails preflight | Auto: replay create/validation (`test_payload_negative`), A01 live/replay no-fallback tests. Candidate: label check on every observation/incident/report | PASS (validation + no-fallback); labels on real replay BLOCKED (A02) |
| F14 | Finish during an open episode | episode closed with `end_reason=session_finished`, stored, update_seq increases | Auto: `test_finish_during_open_phone_episode_closes_it_with_session_finished` (+ abort, pause) | PASS (synthetic) |
| F15 | Corrupt exam file | backend fails visibly (non-zero exit, no READY) instead of running a wrong exam | Auto: `test_corrupt_exam_file_fails_visibly` | PASS |
| F16 | Missing exam file | falls back to the labelled demo fixture exam (`is_demo=true`) and logs it | Auto: `test_missing_exam_file_falls_back_to_labelled_demo_exam` | PASS (behaviour as designed; A10 must ship `demo/exams/demo_exam.json`) |
| F17 | Slow / stuck WebSocket client | bounded per-client queue, seq gap visible, REST unaffected | Auto: `test_slow_stream_client_does_not_block_api` | PASS |
| F18 | Concurrent requests (double click finish/create) | exactly one session; finish idempotent, `finished_at` not rewritten | Auto: `test_concurrent_*` | PASS |
| F19 | Network unavailable at runtime | everything works offline after preparation; zero outbound attempts | Auto: `test_offline.py` (audit guard; Linux netns). Manual W-OFF-1 on Windows | PASS (synthetic); CV + Electron NOT RUN |
| F20 | Environment protection crash (shell) | keyboard hooks/kiosk released, focus returned, emergency exit works | Manual W-ENV-11/12 | NOT RUN (A06) |

Fault injection for F2/F4/F5/F8/F10/F11 before the real modules land: planned as QA-only fake modules that
implement the public factories (`proctor.capture.create_capture_service`, …) and are injected into a REAL
`python -m proctor serve` process via `sys.modules` (no backend file is modified). They test A01's composition
handling of module failures only and will be labelled as fault-injection doubles, never as module results.
