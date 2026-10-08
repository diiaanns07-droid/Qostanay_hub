# Reproducible findings for module owners (A09 does not patch other modules)

## Финальная QA 2026-10-08 (a883e96 → f12cea4 → f6525a6)

Подробные команды, каждый FAIL и отдельные повторы: [FINAL_QA.md](../handoffs/FINAL_QA.md).
Код продукта не исправлялся. Старые записи ниже не являются актуальным списком открытых P0.

| ID / приоритет | Владелец | Наблюдение и следующий шаг |
|---|---|---|
| QA-FINAL-001 / CLOSED в f6525a6 | T01/T05 | На a883e96/f12cea4 два legacy audio_start теста стабильно FAIL отдельно. Поставка integration обновила ожидания; повтор на f6525a6 PASS (в составе 112 PASS изменённых путей). |
| QA-FINAL-002 / P1 тестового запуска | A06/A01 | `desktop/main/src/main.ts` создаёт NativeHelper с `maxMinutes: 240`, конфигурации лимита нет. Для QA по требованию капитана нужен максимум 2 минуты. Просьба добавить параметр длительности; текущая проверка использует отдельный QA-only adapter без изменения продукта. |
| QA-FINAL-003 / P1 схемы, P2 harness | A01/A08/A09 | Временная A08 SessionSummary возвращает `review_zone`, `review_zone_reasons_ru`, `review_zone_rule_version`, но опубликованная schema их запрещает. 8 состояний lifecycle FAIL отдельно; временные поля были ранее согласованы. Дополнительно QA flow ожидает READY 1.0.0 при реальном 1.2.0. Согласовать схему/временную модель и версию проверки, без удаления полей из UI ради зелёного теста. |
| QA-FINAL-004 / P2 harness | A09 | Fault-injection camera_unplugged берёт последний health любой компоненты и получает audio/noise_calibration вместо capture/camera_disconnected. Отдельный повтор FAIL. Фильтровать по компоненте и ждать нужное событие. |
| QA-FINAL-005 / P2 harness | A09 | Static offline gate считает `audio/prepare.py`, `identity/prepare.py`, `uplink/client.py`, `uplink/demo_teacher.py` runtime downloads. Первые — явная подготовка моделей; остальные — разрешённый класс/демо. Уточнить границы статического аудита, сохранив динамический запрет внешней сети для автономного экзамена. |
| QA-FINAL-006 / P2 отчёт harness | A09 | run_qa JUnit parser теряет non-strict XPASS: пишет PASS=356 вместо pytest PASS=354, XPASS=2. Исправленные QA-OBS-003/004 нельзя скрывать в PASS; отчёт FINAL_QA приводит фактические статусы. |
| QA-WIN-003 / прежний P2 | A03 | Три прежних Windows FAIL повторились отдельно. Последующий C2 redelivered-command падает из-за отложенного BaseEventLoop.__del__, отдельно PASS: известный плавающий эффект no_network/Windows, а не доказанный дефект C2. |

| QA-FINAL-007 / P1 тестовой приёмки | A03 | С реальными весами 7 дополнительных FAIL: phone_only/4 boxes_map/prepadded создают несовместимую с новой object-веткой конфигурацию (`object_class_names must not repeat phone classes`); deterministic ожидает 2 ONNX-вызова, фактически 8 при одинаковом итоговом результате. Все повторились отдельно. Обновить реальные тесты с учётом нескольких проходов и раздельных наборов классов; качество модели из этих FAIL не следует. |
| QA-FINAL-008 / P2 теста UI | T03 | E2E ожидает bundled Chromium, которого нет. С QA-only выбором установленного Chrome он получает карточку/red/клип HTTP 206, но падает на strict locator `.t3-test-label`: после добавления provenance теперь 2 элемента. Уточнить селектор; оставшиеся шаги и рестарт этим прогоном не проверены. |

| QA-FINAL-009 / P1 проверки типов | A01/A06 | Новый в f6525a6: `main/src/__tests__/backend.integration.test.ts:151–152`, TS18047: dsDone possibly null. Отдельный `tsc --project tsconfig.main.json --noEmit` повторяет FAIL. Добавить проверку ненулевого результата before field access в тесте; build и 107 shell-тестов проходят. |

