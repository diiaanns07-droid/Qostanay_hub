# A08 — A05 zones connected, 8 октября 2026 / Windows 11

Branch: `claude/focused-mendel-4940jx`. Previous published A08: `9090b9ae19bce86f1317b53dea43109af769d1f9`.
Verified A01 integration: `codex/proctor-integration` @ `78798b7ca83f16552e43a4cf1ae29e81162db44c`.
Confirmed A05: `claude/zen-mayer-e0tivt` @ `d9832a9042f817e560eef26dbad5c45882399a3c`.
Both SHAs fetched and checked; fusion paths in the integration match that A05 delivery exactly.
No A05/A01 code merged into the A08 branch. Integration checks use a detached scratch checkout + A08 overlay.

`review_zones.assess_session_zone` now calls **proctor.fusion.zones.assess_session_zone(incidents, summary, config)**.
Inputs are forwarded unchanged; only zone/reasons_ru/rule_version are mapped into the temporary local model.
No thresholds or classification rules in A08. If A05 is absent in an isolated role checkout, the existing
explicit None / uncalculated fallback remains. Internal A05 import/assessment failures are not hidden.
Contract remains 1.0.0; local ReviewZone/SessionOverviewRow/SessionSummary fields remain temporary as instructed.

## REPLAY acceptance on the captain's Windows laptop

Actual local recordings and A03/A04 models under `%LOCALAPPDATA%/QorgauExam/`.
Original manifests, realtime pacing, calibration explicitly skipped, retain_media=false.
No camera recording, native restriction, Windows setting changes or foreign process termination.

| Replay | Expected / actual | /overview | HTML | Reasons from A05 |
|---|---|---:|---:|---|
| zone_a_green_01 | green / green — PASS | 200 | 200 | Эпизодов нет, наблюдение полное |
| zone_b_yellow_02 | yellow / yellow — PASS | 200 | 200 | Лицо не видно: 00:06, 6 с; 00:14, 12 с |
| zone_c_red_01 | red / red — PASS | 200 | 200 | Второе лицо: 00:37, 3 с; телефон: 00:05, 10 с; нет лица: 00:14, 13 с |

Summary, overview and JSON agree on zone/reasons; HTML contains the same escaped reasons and zone label/icon/color.
All use `zone-rule-1`, max 3 reasons, REPLAY banner, teacher decision counts. Overview order: red, yellow, green.
Capture: 900 / 1052 / 1202 frames, zero capture drops, zero consumer errors; configured consumer sampling remains.
Each clip has one low-priority technical replay-end incident; A05 deliberately excludes it from the zone rule.
This explains the green report's one pending technical review. Raw total counts are preserved.
Three HTML files rendered offline in Chrome: no scripts/network/errors or mobile overflow; A4 print 4/6/7 pages.
Desktop screenshots of all three visually checked: zone and decisions are on the first screen.

## Regression results

