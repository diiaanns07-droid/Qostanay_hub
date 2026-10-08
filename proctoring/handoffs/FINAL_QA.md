# Финальная QA Adal — 8 октября 2026

**Подтверждённых P0, мешающих проверенному видео-сценарию, нет. Автоматическая приёмка остаётся FAIL.**
Проверенный продукт: `codex/proctor-integration @ 0bb070a92ed9a141d0c80a4c585bf01c88e2414a`.
Повторный fetch перед финализацией подтвердил тот же SHA. QA-ветка: `codex/proctor-final-qa`,
отдельный checkout `worktrees/final-qa-20261008`; HEAD при свежих прогонах — `2ade8cae1f324ce9920b1b9c1fba147e8eba3234`.
Продуктовые файлы совпадают с integration: собственных правок backend/contracts/desktop/classroom/classreview нет.
Собственные изменения — отчёты, баги, результаты и QA-инструменты. Старые результаты ниже сохраняют исходные SHA.

## Проверки на 0bb070a

| Проверка | Статус | Результат и доказательство |
|---|---|---|
| Полный pytest: backend, classroom, classreview, desktop/native, desktop/renderer/tests | **FAIL** | **1474 PASS / 13 FAIL / 10 SKIP / 0 XFAIL / 0 XPASS**, 423,55 с; [лог](../qa/results/final_0bb070a/full-pytest.txt), [JUnit](../qa/results/final_0bb070a/full-pytest.xml). Все FAIL повторены отдельно: 12 FAIL, 1 PASS (флейк) |
| qa/run_qa.py | **FAIL** | **381 PASS / 2 FAIL / 4 XFAIL / 0 XPASS / 1 SKIP**, 255,08 с; [отчёт](../qa/results/20261008T152111Z_final_0bb070a_2ade8cae1f32/summary.md), [полный лог](../qa/results/20261008T152111Z_final_0bb070a_2ade8cae1f32/pytest.txt). Оба FAIL повторены отдельно |
| npm run build | PASS | [Лог](../qa/results/final_0bb070a/npm-build.txt) |
| npm run typecheck | PASS | contracts/main/renderer; QA-FINAL-009 закрыт поставкой integration; [лог](../qa/results/final_0bb070a/npm-typecheck.txt) |
| npm run test:shell | PASS | **113 PASS / 1 SKIP** (SIGTERM escalation на Windows); [лог](../qa/results/final_0bb070a/npm-shell.txt) |
| Renderer TS unit | PASS | **22/22**; [лог](../qa/results/final_0bb070a/renderer-unit.txt) |
| Настоящий Electron, exam-surface fixture | PASS | **24 сценария**, все **22 self-test проверки PASS**; [лог](../qa/results/final_0bb070a/electron-surface.txt). Скрытое окно, локальные страницы, синтетическое состояние класса; без камеры, микрофона и native guard |
| REPLAY через продуктовый renderer + настоящий backend/CV | PASS | **33/33**; green/yellow/red на трёх записях, сводка, экспорт, открытие HTML; [лог](../qa/results/final_0bb070a/replay-current-auth.txt), [сводка без медиа](../qa/results/final_0bb070a/replay-summary.json) |
| Start-AdalClassDemo.ps1 -CheckOnly | PASS | **T03 HTTP 200**, history mounted, студент online; [лог](../qa/results/final_0bb070a/class-check.txt). HTTP 404 для несуществующего clip id ожидается, это не проверка воспроизведения настоящего клипа. Свои процессы остановлены |

run_qa сообщает `product tree DIFFERS: handoffs/FINAL_QA.md`: это сам отчёт QA вне старого списка исключений.
Отдельный git diff проверил равенство продуктовых путей integration. Результаты pytest выше не изменялись ради зелёного статуса.

## LIVE на ноутбуке: точная граница доказательства

**PASS на f6525a6f7bd46c2347a5d7a7e010060c39191cfb**, Windows 11, версия ОС 10.0.26200.
Капитан подтвердил: «Все обязательные зелёные, калибровка завершилась».
Запуск произведён из обычного PowerShell, ELECTRON_RUN_AS_NODE снят; LOCALAPPDATA/QorgauExam/models,
DEMO_OPERATOR=1, NATIVE_ENFORCE=1. Отдельный QA adapter ограничивал helper `--max-minutes 2`;
его три теста и self-check прошли, продуктовый helper не менялся.