P0 видео пока не подтверждены: REPLAY/HTML ещё проверяются. LIVE preflight и калибровка PASS по капитану.

## Актуально: кандидат de7290509bf558d6488be84d2e0730b2b9ab104a, Windows 11

Исходный прогон: `results/20261008T070227Z_candidate_de7290509bf5/`.
Повтор с исправленным только A09 harness: `results/20261008T072100Z_candidate_a09_reviewed_de7290509bf5/`.
QA-BUG-001…005 **исправлены A01 и перепроверены**: 10 строгих XPASS в исходном прогоне,
после снятия старых маркеров — PASS. Описания ниже сохранены как история, не список открытых багов.

| ID / приоритет | Владелец | Факт и воспроизведение | Влияние / следующее действие |
|---|---|---|---|
| QA-WIN-007 / **P0, LIVE не запускается** | A06, интеграция A01 | Electron 43.7.5 / Windows 11: обычный запуск заканчивается с exit 0 через ~6 с. 12 startup self-test проверок PASS, затем сразу `shutdown: before-quit`. `probe-electron.ts:dispose()` уничтожает единственное окно; безусловный `app.on("window-all-closed", () => app.quit())` срабатывает до создания mainWindow. Лог: `results/candidate_windows_setup/startup-selftest-exit.txt`. | Предложен минимальный патч `fixes/QA-WIN-007-A06-startup.patch`, **не применён к кандидату**. A06/A01 должны интегрировать и дать новый SHA. Диагностика с self-test=0 позволяет открыть UI, но оставляет capabilities unverified и не проходит LIVE preflight; это не исправление. |
| QA-WIN-001 / **P0 для показа всех требований кейса** | A06, интеграция A01 | В кандидате отсутствует `desktop/native/bin/qorgau-guard.exe`; `native/README.md` описывает интерфейс, а не поставленный помощник. Оболочка не подтверждает системную блокировку Alt+Tab/Win/чужих окон. | Частичный LIVE с камерой допустим, полную защиту Windows показывать как работающую нельзя. Нужна поставка A06 + отдельная проверка на этом ноутбуке. |
| QA-WIN-002 / P1, безопасность тестового режима | A06 | `environment/guard.ts`: при `registered=false` у аварийного сочетания выдаётся `enforcement_error`, но режим экзамена всё равно включается. Unit-тест `emergency hotkey not registrable` подтверждает именно это поведение; конфликт реальной клавиши на ноутбуке ещё не проверен. | До ручного теста всегда иметь PID и путь через Ctrl+Alt+Del. Предложение: отказ от включения ограничений, если аварийная клавиша не зарегистрирована; не изображать регистрацию как PASS. |
| QA-WIN-003 / P2, тесты Windows | A03 | Два `test_install_*` ожидают `phone/m.onnx` вместо Windows `phone\\m.onnx`. `test_app_health_lists_phone_model_missing`: `no_network` запрещает loopback `socket.socketpair()` Windows ProactorEventLoop; это также вызывает ошибку очистки `_ssock` в следующем lifecycle-тесте A01. | Исправить переносимость тестов и разрешить только loopback, сохранив запрет внешней сети. Блокировка запуска продукта этими сбоями не доказана. |
| QA-WIN-004 / P2, тесты Windows | A08 | `test_full_synthetic_flow` ждёт `bootstrap-0`, получает `a05-rules-1.0.0`; symlink-тест падает WinError 1314 без привилегии; delete-тест пытается читать занятый `*.sqlite3.lock` как файл БД. | Обновить тесты для интегрированного A05 и Windows. Настройки Windows/права не менять ради теста. |
| QA-WIN-005 / P2, устаревшее ожидание | A06 / A01 | `npm run test:shell`: 62 PASS, 1 FAIL, 1 SKIP. В `backend.integration.test.ts` после завершения ожидается `events.dropped === 1`, фактически 0. Кандидат A01 намеренно принимает поздние release/focus события в grace-интервале (`session.py:environment_events`). | Согласовать тест A06 с новым поведением A01; это не свидетельство провала включения/снятия ограничений. |
| QA-WIN-006 / исправлено A09 | A09 | Статический offline-тест не исключал штатный CLI `phone/prepare.py`; prepare-скрипт вызывал для A03 неверный `model_tool fetch`. Electron 43.7.5 требует явного `node node_modules/electron/install.js` после `npm ci`. | Исправлены только A09 пути. Проверка runtime-сети остаётся включённой, веса и lockfile не изменены. |

