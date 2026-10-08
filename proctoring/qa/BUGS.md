# Reproducible findings for module owners (A09 does not patch other modules)

Found on product SHA `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (A01 BOOTSTRAP), Linux x86_64, Python 3.12.3.
Each item has a minimal repro against a real `python -m proctor serve` process and a tracking test in `qa/tests`
marked `xfail(strict=True)` — when the fix lands the test turns XPASS → FAIL, and A09 removes the marker.
`<T>` = the bearer token, `<P>` = port from the READY line, `<SID>` = a session id.

---

## QA-BUG-003 — 4xx error codes are returned with HTTP 500 · owner A01 · severity **medium**

`ProctorError` raised directly (not via a subclass) keeps `http_status = 500`, so contract 4xx codes arrive as 500:

| Trigger | Body code | HTTP now | Contract (CONTRACTS.md §1) |
|---|---|---|---|
| `POST /v1/sessions` with `consent.accepted=false` | INVALID_ARGUMENT | 500 | 422 |
| `POST /v1/sessions` with unknown `exam_id` | INVALID_ARGUMENT | 500 | 422 |
| `POST /v1/sessions/<SID>/environment/events` with `session_id` ≠ URL | SESSION_MISMATCH | 500 | 409 |
| (by code reading) `MODULE_NOT_INTEGRATED` raised in `session.py` | MODULE_NOT_INTEGRATED | 500 | 503 |

Repro: `curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer <T>' -H 'Content-Type: application/json'
-d '{"source":{"mode":"synthetic"},"exam_id":"demo-exam-1","consent":{"accepted":false,"text_version":"v","accepted_at":"2026-10-08T09:00:00Z"}}'
http://127.0.0.1:<P>/v1/sessions` → `500`.
Impact: clients (A06/A07) that branch on status treat a user error as a backend crash (retry loops, wrong UI).
A01's `test_consent_required` only asserts `>= 400`, so it does not catch this.
Suggested fix: derive the HTTP status from `ErrorCode` in the `ProctorError` handler (one table per §1) or raise
`InvalidArgument`/`InvalidStateError`/`ModuleUnavailable` subclasses. Tracking:
`qa/tests/test_payload_negative.py::test_error_status_matches_contract`.

## QA-BUG-002 — over-long path ids → 500 INTERNAL and a reset keep-alive connection · owner A01 (same rule for A08) · severity **medium-low**

Path parameters (`session_id`, `incident_id`, `question_id`) are not checked against the contract `Id`
(`^[A-Za-z0-9._:-]{1,128}$`).
* `GET /v1/sessions/<990+ chars>` → the 404 message `session … not found` exceeds `ApiErrorBody.message`
  (max 1000) → `ValidationError` inside the error handler → 500 INTERNAL; uvicorn logs "Exception in ASGI
  application" and **closes the keep-alive connection**: the next request on the same client connection fails with
  `ECONNRESET` (`httpx.ReadError`).
* `PUT /v1/sessions/<SID>/answers/<200 chars>` → `AnswerRecord(question_id=…)` fails validation → 500.
* `POST /v1/sessions/<SID>/incidents/<1000 chars>/reviews` → 500.

Repro (Python): `c = httpx.Client(base_url=f"http://127.0.0.1:{P}/v1", headers={"Authorization": f"Bearer {T}"});
c.get("/sessions/" + "A"*1000).status_code  # 500`; then `c.get("/health")` → `ReadError: [Errno 104] Connection reset by peer`.
Impact: a pooled client in Electron main loses its connection after one bad request; 500 instead of 404/422.
Suggested fix: validate path ids with the `Id` pattern (FastAPI `Path(pattern=…)` → 422) and truncate echoed ids in
error messages. A08's router must apply the same rule. Tracking: `test_overlong_*_is_rejected_cleanly`.

## QA-BUG-001 — token written to the log when a client puts it in a WebSocket URL · owner A01 · severity **low**

The contract forbids tokens in URLs and the backend correctly rejects `ws://127.0.0.1:<P>/v1/stream?token=<T>`.
But uvicorn's handshake log line (`uvicorn.error`, INFO, default level) prints the full path **with query**:
`INFO uvicorn.error: 127.0.0.1:53728 - "WebSocket /v1/stream?token=<TOKEN>" 403` (redacted here). If A06 persists
backend stderr to a log file, a misbehaving client leaks the token to disk. HTTP is not affected (`access_log=False`).
Suggested fix: a `logging.Filter` on `uvicorn.error` that strips query strings, or raise that logger to WARNING.
Tracking: `qa/tests/test_security_negative.py::test_token_misplaced_in_ws_query_is_not_logged`.

---

## Observations (not blockers; owner decides)

| ID | Owner | Observation | Tracking (xfail, non-blocking) |
|---|---|---|---|
| QA-OBS-003 | A08 (A01 bootstrap router today) | `PUT /answers/{qid}` accepts question ids that are not in the exam, option ids not in the question, free text for `single_choice`. Contract is silent; report/summary may show phantom answers | `test_answers_are_checked_against_exam_definition` |
| QA-OBS-004 | A01 / A08 | After `DELETE /v1/sessions/{sid}` the session is still readable via `GET /v1/sessions/{sid}` (runtime kept in `SessionManager`) — privacy expectation of "delete" | `test_deleted_session_is_not_readable` |
| QA-OBS-005 | A01 | Pydantic lax mode accepts `"30"` for int and `"yes"`/`"false"` for bool fields that the generated JSON Schema rejects (schema/server mismatch). TS clients send typed values; consider `strict=True` on request models | `test_create_rejects_lax_typed_values` |
| QA-OBS-006 | A01 | Host header comparison is case-sensitive (`LOCALHOST` → 403). Browsers lowercase Host; no impact | `test_loopback_host_header_accepted[LOCALHOST]` |
| QA-OBS-007 | A06 (info) | A rejected WebSocket (no/invalid token, foreign Origin) is seen by a real client as an HTTP **403 handshake failure** (uvicorn turns a pre-accept close into 403), not as close code 4401/4403. Electron main must treat any handshake failure as auth/config error | `_assert_ws_rejected` accepts both |
| QA-OBS-008 | A01 (info) | The lifecycle diagram omits re-entry transitions the code allows: `preflight→preflight`, `calibrating/ready→calibration/start`; `aborted→abort` is idempotent. Worth one line in CONTRACTS.md so A07 knows "re-run preflight/recalibrate" is supported | `test_lifecycle_matrix.SILENT` |