Read-only проверка локальной БД подтверждает сессию `s-20261008-193247-478c92d9`, state=finished,
калибровку completed, **центр/лево/право/верх/низ — ok, по 20/20 образцов**.
Экзамен шёл 14:33:37–14:33:44 UTC. [Санитизированная запись](../qa/results/final_f6525a6/live-confirmation.json).
Модели телефона, лица и сверки лица отдельно загружены штатными CLI --check/verify;
[логи](../qa/results/final_f12cea4/). Обязательная защита среды прошла по сообщению капитана;
отдельного нового подтверждения строки RustDesk не запрашивалось.

После инструкции про Ctrl+Alt+Shift+F12 капитан сначала сообщил, что окно не вышло, затем — что Adal не виден.
Наши сохранённые PID Electron и backend уже отсутствовали; чужие процессы не завершались.
**Повтор hotkey, восстановление клавиатуры и фокуса в этой QA не подтверждены человеком.**
Инструкция QA была неточной: сочетание регистрируется при экзамене, снимает ограничения и прерывает сессию,
но не обязано закрывать приложение. Вне экзамена используется обычный крестик окна.
На обновлённом 0bb070a физическая LIVE-калибровка и native-клавиши повторно не проверялись.

Аварийное снятие ограничений в экзамене: **Ctrl+Alt+Shift+F12**; запасной путь — **Ctrl+Alt+Del → Диспетчер задач**.
Ctrl+Alt+Del и UAC не блокируются. Python guard — неподписанный прототип.

## REPLAY и отчёт

| Запись | Ожидание | Факт |
|---|---|---|
| zone_a_green_01 | зелёная | green, HTML открылся |
| zone_b_yellow_02 | жёлтая | yellow, HTML открылся |
| zone_c_red_01 | красная | red, HTML открылся |

Во всех трёх UI/HTML виден REPLAY, заголовок и имя продукта — **Adal**.
Внутренние `qorgau.v1` и `qorgau.report.v1` сохранены согласно заданию.
В отчёте красной записи видны русские подписи телефона и второго лица.
Приватные HTML и скриншоты — `C:\Qostanay_hub-codex-proctoring-prompts\tmp\final-qa-replay-0bb-auth`, вне Git.
Прогон использует Chromium transport и настоящий backend; нативный диалог сохранения Electron не проверяет.

Исходный штатный сценарий отдельно получил **3 PASS / 1 FAIL**: после нового session bind появляется PIN,
а тест ждёт «Причина». [Лог](../qa/results/final_0bb070a/replay.txt), QA-FINAL-011 (A07/A09).
QA adapter [replay-current-auth.mjs](../qa/tools/replay-current-auth.mjs) вводит тот же одноразовый PIN
через UI повторно после preflight. Исходные проверки сохранены, аутентификация не обходится.

Точная последовательность для видео: «Дополнительные настройки» → «запись (replay)» → ID записи → согласие →
«Проверить устройства» → **снова «Режим преподавателя», PIN текущего запуска** → «Без калибровки…» → причина
«REPLAY — записанное видео» → «Пропустить калибровку» → «Начать экзамен» → дождаться конца записи →
«Завершить экзамен» → «Завершить» → вход преподавателя → «К итогу →» → «Сохранить отчёт HTML».

## Каждый FAIL полного прогона и отдельный повтор

Классификация «новый» означает найденный этой финальной QA; находки f12cea4/f6525a6 уже известны к прогону 0bb070a.
Полные nodeid сохранены в [isolated-results.json](../qa/results/final_0bb070a/isolated-results.json).

