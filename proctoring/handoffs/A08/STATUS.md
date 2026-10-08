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