Ручные результаты CV/горячих клавиш/восстановления **не получены**. Не добавлять их как PASS.
Подробности подготовки и исходные автоматические результаты: `results/CANDIDATE_WINDOWS.md`.

---

## Исторические находки на BOOTSTRAP

Found on product SHA `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49` (A01 BOOTSTRAP), Linux x86_64, Python 3.12.3.
Order = priority for A01: QA-BUG-004, QA-BUG-005, QA-BUG-003, QA-BUG-002, QA-BUG-001.
Each item has a minimal repro against a real `python -m proctor serve` process and a tracking test in `qa/tests`
marked `xfail(strict=True)` — when the fix lands the test turns XPASS → FAIL, and A09 removes the marker.
`<T>` = the bearer token, `<P>` = port from the READY line, `<SID>` = a session id.

---

## QA-BUG-004 — a failing store write silently disables episode detection · owner A01 · severity **medium**

In `SessionRuntime._fusion_loop` (`session.py`) `store.record_observation(item)` and `engine.consume(item)` run in
the same `try` block, store first. If the evidence store raises (SQLite locked, disk full, `STORAGE_ERROR`), the
observation never reaches the engine: **no episodes are produced for the rest of the session**, while the exam keeps
running and `/health` stays `ok` (see QA-BUG-005).
Repro (fault-injection double, real `serve` process): `QA_FAKES='{"capture":"ok","phone":"ok","attention":"ok","fusion":"ok","evidence":"record_raises"}' python -m qorgau_qa.fakes -- -m proctor serve --token-stdin --port 0`
(PYTHONPATH = backend, contracts/python, qa) → LIVE session → start → the scripted phone never opens an episode on the
stream within 12 s (with `evidence=ok` it opens within ~2 s).
Suggested fix: consume first (or in a separate `try`), record afterwards; report store failures as health/coverage.
Tracking: `qa/tests/test_fault_injection.py::test_storage_write_failure_does_not_silence_episode_detection`.

## QA-BUG-005 — pipeline failures are invisible ("unknown" looks like "all clear") · owner A01 · severity **medium**

Exceptions in the engine (`consume`) or the store are only counted in `SessionRuntime.counters`; nothing reaches
`/health`, `RuntimeMetrics`, the stream or the summary. With an engine that fails on every observation the operator
sees an exam with **no episodes**, indistinguishable from "no violations" — against CONTRACTS.md §1 Unknown.
Repro: as above with `"fusion":"consume_raises"` or `"evidence":"record_raises"` → after 2 s of a running LIVE
session `/v1/health` reports `fusion: ok`, `evidence: ok`.
Suggested fix: expose the counters (health `degraded` with code e.g. `fusion_errors`, a `monitoring_degraded`
coverage gap, or `RuntimeMetrics` fields). Tracking: `test_pipeline_errors_are_visible_in_health`.

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
| QA-OBS-009 | A03 / A04 / A08 | `/v1/health` calls `impl.health()` live, while preflight uses the `Health` returned by `load()`/`open()` at startup. A module whose `health()` does not stay consistent with a failed `load()`/`open()` would show `ok` in health and `fail` in preflight (the QA doubles had exactly this bug at first). Add a unit test: after a failed load, `health()` returns the same UNAVAILABLE code | — |
| QA-OBS-008 | A01 (info) | The lifecycle diagram omits re-entry transitions the code allows: `preflight→preflight`, `calibrating/ready→calibration/start`; `aborted→abort` is idempotent. Worth one line in CONTRACTS.md so A07 knows "re-run preflight/recalibrate" is supported | `test_lifecycle_matrix.SILENT` |