| Тест | Известный/новый, владелец | Отдельный повтор |
|---|---|---|
| `test_install_success_writes_atomically` | Известный QA-WIN-003; A03 | **FAIL** — Разделитель Windows `phone\m.onnx` против `phone/m.onnx`; [лог 1](../qa/results/final_0bb070a/isolated-1.txt) |
| `test_install_source_error_leaves_nothing_and_keeps_existing_target` | Известный QA-WIN-003; A03 | **FAIL** — Разделитель Windows `phone\m.onnx` против `phone/m.onnx`; [лог 2](../qa/results/final_0bb070a/isolated-2.txt) |
| `test_real_prepare_from_file_installs_and_check_loads` | Известный QA-WIN-003; A03 | **FAIL** — Разделитель Windows `phone\m.onnx` против `phone/m.onnx`; [лог 3](../qa/results/final_0bb070a/isolated-3.txt) |
| `test_app_health_lists_phone_model_missing` | Известный QA-WIN-003; A03 | **FAIL** — no_network запрещает loopback socketpair Windows; [лог 4](../qa/results/final_0bb070a/isolated-4.txt) |
| `test_real_phone_only_config_does_not_report_other_objects` | Новый в этой QA, QA-FINAL-007; A03 | **FAIL** — Конфигурация теста пересекает object_class_names и class_names; [лог 5](../qa/results/final_0bb070a/isolated-5.txt) |
| `test_real_inference_is_deterministic` | Новый в этой QA, QA-FINAL-007; A03 | **FAIL** — 8 ONNX-вызовов вместо прежних 2; детерминизм результата сохраняется; [лог 6](../qa/results/final_0bb070a/isolated-6.txt) |
| `test_real_boxes_map_to_unmirrored_normalized_frame_coordinates[320-rect]` | Новый в этой QA, QA-FINAL-007; A03 | **FAIL** — Конфигурация теста пересекает object_class_names и class_names; [лог 7](../qa/results/final_0bb070a/isolated-7.txt) |
| `test_real_boxes_map_to_unmirrored_normalized_frame_coordinates[320-square]` | Новый в этой QA, QA-FINAL-007; A03 | **FAIL** — Конфигурация теста пересекает object_class_names и class_names; [лог 8](../qa/results/final_0bb070a/isolated-8.txt) |
| `test_real_boxes_map_to_unmirrored_normalized_frame_coordinates[640-rect]` | Новый в этой QA, QA-FINAL-007; A03 | **FAIL** — Конфигурация теста пересекает object_class_names и class_names; [лог 9](../qa/results/final_0bb070a/isolated-9.txt) |
| `test_real_boxes_map_to_unmirrored_normalized_frame_coordinates[640-square]` | Новый в этой QA, QA-FINAL-007; A03 | **FAIL** — Конфигурация теста пересекает object_class_names и class_names; [лог 10](../qa/results/final_0bb070a/isolated-10.txt) |
| `test_real_prepadded_frame_gives_identical_boxes` | Новый в этой QA, QA-FINAL-007; A03 | **FAIL** — Конфигурация теста пересекает object_class_names и class_names; [лог 11](../qa/results/final_0bb070a/isolated-11.txt) |
| `test_server_unavailable_at_start_exam_runs_locally` | Известный FLAKY QA-WIN-003; A03 / C2 | **PASS (FLAKY)** — Отложенный BaseEventLoop.__del__ после A03; отдельно PASS; [лог 12](../qa/results/final_0bb070a/isolated-12.txt) |
| `test_teacher_flow_in_browser_with_restart` | Новый в этой QA, QA-FINAL-008; T03 | **FAIL** — Strict locator .t3-test-label теперь находит два элемента; клип HTTP 206 получен; [лог 13](../qa/results/final_0bb070a/isolated-13.txt) |

## Остальные FAIL, P1 и ограничения

