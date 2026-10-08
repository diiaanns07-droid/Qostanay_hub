# Failure scenarios (prompt item 2) — expected behaviour, how it is checked, status

Owner: A09. Expected behaviour is taken from `coordination/CONTRACTS.md` (frozen v1). "Auto" = executable in
`qa/tests` today against a real backend process. "Candidate" = to run on A01's integration SHA with the real
modules. "Manual" = needs Windows / camera / hardware (procedure in `WINDOWS.md`).

| ID | Failure | Expected (contract) | Check | Status @35bea4c |
|---|---|---|---|---|
| F1 | Camera absent / wrong index | live preflight `camera=fail` with a `CAMERA_*` code, `ready=false`; no fallback to synthetic; session can be aborted; new session possible | Auto: module missing (A01 smoke) + fault injection `open_fails:CAMERA_UNAVAILABLE`, `no_frames`, `factory_raises` (`test_fault_injection`). Candidate: `QORGAU_CAMERA_INDEX=15`. Manual W-FAIL-1 | PASS (composition, doubles); real camera BLOCKED (A02) |
| F2 | Camera unplugged mid-exam | `HealthObservation` (capture degraded) on stream, coverage gap in summary, **no** `face_missing` episode from missing frames; recovery or clear error when replugged | Auto (composition): `disconnect_after` double → HealthObservation `camera_disconnected`, session stays controllable. Candidate: unplug USB camera. Manual W-FAIL-2 | PASS (health path, doubles); gap/no-face_missing BLOCKED (A02/A05/A08) |
| F3 | Camera busy / denied | `CAMERA_BUSY` / `CAMERA_DENIED` in preflight, retryable | Auto (composition): `open_fails:CAMERA_BUSY|CAMERA_DENIED`. Manual W-FAIL-3 (Windows Camera app open; privacy setting off) | PASS (doubles); Windows NOT RUN |
| F4 | Model weights missing | analyzer `load()` → health `UNAVAILABLE`/`model_missing`, backend starts; live preflight `phone_model`/`face_model` FAIL (required); no fallback; synthetic demo still labelled | Auto (composition): `model_missing` doubles. Candidate: empty `QORGAU_MODELS_DIR` under the network guard | PASS (doubles); real A03/A04 BLOCKED |
| F5 | Model weights corrupted / checksum mismatch | `MODEL_INVALID`, manifest SHA256 mismatch reported, never silently used | Auto (composition): `load_raises` double → backend up, `init_error`, preflight FAIL. Candidate: truncated `.onnx`/`.task` copy | PASS (doubles); checksum check BLOCKED (A03/A04) |
| F6 | Backend crashes (killed) mid-exam | shell detects loss, releases all restrictions; restart works; port freed; no stale active session | Auto (backend side): `test_crash_mid_exam_then_restart_on_same_data_dir`. Manual W-ENV-11 (keyboard/focus back after shell/backend kill) | Backend PASS (Linux); shell NOT RUN |
| F7 | Parent (Electron) dies | backend sees stdin EOF, aborts the active session, exits ≤ ~5 s | Auto: `test_stdin_eof_during_running_exam_aborts_and_exits`, `test_shutdown_line_stops_backend` | PASS |
| F8 | SQLite unavailable (read-only dir, locked, disk full) | `STORAGE_ERROR` (503), storage preflight FAIL for live, no silent data loss, exam not started on a store that cannot write; a write failure during the exam must not stop detection and must be visible | Auto (composition): `open_raises`, `open_unavailable` (PASS); `record_raises` mid-exam (**FAIL**: QA-BUG-004, QA-BUG-005). Candidate: read-only `QORGAU_DATA_DIR`, locked DB | **FAIL** (QA-BUG-004/005); real SQLite BLOCKED (A08) |
| F9 | Port busy | ephemeral port 0 avoids conflicts; an explicit busy port fails fast, non-zero exit, no READY | Auto: `test_port_already_in_use_fails_fast_without_ready`, `test_ephemeral_port_avoids_busy_port` | PASS (Linux; Windows `SO_EXCLUSIVEADDRUSE` path NOT RUN) |
| F10 | Stale results | observation older than consumer TTL → `unknown/stale`, not "no violation"; UI shows result age | Candidate + fault injection (delayed analyzer) | NOT RUN (needs A05/A07) |
| F11 | Slow inference | latest-frame mailbox drops stale frames (counted as skipped), API and preview stay responsive, latency reported honestly | Auto (composition): `slow:1.5` phone double → `/health` p95 < 0.5 s, finish < 10 s. Candidate: CPU-throttled run (`protocols/PERFORMANCE.md`) | PASS (API side, doubles); real mailbox/latency BLOCKED (A02) |
| F12 | Invalid payloads | 4xx `ApiError`, never 500, backend stays usable | Auto: `test_payload_negative.py` (≈100 cases) | **FAIL**: QA-BUG-002 (over-long ids → 500 + connection reset), QA-BUG-003 (4xx codes returned as HTTP 500) |
| F13 | Replay instead of live | replay is a separate, visibly labelled mode (`source_mode=replay` on every record); a session cannot change mode; replay without A02 fails preflight | Auto: replay create/validation (`test_payload_negative`), A01 live/replay no-fallback tests. Candidate: label check on every observation/incident/report | PASS (validation + no-fallback); labels on real replay BLOCKED (A02) |
| F14 | Finish during an open episode | episode closed with `end_reason=session_finished`, stored, update_seq increases | Auto: `test_finish_during_open_phone_episode_closes_it_with_session_finished` (+ abort, pause) | PASS (synthetic) |
| F15 | Corrupt exam file | backend fails visibly (non-zero exit, no READY) instead of running a wrong exam | Auto: `test_corrupt_exam_file_fails_visibly` | PASS |
| F16 | Missing exam file | falls back to the labelled demo fixture exam (`is_demo=true`) and logs it | Auto: `test_missing_exam_file_falls_back_to_labelled_demo_exam` | PASS (behaviour as designed; A10 must ship `demo/exams/demo_exam.json`) |
| F17 | Slow / stuck WebSocket client | bounded per-client queue, seq gap visible, REST unaffected | Auto: `test_slow_stream_client_does_not_block_api` | PASS |
| F18 | Concurrent requests (double click finish/create) | exactly one session; finish idempotent, `finished_at` not rewritten | Auto: `test_concurrent_*` | PASS |
| F19 | Network unavailable at runtime | everything works offline after preparation; zero outbound attempts | Auto: `test_offline.py` (audit guard; Linux netns). Manual W-OFF-1 on Windows | PASS (synthetic); CV + Electron NOT RUN |
| F20 | Environment protection crash (shell) | keyboard hooks/kiosk released, focus returned, emergency exit works | Manual W-ENV-11/12 | NOT RUN (A06) |

| F21 | Fusion engine raises on every observation | session stays controllable (pause/resume/finish), observations still published, failure **visible** | Auto: `consume_raises` double | controllable PASS; visibility **FAIL** (QA-BUG-005) |
| F22 | Fusion engine raises in `finish()` | session still reaches `finished`, camera owner released | Auto: `finish_raises` double | PASS |

Fault injection (`qa/qorgau_qa/fakes.py`, `qa/tests/test_fault_injection.py`): QA-only doubles implement the public
factories (`proctor.capture.create_capture_service`, …) and are injected into a REAL `python -m proctor serve`
process via `sys.modules` (no backend file is modified). They test A01's composition handling of module failures
only, every record is labelled `qa.fake_*` / "QA FAULT-INJECTION DOUBLE", and they are never module or CV results.
On the candidate the same doubles can replace a single module (e.g. only `capture`) while the others are real.
