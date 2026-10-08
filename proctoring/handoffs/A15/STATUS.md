# A15 — осмотр рабочего места перед экзаменом (desk scan)

Base: `2de547fe898cf78a6cfb673778fe91103fa7e4f8` (контракт 1.2.0); ветка `codex/proctor-A15-deskscan`.
Контракт не менялся. Владение: `backend/proctor/deskscan/`, `handoffs/A15/`.
Осмотр помогает преподавателю; это **не доказательство нарушения**. Уверенность — оценка модели, а не вероятность нарушения.

## API (backend/proctor/deskscan)

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/v1/sessions/{id}/desk-scan` | — | `DeskScanResult` (`not_started`, если осмотра не было) |
| POST | `/v1/sessions/{id}/desk-scan?mode=laptop\|usb` | `DeskScanRequest` (`duration_s` 5–30, по умолчанию 12) | `DeskScanResult` в `recording`; итог — опрос GET |
| POST | `/v1/sessions/{id}/desk-scan/skip` | `{"reason": "..."}` (1–200) | `DeskScanResult` в `skipped` |

- Только в `preflight` / `calibrating` / `ready`; иначе 409 `INVALID_STATE` (в т.ч. `created` — камера ещё не открыта, и `running`). Второй POST во время записи — 409. Повторный осмотр перезаписывает результат.
- `mode` — query-параметр (контракт `DeskScanRequest` не трогали). Тело skip — локальная модель `DeskScanSkipRequest` той же формы, что `CalibrationSkipRequest`.
- **PIN**: backend PIN не знает (как и для `calibration/skip`); skip закрыт в оболочке — `skipDeskScan` в списке операторских методов `main/src/shell/access.ts`.

### Три варианта (дополнение от капитана)
1. «Ноутбук» (`mode=laptop`, по умолчанию) — наклон экрана, поворот влево/вправо.
2. «USB-камера» (`mode=usb`) — провести камерой над столом слева направо; те же 12 с и тот же анализ.
3. «Камера не двигается» — осмотр камерой не проводится; `POST .../skip {"reason":"fixed_camera_teacher_check"}` после PIN → `SKIPPED`, `skip_reason="fixed_camera_teacher_check"`.

Вариант сохраняется в `message_ru`: `«Вариант: ноутбук. …»`, `«Вариант: USB-камера. …»`, `«Вариант: камера не двигается. Осмотр камерой невозможен (стационарная камера) — подтверждён преподавателем.»`. Пропуск оператором: `«Осмотр пропущен оператором. Причина: …»`.

## Как работает
- Кадры только от A02: временный consumer `deskscan` (`capture.add_consumer(..., max_fps=4)`) на уже открытой при preflight камере; свою камеру не открываем. Снимается `remove_consumer` по окончании.
- Анализ: **отдельный экземпляр** анализатора A03 из публичной фабрики `create_phone_analyzer` (берётся из `registry.factories["phone"]`), тот же YOLO11n и те же классы `cell phone`, `book`, `laptop`, `tv`. Отдельный экземпляр — чтобы не ломать трекер/порядок кадров основного анализатора сессии. Модель грузится в фоне при первом GET (студент открыл шаг). Нового детектора нет.
- Агрегирование по `class_name`: уверенность ≥ 0,5; класс засчитывается, если самая длинная непрерывная серия ≥ 0,5 с (пропуск до 0,7 с серию не рвёт); `max_confidence`, `seen_ms` = сумма серий.
- Итог: `clear` / `objects_found`. **FAILED (не «чисто»)**: камера не открыта; нет кадров; кадров < 40 % от ожидаемых; ошибки анализатора; модель недоступна; сессия завершена во время осмотра. В синтетическом режиме к тексту добавляется «(СИНТЕТИЧЕСКИЙ источник, не камера.)».
- Клип: только при `retain_media=true`. A02 `export_clip_result` (MJPG .avi, кольцо 10 с → клип покрывает последние ≤ 9,5 с осмотра) перекодируется в WebM/VP8 (контракт `EvidenceItem.media_type` допускает только jpeg/mp4/webm; Chromium играет VP8), ≤ 1,9 МБ (лимит чтения A08 — 2 МБ), временные файлы удаляются. Сохраняется как evidence `kind=clip`, `incident_id=null`. Без хранения медиа `evidence_id=null`, только список предметов.

## Минимальные правки чужих файлов (разрешены A01)
- `backend/proctor/app.py` (A01): импорт, `install_desk_scan_routes(api, manager, registry)`, хук preflight, `desk_scan.close()` при остановке, `app.state.desk_scan`.
- `backend/proctor/session.py` (A01): `extra_preflight_checks` у `SessionManager`/`SessionRuntime` (+`_extra_checks()` в конце `preflight()`); ошибка хука логируется, preflight не ломает. Проверка `DESK_SCAN` всегда `required=false`: PASS — clear; WARN — objects_found («На столе замечено: телефон — уберите его») и failed; NOT_RUN — не проводился/пропущен. На `ready` не влияет. Preflight идёт **до** осмотра, поэтому в первом отчёте подготовки будет NOT_RUN; повторный preflight показывает итог.
- `backend/proctor/evidence/store.py` (A08) — выбран путь через store, а не SessionInfo (SessionInfo не имеет поля, контракт не трогаем):
  - таблица `desk_scans(session_id PK, body_json, updated_at_us)` — `CREATE TABLE IF NOT EXISTS` в `open()`, **без** изменения `SCHEMA_VERSION`;
  - `record_desk_scan(session_id, result, clip)`, `desk_scan(session_id)`; `_summary()` заполняет `SessionSummary.desk_scan`;
  - `delete_session` удаляет строку `desk_scans`; клип удаляется вместе с медиа сессии;
  - `export_snapshot`: для `video/webm` проверяется сигнатура EBML вместо JPEG SOI.
- `backend/proctor/evidence/report.py` (A08): раздел «Осмотр рабочего места» (состояние цветом + текстом, вариант, таблица предметов с уверенностью и временем, `<video>` из data: если клип сохранён, иначе «Клип не сохранялся (хранение медиа выключено)»; для варианта 3 — «Осмотр камерой невозможен (стационарная камера) — подтверждён преподавателем»); CSP дополнен `media-src data:`; JSON: `desk_scan` верхнего уровня (+ `summary.desk_scan`); имя файла клипа в манифесте `.webm`.
- Bootstrap `MemoryEvidenceStore` (synthetic без модуля evidence) не получил `record_desk_scan`: результат виден через GET (память процесса), но не в summary.

## UI (desktop, A07 + IPC оболочки) — минимальные правки
- Шаг `renderer/src/screens/DeskScan.tsx` между «Подготовкой» и «Калибровкой»; логика (подсказки по секундам, тексты результата, вариант → mode) — `renderer/src/lib/deskScan.ts`. Степпер: 6 шагов («Осмотр места» — 2-й; ключ i18n `step_desk_scan`, казахский текст — черновик).
- Выбор варианта (по умолчанию «Ноутбук»), 12 с с обратным отсчётом и крупными подсказками (0–4 / 4–8 / 8–12 с; для USB — одна подсказка), превью камеры (`PreviewPanel`), «Повторить осмотр», «Далее» (запускает калибровку). Результат: зелёный «Стол осмотрен: посторонних предметов не замечено», жёлтый «Замечено: телефон, книга — уберите их и повторите осмотр», красный «Осмотр не удался: … Повторите…», пропуск — `message_ru`. Подпись: «Осмотр помогает преподавателю, это не доказательство нарушения. Видео осмотра сохраняется только если включено хранение медиа.»
- «Камера не двигается»: «Осмотр места проведёт преподаватель в аудитории» → «Подтвердить (PIN преподавателя)» → тот же PIN-механизм, что для пропуска калибровки → `skipDeskScan(reason="fixed_camera_teacher_check")`. «Пропустить (оператор)» — PIN + причина. Действие после PIN выполняется само (запоминается на 60 с).
- «Далее» доступен только после итога (clear/objects_found/failed/skipped), после тайм-аута опроса (duration + 20 с) или ошибки GET. В `not_started` — нет: либо осмотр, либо пропуск преподавателем.
- Кнопка Preflight теперь «К осмотру рабочего места»; `calibrationStart` перенесён в «Далее» шага осмотра. Кнопка «Без калибровки…» на Preflight не менялась — этот путь обходит осмотр (осмотр остаётся `not_started`, в отчёте «не проводился»).
- IPC: `getDeskScan`, `startDeskScan` (`validate.deskScanStart`: duration 5..30, mode laptop|usb до query-строки), `skipDeskScan` — в `OPERATOR_REQUIRED` (`main/src/shell/access.ts`). Методы bridge объявлены в `desktop/shared/desk-scan.ts` (`AppBridge = QorgauBridge & DeskScanBridge`), т.к. `contracts/ts/bridge.ts` — A01; при следующей правке контракта их стоит перенести туда.
- FixtureBridge (FIXTURE): осмотр в памяти → `clear`, либо `objects_found` («телефон», 0.82) при включённом флаге панели или `student_label` с «phone»; skip без разблокировки оператора — отказ.
- e2e-скрипты (`e2e-fixture/class/preflight`, `real-bridge/run-real`) проходят новый шаг (+~13 с).

## Проверено
```powershell
cd proctoring
..\..\verify-candidate\proctoring\.venv\Scripts\python.exe -m pytest -q backend/proctor/deskscan                       # 12 passed
..\..\verify-candidate\proctoring\.venv\Scripts\python.exe -m pytest -q backend/proctor/evidence backend/tests contracts/tests  # 139 passed, 3 skipped (как в базе)
```
Тесты (заглушка анализатора, кадры от synthetic capture через `add_consumer`): телефон 1 с → `objects_found` / `cell phone` / «телефон»; пусто → `clear`; нет кадров (камера) → `failed`; ошибка анализатора и недоступная модель → `failed`; `running` и `created` → 409; повтор перезаписывает; второй старт во время записи → 409; вариант USB; вариант «камера не двигается» → `skipped` + строка отчёта; skip оператора с причиной; summary/preflight/HTML/JSON содержат осмотр; клип с настоящим A02 capture: `retain_media=true` → evidence `video/webm`, `<video>` в отчёте; `false` → `evidence_id=null`.

Desktop (из `proctoring/desktop`, node_modules — junction на `A13-A07`, package-lock идентичен):
```powershell
node scripts/typecheck.mjs                       # PASS contracts/main/renderer
node renderer/tests/run-unit.mjs                 # 22/22 (4 логики шага + FIXTURE: start->recording->clear, phone->objects_found, skip без PIN — отказ)
node main/tests/run.mjs access validate          # 16/16 (skipDeskScan — только оператор)
$env:QORGAU_PYTHON=...; $env:PYTHONPATH="<clone>\proctoringackend;<clone>\proctoring\contracts\python"
node main/tests/run.mjs backend.integration      # 11 pass, 1 skip (POSIX-only); настоящий backend через bridge
```
Интеграция через bridge (настоящий процесс backend): старт осмотра `usb` → `recording` → итог с «Вариант: USB-камера.»; mode=phone — отказ валидации; skip без PIN → `operator_locked`; после PIN → `skipped`/`fixed_camera_teacher_check`; в `running` → `INVALID_STATE`. Без весов модели итог `failed` (мгновенно), с весами (`QORGAU_MODELS_DIR` → candidate) — полный 5-секундный осмотр.
Субагент UI: FIXTURE-браузер (vite preview + Playwright/Chromium) 11/11 по шагу; `e2e-fixture.mjs` 100/102 — 2 падения «identity is not invented from contract 1.1» есть **уже в базе 2de547f** (тест ждёт «Не заявлено сервисом», `examChecks.ts` в SYNTHETIC даёт «Не используется»), к A15 не относятся.

Смоук с реальными A02 + A03 (YOLO11n из `worktrees/candidate/proctoring/models`) + A08, синтетический источник, 6 с: `clear`, клип сохранён. Это проверка проводки, **не точность**.

## Не проверено
- Упакованное Electron-приложение не запускалось; `e2e-class.mjs`, `e2e-preflight.mjs`, `run-real.mjs`, `run-replay.mjs` после правок не прогонялись; полный `main/tests/run.mjs` — только access/validate/backend.integration.
- LIVE на ноутбуке капитана (CLEAR без предметов / OBJECTS_FOUND с телефоном) — ждёт готовности капитана; будет наблюдением, не точностью.
- Нагрузка CPU: во время осмотра работают основной анализатор телефона (8 к/с), внимание и анализатор осмотра (4 к/с).
- Во время быстрого движения ноутбука кадры смазаны — YOLO может не увидеть предмет; «не замечено» ≠ «нет».

## Класс (не делалось, только описание)
Преподаватель мог бы запросить повторный осмотр командой класса (C2 uplink): команда `desk_scan_request {session_id}` → студенческий клиент показывает шаг осмотра поверх экзамена… но во время `running` backend сейчас отвечает 409 (осмотр только до старта). Варианты: (а) разрешить осмотр в `paused` (преподаватель ставит паузу → осмотр → resume) — правка `ALLOWED_STATES` + проверка, что fusion не получает кадры осмотра (уже так: отдельный consumer); (б) только до старта. Нужна команда в `qorgau.class.v1` и согласование C2/A01 — без согласования не реализовано.
