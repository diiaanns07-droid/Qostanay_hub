# A09 — кандидат A01 на ноутбуке Windows 11

Ветка A01: `claude/nifty-ride-ux8e4j`. После `git fetch` ref совпал с
`de7290509bf558d6488be84d2e0730b2b9ab104a`; manifest: `proctoring/coordination/CANDIDATE.json`.
Отдельный detached checkout: `worktrees/candidate`. Продукт этого SHA не менялся;
после первого прогона в checkout скопированы только собственные A09 QA/packaging-файлы.
Ветка поставки A09 не включает чужие модули.

## Автоматические результаты

| Проверка | Результат | Что это доказывает |
|---|---|---|
| Исходный `run_qa.py --with-baseline --label candidate --expected-sha de7290509bf558d6488be84d2e0730b2b9ab104a` | **FAIL**: 351 PASS, 1 FAIL, 6 XFAIL, 10 XPASS, 1 SKIP | Неизменённый продукт и harness. Старый runner записал 11 FAIL, из них 10 `[XPASS(strict)]`; разбор виден в `summary.json`/JUnit. |
| Исходный baseline pytest | **6 FAIL, 853 PASS, 39 SKIP, 1 ERROR** | Переносимость A03/A08 на Windows и старые ожидания; подробности QA-WIN-003/004. |
| Генерация контрактов / A01 smoke / ownership self-test | PASS / **37 PASS** / PASS | Схемы и SYNTHETIC API-путь; не LIVE/CV/системная блокировка. |
| Повтор `run_qa.py --label candidate_a09_reviewed --expected-sha …` | **363 PASS, 0 FAIL, 6 XFAIL, 0 XPASS, 1 SKIP** | Продукт неизменён. Исправлены только A09 static scan, учёт XPASS, старые xfail-маркеры; добавлен тест классификации JUnit. Harness hashes и dirty-флаг записаны. |
| Реальные модели: `pytest backend/proctor/phone/tests/test_real_model.py backend/proctor/attention/tests/test_mediapipe_real.py -q -rA` | **27 PASS, 6 SKIP** | Настоящие ONNX/MediaPipe загружаются, inference/геометрия/контракты работают; пропущены проверки с внешними размеченными изображениями. Это не оценка точности на участниках. |
| `npm ci`, `npm run typecheck`, `npm run build` | PASS | Установлены зависимости по package-lock, собраны main/preload/renderer. Бинарник Electron устанавливается отдельным штатным install.js. |
| `npm run test:shell` | **62 PASS, 1 FAIL, 1 SKIP** | Один устаревший assert о позднем focus event — QA-WIN-005. Не ручная проверка клавиш. |
| A02 `verify-live --camera 0 --max-index 0 --seconds 15` | **9 PASS, 5 NOT_RUN**, overall INCOMPLETE | Настоящая камера DSHOW 640×480@30, 450 кадров/15 с, capture 30 FPS, p95 110 мс при **симулированных** потребителях, три рестарта без утечки потоков. Кадры не сохранялись. |
| Настоящий Electron, обычный запуск | **FAIL / P0 QA-WIN-007** | 12 startup self-test PASS, затем приложение само завершилось (exit 0) до рабочего окна. Dispose скрытого probe вызывает безусловный window-all-closed → app.quit. |
| Electron с явным `--diagnostic-no-selftest` | **PASS только запуска UI/backend**, LIVE BLOCKED | Процесс electron.exe, заголовок Qorgau Exam, Responding=true; backend READY, stream connected. Матрица: blocked=0, unverified=15. Экзамен не создавался. Визуальная проверка содержимого окна не выполнена. |

Исходные логи сохранены в `20261008T070227Z_candidate_de7290509bf5/`, повтор —
`20261008T072100Z_candidate_a09_reviewed_de7290509bf5/`. Дополнительные измерения —
`candidate_windows_setup/`.

## Подготовка

Python 3.12.14: `uv sync --frozen --extra cv --extra dev`, lockfile не изменён.
Node 24.17.0 / npm 11.13.0; Electron 43.7.5 из package-lock.
Модели подготовлены командами владельцев:
`python -m proctor.phone.prepare --download` и `python -m proctor.attention.model_tool fetch`.
SHA-256/размер обеих моделей совпали с manifest; phone warmup CPU около 91 мс.
Штатный Electron downloader завис на передаче body. Тот же официальный архив загружен
HTTP Range-частями (151231099 байт), объединён и проверен по `electron/checksums.json`:
`7acfa0646793f912ff983c8db8c3a145dc18ee40fe3d11a01840fd59cb76e5a2`.
Архив передан штатному кэшу и `install.js`; версия/lockfile не подменялись.
Никакие настройки Windows не менялись. Нативная блокировка не включалась.

`packaging/launch-live-tonight.py` проверяет точный SHA и отсутствие изменений продукта,
готовность моделей/сборки, включает startup self-test, принудительно задаёт native enforcement=0.
Консоль показывает PID и одноразовый PIN преподавателя. Логи на диске маскируют PIN/token;
данные теста и диагностика находятся вне checkout в `tmp/live-tonight/`.
Аварийное сочетание: Ctrl+Alt+Shift+F12; резерв — Ctrl+Alt+Del и завершение **только своего PID**.
Запуск не создаёт экзамен и не подтверждает согласие за человека.

## Граница приёмки

**LIVE заблокирован P0 QA-WIN-007**: приложение завершает себя при закрытии окна самопроверки.
Минимальный предлагаемый патч для A06/A01: `../fixes/QA-WIN-007-A06-startup.patch`;
применимость проверяется `git apply --check`, код кандидата им не изменён. Реальное исправление
требует нового кандидата A01 и повторного запуска на новом SHA.
Диагностический ключ launcher `--diagnostic-no-selftest` открывает UI с unverified capabilities,
**не заменяет LIVE и не обходит preflight**. `desktop/native/VERIFICATION.json` не менялся.

**Полный release gate пока не пройден**: также baseline FAIL и Windows-ограничения не приняты.
P0 для демонстрации всех направлений: QA-WIN-001 (A06: отсутствует подтверждённая системная защита).
Камера и модели подготовлены; переход к LIVE ждёт исправления старта. Жесты, калибровка, UI, реальные сочетания клавиш,
аварийный выход, клавиатура/фокус после выхода требуют ручного прогона.
В этом окружении инструмент управления Windows UI дважды не инициализировался
(`windows sandbox failed: setup refresh had errors`), поэтому видимое окно не объявляется проверенным по скриншоту.

Ручной протокол: `../scenarios/LIVE_TONIGHT.md`. Поля пусты; пользователь ещё не сообщил результатов.
`ACCEPTANCE_MATRIX.md` содержит историческую приёмку BOOTSTRAP и не переписан вымышленными LIVE-результатами.
После ответа пользователя записать только сообщённые строки с SHA/датой, ошибки назначить владельцам,
затем commit/push `codex/proctor-A09`.