- **QA-FINAL-007, A03, P1 тестовой приёмки:** семь устаревших ожиданий реальной модели. Исправление тестов должен согласовать владелец; качество CV из этих падений не следует.
- **QA-FINAL-002, A06/A01, P1 тестового запуска:** main.ts передаёт helper 240 минут, внешнего параметра лимита нет. QA использует отдельный [Start-FinalQa.ps1](../qa/tools/Start-FinalQa.ps1) и adapter с лимитом 2 минуты; обычный A07-скрипт сам по себе этого лимита не гарантирует.
- **QA-FINAL-005, A09/A14, P2:** `test_no_download_calls_in_backend_runtime_source` — FAIL отдельно ([QA1](../qa/results/final_0bb070a/qa-isolated-1.txt)); `test_reviewed_network_scopes_cannot_hide_new_runtime_calls` — FAIL отдельно ([QA2](../qa/results/final_0bb070a/qa-isolated-2.txt)). Оба статически отмечают две операции urlopen в явной команде `audio.prepare.download_yamnet`; динамический автономный сценарий PASS. Это не доказательство загрузки модели из runtime.
- **QA-FINAL-008, T03, P2:** старый неоднозначный селектор останавливает E2E до проверки рестарта. Актуальная панель класса доступна, но этот полный сценарий не заявлен PASS.
- **QA-FINAL-010, A03, P2 FLAKY:** на f6525a6 `test_real_latency_p50_from_runtime_stats` один раз получил process p50=31,0 < infer p50=32,72 мс; отдельно PASS и на полном 0bb070a PASS. Лимит скорости не был нарушен. Проверить согласованность часов измерения.
- **QA-FINAL-006, A09, P2:** parser run_qa теряет non-strict XPASS. В начальном прогоне было 354 PASS + 2 XPASS, parser писал 356 PASS. В свежем прогоне XPASS=0: маркеры двух исправленных тестов сняты integration, сам parser не исправлялся.
- Закрыты поставкой integration и проверены: **QA-FINAL-001, 003, 004, 009**. Остались **4 XFAIL**: три lax typed-value случая QA-OBS-005 и uppercase LOCALHOST QA-OBS-006.
- SKIP: отсутствуют внешние наборы картинок A04/A03, Windows symlink privilege не менялась, тест отсутствия камеры неприменим при наличии камеры; дополнительный backend replay-тест без QORGAU_IT_REPLAY_DIR пропущен, вместо него отдельно выполнен описанный UI REPLAY. Linux netns неприменим на Windows.
- Предыдущий YAMNet LIVE: «тишина» невалидна из-за подтверждённого фонового разговора; тихий повтор не проводился. Это не новый ложный сигнал этого прогона.
- Computer-use недоступен: после sandbox error и штатного reset/retry kernel не стартовал. Поэтому физические действия выполнял капитан; скриншотов LIVE нет. Скриншоты REPLAY — отдельные артефакты браузерного теста.

Все текущие назначения владельцам: [qa/BUGS.md](../qa/BUGS.md). Новые pip/npm/SDK не устанавливались,
настройки Windows не менялись, модели и node_modules использованы из существующего окружения/локального кэша.
Повторы FAILED выполнялись отдельно; подмены продукта или отключения проверок ради PASS не было.

## Воспроизведение и история

Из `proctoring`, Python — существующий `verify-candidate/proctoring/.venv/Scripts/python.exe`;
PYTHONPATH указывает на **свой** backend/contracts/python/proctoring, PYTHONUTF8=1 и PYTHONIOENCODING=utf-8.
Модели — LOCALAPPDATA/QorgauExam/models (копия также в ignored proctoring/models для тестов Settings()).
Для headless тестов использован установленный Chrome и QA-only выбор executablePath через NODE_OPTIONS;
Playwright — существующий tmp/a08-render-tools/node_modules. Для видео NODE_OPTIONS очищен.

```text
python -m pytest backend classroom classreview desktop/native desktop/renderer/tests -q
python qa/run_qa.py --label final_0bb070a --expected-sha 2ade8cae1f324ce9920b1b9c1fba147e8eba3234 --base 0bb070a92ed9a141d0c80a4c585bf01c88e2414a
cd desktop
npm run build
npm run typecheck
npm run test:shell
node renderer/tests/run-unit.mjs
node main/tests/exam-surface.mjs
```

| Предыдущий срез | Полный pytest | QA |
|---|---|---|
| a883e96 | 1420 PASS / 6 FAIL / 41 SKIP | 354 PASS / 12 FAIL / 4 XFAIL / 2 XPASS / 1 SKIP |
| f12cea4, реальные модели | 1448 PASS / 16 FAIL / 10 SKIP | исходные 12 FAIL повторены отдельно |
| f6525a6 | 1463 PASS / 14 FAIL / 10 SKIP | повтор run_qa INTERRUPTED, без итога/JUnit; не засчитывается |

[История с каждым прежним FAIL и ссылками на повторы](https://github.com/diiaanns07-droid/Qostanay_hub/blob/2ade8cae1f324ce9920b1b9c1fba147e8eba3234/proctoring/handoffs/FINAL_QA.md).