- Isolated A08: `python -m pytest backend/proctor/evidence -q -p no:cacheprovider` → **57 PASS, 2 SKIP**, 21.45 s.
  SKIP: symlink WinError 1314; real A05 test (module intentionally not in this role's branch).
- Pinned integration + A08: same command plus `backend/tests/test_route_order.py` → **59 PASS, 1 SKIP**, 20.63 s.
  SKIP: symlink WinError 1314 only. **0 FAIL / 0 XFAIL / 0 XPASS** on both checkouts.
- A01 overview route fix confirmed by its regression and all three real REPLAY sessions. Previous 404 dependency resolved.
- Logs, replay hashes/counts and rendering facts: `checks/zones-*`, `checks/replay-zones-results.json` and `checks/replay-zones-render.json`.
  Media, model weights, local databases and replay HTML containing session metadata are not committed.

Ready for A01 to integrate this A08 commit. NOT verified: LIVE, accuracy metrics, native Windows protection,
contract 1.1, symlink with elevated privilege, JSON import across computers. Three REPLAY successes are not accuracy measurement.

---
## Historical checkpoint — temporary adapter before A05 delivery

Branch: `claude/focused-mendel-4940jx`. Previous published SHA: `5509950e38c622ae544d40c75d4b8fdba7256b82`.
Baseline unchanged: A01 `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`, contract 1.0.0.
A01 integration candidate read-only reference: `de7290509bf558d6488be84d2e0730b2b9ab104a`.
Current stage: QA fixes + temporary zone models/adapter + overview handler + report zone header.

- QA-WIN-004: rule-version test compares actual incident provenance; symlink-only test skips specifically
  WinError 1314 without changing Windows permissions; deletion test excludes the process lock file.
- QA-BUG-002: A08 already validates path ids; added regressions for 129/1500-character and malformed ids
  across summary/incidents/evidence/answers/reviews, clean 422 and continued client use.
- QA-OBS-003: HTTP answer writes validate exam/question/option ids, shape, duplicates, single-choice
  cardinality and short-text length before persistence. Empty choice list is an allowed clear operation.
- QA-OBS-004: A08 reads return 404 after deletion; router calls optional public context.forget_session,
  available in the A01 candidate (BOOTSTRAP lacks the hook).
- QA-OBS-009: regressions assert health() equals open() after unavailable directory, schema mismatch and lock refusal.
- Temporary local models: ReviewZone, SessionOverviewRow and extended SessionSummary with exact requested fields.
  Captain explicitly approved zone=None while A05 is unpublished. Adapter implements NO zone-rule-1 thresholds.
- overview(): consistent local-session counts, pending reviews, red/yellow/grey/green ordering, then newest first;
  uncalculated None rows last. Endpoint handler GET /sessions/overview in A08 router.
- Report: zone text + symbol + color, reasons (max 3), teacher decision counts alongside; escaped HTML,
  LIVE/REPLAY/SYNTHETIC and limitations retained. Teacher reviews do not alter the incidents passed to assessor.

First checkpoint pushed: `c201443f215b58b14f9d79104d4dcc86f3a4b9fd` before 14:00 local.

Final tests on Windows 11 / Python 3.12.14, environments from unchanged lockfile:
`python -m pytest backend/proctor/evidence -q -p no:cacheprovider`
- A08 branch: **55 PASS, 1 SKIP, 0 FAIL, 0 XFAIL, 0 XPASS**, 17.91 s.
- Scratch A01 `de7290509bf558d6488be84d2e0730b2b9ab104a` + ONLY A08 overlay:
  **55 PASS, 1 SKIP, 0 FAIL, 0 XFAIL, 0 XPASS**, 17.61 s.
SKIP: symlink privilege (WinError 1314). Windows permissions/settings unchanged.
The optional real Chromium render/print test now PASSES (installed Chrome + temporary Playwright 1.56.0).
No dependency or lockfile changes. See `checks/README.md` for commands and evidence.
Windows lock refusal/release, atomic media write/reopen/delete and Cyrillic path passed.
QA-BUG-002 now also has a real backend subprocess + HTTP/1.1 TCP regression:
15 invalid-id requests return 422/INVALID_ARGUMENT; the same socket serves every subsequent health request.
Only the test's own subprocess is shut down, through the public stdin protocol.
Zone plumbing/sorting uses labelled assessor doubles; NOT validation of unpublished A05 rules.
Only A08-owned paths changed. No other modules merged. Logs: handoffs/A08/checks/.

Candidate integration probe: after DELETE, A01 common GET/session and GET/metrics plus all checked A08
reads return 404. QA-OBS-004 is verified across the public boundary on this candidate.
**Integration blocker for the NEW overview screen:** candidate GET /v1/sessions/overview returns
404 SESSION_NOT_FOUND ("session overview not found"). A01's generic route shadows the A08 handler.
The standalone A08 handler passes. A01 must register the static route first; no foreign code was changed.

SYNTHETIC HTML/JSON samples regenerated with current temporary zone fields and teacher review counts.
Real Chrome: one local-file request, no network requests/scripts/links/console errors, embedded image loads,
mobile width 390 has no horizontal overflow, print-to-A4 succeeds. Desktop zone + teacher decisions fit
in the first screen (bottom 332 px of a 900 px viewport). Mobile screenshot visually reviewed too.
This is report/layout verification, not a measured 20-second teacher usability study or LIVE CV validation.

Dependencies: A01 must mount static /sessions/overview before /sessions/{id}; contract 1.1 SHA pending.
A05 function SHA pending; today adapter returns None with reason, not a guessed green/grey.
Current added summary fields are temporary local models, not a claim of released contract 1.1 compatibility.
Answer validation reads the same settings.exam_path / demo_min fallback as A01; request a public get_exam view.

Ready for A01 to integrate the A08 branch. Captain explicitly instructed not to wait for the unpublished
A01/A05 deliveries. Switch the local models and thin adapter only after receiving confirmed SHAs.
Optional cross-laptop JSON import is deferred; overview currently includes local sessions only.

## После 9 октября
Cross-laptop JSON import (schema/version/hash, duplicate handling), long-session performance,
precise monotonic coverage timing, video clips/encryption/new formats (not implemented today).

---
## Historical checkpoint 1

# A08 — STATUS

Роль: SQLite-хранилище, материалы эпизодов (evidence), проверка преподавателем, отчёты.
Ветка: `claude/focused-mendel-4940jx` (назначена платформой; fast-forward на BOOTSTRAP A01).
Contract/baseline: `qorgau.v1 1.0.0`, BOOTSTRAP A01 `claude/nifty-ride-ux8e4j` @ `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`.
Предыдущий checkpoint A08: нет (первый). SHA этого checkpoint сообщается в финальном ответе (коммит не содержит свой SHA).
Этап: **checkpoint 1 — store + router + review + HTML/JSON отчёт готовы, проверены на contract fixtures и синтетическом конвейере A01.**

## Что работает
* `proctor.evidence.create_evidence_store(settings, config=None) -> SqliteEvidenceStore` (протокол `EvidenceStore`,
  `isinstance(..., interfaces.EvidenceStore)` = True). A01 `ModuleRegistry` находит модуль сам — без overrides
  `/v1/health` показывает `evidence: ok`, preflight `storage: pass (impl=module)`.
* SQLite (stdlib) в `settings.data_dir/qorgau-evidence.sqlite3`: WAL + `synchronous=FULL` + `secure_delete=ON`,
  миграции по `PRAGMA user_version` (v1), каждая в одной транзакции; БД новее кода не трогается (`schema_too_new`).
  Эксклюзивный lock-файл: второй backend на том же `data_dir` получает health `store_in_use`.
* Таблицы: sessions, session_events (переходы состояний → паузы), observations (только нужные), incidents
  (последняя версия) + incident_changes (история по `(incident_id, update_seq)`), incident_observations, reviews
  (append-only, UPDATE запрещён триггером), answers, evidence, coverage_segments, producers, counters,
  deleted_sessions (tombstone), pending_media_deletions. Индексы по `(session_id, t_session_ms|t_start_ms)`.
* Все A08-маршруты из CONTRACTS.md §3 (router монтирует A01 под `/v1` за своей авторизацией; своих bypass нет).
* Автономный HTML-отчёт (печать A4, телефонная ширина) и JSON-экспорт с `ExportManifest` (SHA-256 report.html и снимков).

## Политики (выбранные и проверенные)
| Тема | Поведение |
|---|---|
| Приватность | по умолчанию метаданные. CV-наблюдения держатся в памяти (≤120 с / ≤5000) и пишутся в БД только если на них сослался эпизод; environment/health — всегда. Снимки — только при `retain_media=true`, JPEG через cv2, ≤2 МБ, ≤3 на эпизод, ≤200 / 50 МБ на сессию, нужно ≥100 МБ свободного места, TTL медиа 7 дней (метаданные остаются, помечены `ttl_expired`). Видео-клипы не реализованы (stretch). |
| Повторная доставка | observations — по `(session_id, observation_id)`; incident changes — по `(session_id, incident_id, update_seq)`, более старый seq не перезаписывает новый, тот же seq с другим телом — `conflicting_incident_changes`; answers — last-writer по `client_seq` (равный seq возвращает сохранённое); повтор идентичного review за 5 с — тот же review (двойной клик). |
| Изоляция сессий | все ключи `(session_id, …)`; одинаковые incident_id в разных сессиях независимы; evidence чужой сессии → 404; кадр другой сессии не сохраняется; `source_mode` сессии неизменен, записи другого режима отбрасываются. |
| После finish/abort | новые observations/incidents/snapshots/answers отклоняются (счётчик `rejected_after_end`), состояние не «воскрешается»; временный буфер очищается. Закрывающие изменения `engine.finish()` A01 присылает до перехода в finished — они сохраняются. |
| Ошибки диска | `record_*()` никогда не бросают (поток fusion не останавливается): счётчик `write_failures`, health `degraded/storage_write_failed`, отметка в отчёте. Маршруты отдают `503 STORAGE_ERROR (retryable)`. `open()` не бросает: `storage_unavailable` → LIVE preflight `storage: fail`. |
| Медиа-файлы | `<data_dir>/evidence-media/<random token>/<random>.jpg`; клиентские id никогда не становятся путём; проверка regex + resolve внутри корня + отказ от symlink; запись `*.tmp` + fsync + `os.replace`; при сбое вставки в БД файл удаляется; при `open()` удаляются `*.tmp`, `.trash-*`, сиротские каталоги. Чтение сверяет SHA-256 (несовпадение → 503 `hash_mismatch`, в отчёт не встраивается). |
| Удаление | только завершённой сессии (активная → 409 `SESSION_ACTIVE`); одна транзакция по всем таблицам + tombstone, затем атомарное переименование и удаление каталога медиа; если каталог не удалился (файл занят) — повтор при следующем `open()`. `wal_checkpoint(TRUNCATE)` + `secure_delete`: тест проверяет, что уникальная строка исчезла из файлов БД. Это **не** forensic-стирание SQLite/SSD. |
| delete/export race | экспорт и удаление одной сессии сериализованы per-session lock: экспорт либо полный (все файлы сверены по хэшу), либо `SESSION_NOT_FOUND`; частичного состояния нет. |
| Рестарт | сессии, оставшиеся не-терминальными после сбоя/kill, при `open()` становятся `failed` c `last_error{code: INTERNAL, details.recovered: true}`; данные до последней записи сохраняются; отчёт показывает баннер «СЕССИЯ ПРЕРВАНА СБОЕМ». |
| Покрытие | сегменты наблюдений по `phone` и `attention` (determined = ok/degraded, undetermined = unknown/error), разрыв > 2 с = пропуск. `observed_ms` = время экзамена без пауз, где **оба** анализа выдавали определённый результат. `gaps`: паузы, `no_observations`, `undetermined`, health-интервалы (`camera_disconnected` …). Время пауз/завершения выводится из wall-clock в момент получения `upsert_session` (точное — только старт экзамена). |

## Интерфейсы
Python (для A01):
```
store = create_evidence_store(settings)       # proctor.evidence
store.open() -> Health                        # никогда не бросает
store.create_router(context) -> APIRouter     # A01: app.include_router(..., prefix="/v1")
store.upsert_session / record_observation / record_incident_change / capture_snapshot   # не бросают
store.delete_session(sid)                     # NotFoundError / InvalidStateError(SESSION_ACTIVE) / StorageError
store.health() / store.close()
```
Настройки A08: `EvidenceConfig` (`backend/proctor/evidence/config.py`), переопределение `QORGAU_EVIDENCE_<FIELD>`,
например `QORGAU_EVIDENCE_MEDIA_TTL_S=86400`. Общие `Settings` только читаются (`data_dir`).

### Для A07 (bridge-методы → маршруты → поведение)
| `window.qorgau` | Маршрут | Ответ / ошибки |
|---|---|---|
| `listSessions()` | GET `/sessions` | `SessionInfo[]`, новые первыми; удалённые не возвращаются; после сбоя — `state: failed`, `last_error.details.recovered = true` |
| `listIncidents(sid)` | GET `/sessions/{sid}/incidents` | `Incident[]` по `t_start_ms`; `review_status` и `evidence_ids` заполняет A08. В WS `IncidentMsg` эти поля от A05 (`pending`, `[]`) — **перезапрашивайте REST** после события |
| `getIncident(sid, iid)` | GET `/sessions/{sid}/incidents/{iid}` | `IncidentDetail` (история reviews по порядку; действует последний; `evidence` без удалённых по TTL); 404 `NOT_FOUND` |
| `addReview(sid, iid, body)` | POST `.../reviews` | 200 `HumanReview` (append-only, `supersedes_review_id` = предыдущий); повтор того же тела ≤5 с — тот же review; comment ≤2000, operator 1..64 → иначе 422 |
| `getEvidence(sid, eid)` | GET `/sessions/{sid}/evidence/{eid}` | `image/jpeg`; 404 `NOT_FOUND` с `details.reason` ∈ `media_missing`, `ttl_expired`, `invalid_path`; 503 `hash_mismatch` |
| `saveAnswer(sid, qid, body)` | PUT `/sessions/{sid}/answers/{qid}` | 200 `AnswerRecord` (при устаревшем `client_seq` — сохранённая запись); 409 `INVALID_STATE` вне `running` (в т.ч. пауза/после finish); строка ≤4000 |
| `listAnswers(sid)` | GET `/sessions/{sid}/answers` | `AnswerRecord[]` по `question_id` |
| `getSummary(sid)` | GET `/sessions/{sid}/summary` | `SessionSummary`; `reviews_by_decision` содержит и `pending`; `limitations_ru` показывать как есть |
| `exportReport(sid, "html"\|"json")` | GET `/sessions/{sid}/report.html` / `report.json` | main сохраняет файл; HTML без скриптов/ссылок/внешних URL, CSP `default-src 'none'`; JSON = `qorgau.report.v1` (см. sample) |
| `deleteSession(sid)` | DELETE `/sessions/{sid}` | `{"deleted": true}`; 409 `SESSION_ACTIVE` для незавершённой; 404 `SESSION_NOT_FOUND` |
Общие: id в пути по `^[A-Za-z0-9._:-]{1,128}$` (иначе 422 `INVALID_ARGUMENT`); хранилище недоступно → 503 `STORAGE_ERROR` (retryable).
Fixtures: контрактные `IncidentDetail.with_review`, `HumanReview.dismissed`, `HumanReviewCreate.dismiss`,
`SessionSummary.finished`, `AnswerRecord.single`, `ExportManifest.example`; полный синтетический пример экспорта —
`backend/proctor/evidence/samples/report.synthetic.json` и его HTML `report.synthetic.html`
(генерация: `python -m proctor.evidence.sample --out <dir>`; всё синтетическое, кадр — градиент без человека).

## Изменённые пути
`proctoring/backend/proctor/evidence/` (`__init__.py`, `config.py`, `db.py`, `media.py`, `coverage.py`, `store.py`,
`report.py`, `router.py`, `sample.py`, `samples/`, `tests/`), `proctoring/handoffs/A08/`.

## Проверки (Linux x86_64, Python 3.12.3, venv из `requirements/full.txt --require-hashes`; без камеры/Windows/GPU)
| Команда (из `proctoring/`) | Результат |
|---|---|
| `python -m pytest -q backend/proctor/evidence` | **44 passed** (store 18, resilience 14, API через реальный `create_app` A01 5, report 7) |
| `python -m pytest -q` (весь репозиторий) | 106 passed, **2 failed — ожидаемо**: тесты A01 утверждают, что evidence *не* интегрирован (см. DEPENDENCIES №1) |
| `QORGAU_DATA_DIR=<tmp> python -m proctor smoke` | **34/34 PASS** (реальный процесс backend, evidence=ok, storage=pass) |
| ручной сценарий: реальный backend, синтетический экзамен, `SIGKILL`, перезапуск | сессия `failed`+`recovered`, эпизод/снимок (хэш)/ответы сохранены, отчёт с баннером, delete убрал медиа |
| Chromium (Playwright 1.56, headless, offline) `tests/render_check.cjs` | запросы только `file://`, 0 сообщений консоли/CSP, изображение из `data:`, нет горизонтального скролла на 390 px, PDF A4 печатается (пример: 10 страниц) |
| `python coordination/verify_ownership.py --agent A08 --base 35bea4c…` | PASS (см. финальный ответ) |

Покрыто тестами: duplicate event/incident/review/answer, изоляция двух сессий, прерванная транзакция (fault-hook →
rollback), `os._exit` внутри открытой транзакции в отдельном процессе → рестарт, missing file, hash mismatch,
path traversal (подменённые `file_name`/token, symlink, `..%2F` в URL), вредоносный текст в отчёте (HTML-парсер:
нет script/iframe/svg/a/on*-атрибутов/внешних src), слишком большой payload (413 от A01, 422 по контракту, лимит
снимка), реальный `SQLITE_FULL` (`max_page_count`), `ENOSPC` при записи медиа (нет частичных файлов), непригодный
`data_dir`, схема новее кода, второй backend на том же каталоге, delete/export race, конкурентная запись/чтение,
удаление (включая отсутствие маркера в файлах БД), TTL-очистка, восстановление после рестарта, покрытие/пропуски/пауза.

## Не проверено / ограничения
* Windows: `msvcrt` lock, `os.replace`/удаление при открытых дескрипторах (антивирус/индексатор) — код есть, запуск нет.
* LIVE/REPLAY: реальные кадры камеры A02 и реальные правила A05 не интегрированы — только синтетика и fixtures.
  Снимок по `trigger_frame_id` зависит от кольцевого буфера A02 (3 с): поздно открытый эпизод может остаться без снимка.
* Длинные сессии (2 ч) и производительность записи на целевом ноутбуке не измерены.
* Видео-клипы не реализованы; `ExportManifest.models` пуст (нет источника манифестов — DEPENDENCIES №2).
* Время пауз/завершения — по wall-clock в момент перехода (точность ~мс, не монотонные часы).
* Хэши — обнаружение случайных изменений, не защита от владельца машины; удаление — не forensic.

## Блокеры
Нет для A08. Запросы к A01 — `DEPENDENCIES.txt`.

## Порядок интеграции
A02 → A03/A04 → A05 → **A08** (этот SHA поверх `35bea4c`). При интеграции A01 обновляет два своих теста
(DEPENDENCIES №1). A08 не зависит от кода A02/A05 — только от контрактов.

## Следующий шаг
Прогон на LIVE/REPLAY после интеграции A02/A05; приём манифестов моделей и конфигурации движка (№2); измерение
нагрузки на 2-часовой сессии; Windows-проверка удаления/lock (вместе с A09); по запросу — клипы.
