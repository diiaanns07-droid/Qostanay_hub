# Финальная QA Adal — 2026-10-08

Начато 19:03 Asia/Qyzylorda. Ветка `codex/proctor-final-qa`.
Первый свежий fetch: `codex/proctor-integration @ a883e968d074adfb85601b599c801b7f65de5c1f`.
Повторный fetch: **`f12cea4b92d3e6308c99ff493f80fdd9999bf182`**, checkout обновлён fast-forward.
Полный прогон f12cea4 завершён. Затем fast-forward до **f6525a6f7bd46c2347a5d7a7e010060c39191cfb**: A15 desk scan, тексты и безопасное аудио класса. Именно f6525a6 запущен в verify-candidate для LIVE; изменённые пути перепроверяются.
Отдельный checkout `worktrees/final-qa-20261008`. Код продукта не меняется.
Статусы ниже обновляются по фактическим результатам; PENDING не означает PASS.

| Проверка | Статус | Результат / доказательство |
|---|---|---|
| pytest backend + classroom + classreview + desktop/native + desktop/renderer/tests @ a883e96 | FAIL | **1420 PASS / 6 FAIL / 41 SKIP**, 247,92 с; [лог](../qa/results/final_a883e96/full-pytest.txt), [JUnit](../qa/results/final_a883e96/full-pytest.xml) |
| Тот же полный pytest @ f12cea4 | FAIL | **1448 PASS / 16 FAIL / 10 SKIP**, 268,31 с; [лог](../qa/results/final_f12cea4/full-pytest.txt). Реальные модели скопированы в ignored `proctoring/models`; дополнительных загрузок не было |
| qa/run_qa.py @ a883e96 | FAIL, частично устарел | **354 PASS / 12 FAIL / 4 XFAIL / 2 XPASS / 1 SKIP** по pytest; [лог](../qa/results/20261008T141155Z_final_a883e96_a883e968d074/pytest.txt). 12 FAIL повторены по одному |
| npm run build @ a883e96 | PASS | [лог](../qa/results/final_a883e96/npm-build.txt); повтор f12cea4 идёт |
| npm run typecheck @ a883e96 | PASS | contracts/main/renderer; [лог](../qa/results/final_a883e96/npm-typecheck.txt) |
| npm run test:shell @ a883e96 | PASS | **106 PASS / 1 SKIP** (Windows SIGTERM escalation test); реальный backend включён, [лог](../qa/results/final_a883e96/npm-shell.txt) |
| Renderer TS unit @ a883e96 | PASS | **17/17**, [лог](../qa/results/final_a883e96/renderer-unit.txt) |
| LIVE preflight и калибровка @ f6525a6 | PASS, подтверждено капитаном | «Все обязательные зелёные, калибровка завершилась». Запуск verify-candidate из чистого PowerShell, QA adapter ограничивает native helper двумя минутами. REPLAY/HTML проверяются отдельно |
| C2 Start-AdalClassDemo.ps1 -CheckOnly @ a883e96 | PASS | Порт 18765; history mounted, **T03 UI=200**, clips=404 для несуществующего id (не 501), студент online, build готов; свои процессы остановлены; [лог](../qa/results/final_a883e96/class-check.txt) |
| Сборка / typecheck / shell / renderer @ f12cea4 | PASS | build=0, все TS PASS, shell 106 PASS + 1 SKIP, renderer 17 PASS; [логи](../qa/results/final_f12cea4/) |
| Сборка / shell / renderer @ f6525a6 | PASS | build=0; shell **107 PASS / 1 SKIP**; renderer **22 PASS**; [логи](../qa/results/final_f6525a6/) |
| typecheck @ f6525a6 | FAIL, новый QA-FINAL-009, A01/A06 | `backend.integration.test.ts:151–152`: TS18047, dsDone может быть null; contracts и renderer PASS. [Отдельный повтор main TS — FAIL](../qa/results/final_f6525a6/typecheck-main-isolated.txt) |
| Изменённые Python-пути f6525a6: deskscan/evidence/backend tests/audio класса | PASS | **112 PASS / 3 SKIP**; [лог](../qa/results/final_f6525a6/changed-paths.txt). QA-FINAL-001 закрыт поставкой integration: оба прежних FAIL аудио проходят |
| REPLAY через продуктовый renderer + настоящий backend @ f6525a6 | PASS | **30/30**: zone_a_green_01 → green, zone_b_yellow_02 → yellow, zone_c_red_01 → red. Экзамен, сводка, экспорт и открытие HTML; REPLAY виден. [Лог](../qa/results/final_f6525a6/replay.txt). Chromium transport, без native guard/диалога сохранения Electron; приватные скриншоты/HTML вне Git в tmp/final-qa-replay-f652 |
| Полный pytest @ f6525a6 | FAIL | **1463 PASS / 14 FAIL / 10 SKIP**, 306,50 с. Все 14 повторены отдельно: 12 FAIL, 2 PASS (флейки). [Лог](../qa/results/final_f6525a6/full-pytest.txt), [повторы](../qa/results/final_f6525a6/isolated-results.json) |
| C2 Start-AdalClassDemo.ps1 -CheckOnly @ f6525a6 | PASS | T03 HTTP 200, студент online; собственные процессы остановлены; [лог](../qa/results/final_f6525a6/class-check.txt) |
| run_qa.py, повтор f6525a6 | INTERRUPTED | Прерван без итоговой строки/JUnit/summary; не засчитывается как завершённый прогон |
| Проверка моделей телефона / лица / сверки лица | PASS | Официальные CLI --check/verify, все exit=0; реальная загрузка YOLO/YuNet/SFace; [логи](../qa/results/final_f12cea4/) |
| Свежесть integration перед сдачей | PENDING | Последний взятый SHA f6525a6; повторный fetch перед сдачей |

## P0 — мешает записи видео

Пока не установлены; проверки продолжаются.

## P1 и воспроизведение FAIL

| FAIL / замечание | Известный/новый; владелец | Повтор в одиночку / вывод |
|---|---|---|
| A03 `test_install_success_writes_atomically` | Известный QA-WIN-003; A03 | FAIL: Windows `phone\\m.onnx` против `phone/m.onnx`; [1](../qa/results/final_a883e96/isolated-1.txt) |
| A03 `test_install_source_error_leaves_nothing_and_keeps_existing_target` | Известный QA-WIN-003; A03 | FAIL: тот же разделитель пути; [2](../qa/results/final_a883e96/isolated-2.txt) |
| A03 `test_app_health_lists_phone_model_missing` | Известный QA-WIN-003; A03 | FAIL: no_network блокирует loopback `socketpair()` Windows; [3](../qa/results/final_a883e96/isolated-3.txt) |
| C2 `test_redelivered_command_is_not_executed_twice_and_ack_has_code_and_result` | Известный плавающий эффект QA-WIN-003; A03, наблюдение C2 | **PASS отдельно**. В общем прогоне unraisable `BaseEventLoop.__del__` после A03, а не провал ACK; [4](../qa/results/final_a883e96/isolated-4.txt) |
| T01 `test_audio_signaling_is_bound_to_an_acked_audio_session` | Новый QA-FINAL-001; T01/T05 | FAIL отдельно: legacy audio_start запрещён core.py с `audio_extension_required`; [5](../qa/results/final_a883e96/isolated-5.txt) |
| T01 `test_audio_ends_when_student_goes_offline` | Новый QA-FINAL-001; T01/T05 | FAIL отдельно по той же причине; [6](../qa/results/final_a883e96/isolated-6.txt) |
| QA `test_full_synthetic_flow` | Новый QA-FINAL-003 + известные временные зоны; A09/A01/A08 | FAIL отдельно: READY ожидает 1.0.0, сервер 1.2.0; summary имеет временные `review_zone*` вне schema; [QA1](../qa/results/final_a883e96/qa-isolated-1.txt) |
| QA `test_camera_unplugged_mid_exam_is_visible_and_session_survives` | Новый QA-FINAL-004; A09 | FAIL отдельно: тест сравнивает последний health без фильтра component, получает audio/noise_calibration; [QA2](../qa/results/final_a883e96/qa-isolated-2.txt) |
| QA `test_read_routes_valid_in_every_state[created]` | Известная временная модель A08; A01/A08/A09 | FAIL отдельно: summary review_zone* не входит в schema; [QA3](../qa/results/final_a883e96/qa-isolated-3.txt) |
| То же `[preflight]` | То же | FAIL отдельно; [QA4](../qa/results/final_a883e96/qa-isolated-4.txt) |
| То же `[calibrating]` | То же | FAIL отдельно; [QA5](../qa/results/final_a883e96/qa-isolated-5.txt) |
| То же `[ready]` | То же | FAIL отдельно; [QA6](../qa/results/final_a883e96/qa-isolated-6.txt) |
| То же `[running]` | То же | FAIL отдельно; [QA7](../qa/results/final_a883e96/qa-isolated-7.txt) |
| То же `[paused]` | То же | FAIL отдельно; [QA8](../qa/results/final_a883e96/qa-isolated-8.txt) |
| То же `[finished]` | То же | FAIL отдельно; [QA9](../qa/results/final_a883e96/qa-isolated-9.txt) |
| То же `[aborted]` | То же | FAIL отдельно; [QA10](../qa/results/final_a883e96/qa-isolated-10.txt) |
| QA `test_full_flow_offline_audit_guard` | Новый QA-FINAL-003; A09/A01/A08 | FAIL отдельно из-за READY/schema; доказательств внешнего download из этого FAIL нет; [QA11](../qa/results/final_a883e96/qa-isolated-11.txt) |
| QA `test_no_download_calls_in_backend_runtime_source` | Новый QA-FINAL-005; A09 | FAIL отдельно: static gate захватывает явные prepare audio/identity и разрешённый C2 HTTP uplink; [QA12](../qa/results/final_a883e96/qa-isolated-12.txt) |

**P1 QA-FINAL-002 (A06/A01):** main.ts жёстко передаёт helper `maxMinutes: 240`; env/параметра лимита нет.
Для разрешённого теста приготовлен QA-only adapter `qa/tools/final_guard_limit.py`, дописывающий `--max-minutes 2`.
Продуктовый helper не изменён; 3 теста с заглушкой PASS, self-check без хука предусмотрен. Запуск через `qa/tools/Start-FinalQa.ps1`.

**P1 QA-FINAL-003 (A01/A08):** временная расширенная SessionSummary не соответствует опубликованной JSON Schema.
Это известный долг интеграции, не основание переписывать контракт в QA.

**QA-FINAL-006 (A09):** summary.json run_qa считает 2 нестрогих XPASS как PASS (356 вместо 354 + 2 XPASS).
Здесь приведены реальные статусы pytest. XPASS: исправленные QA-OBS-003 (ответы) и QA-OBS-004 (удаление).
XFAIL: 3 варианта QA-OBS-005 (lax types) и QA-OBS-006 (uppercase LOCALHOST). Linux netns SKIP на Windows.

## Ограничения и безопасность прогона

Аварийный выход нативной защиты: **Ctrl+Alt+Shift+F12**.
Чужие экземпляры/процессы не закрываются. Свой Adal закрывается сразу после проверки.
Предыдущий LIVE YAMNet: тишина not_applicable из-за подтверждённого фонового разговора; это не новый FAIL продукта.
Computer-use не стартовал: sandbox setup error, затем trusted Node process exited после штатного reset/retry.
Капитан подтвердил зелёный обязательный preflight и завершение калибровки. После Ctrl+Alt+Shift+F12 сначала сообщил, что окно не вышло, затем — что Adal уже не виден. Проверка PID подтвердила завершение нашего Electron и его backend; осталась только тестовая PowerShell-консоль. Клавиатура/фокус в этом прогоне отдельно не подтверждены. По коду сочетание регистрируется при включении режима экзамена, снимает ограничения и прерывает сессию, но не закрывает приложение. Инструкция QA о закрытии окна этим сочетанием вне экзамена была неточной; это не доказанный отказ native hook.
Переименование отчёта было не выполнено на a883e96, но уже исправлено поставкой A08 0bc18e7 в свежей integration.
`product_tree_dirty=true` старого run_qa обусловлен новым QA-отчётом `handoffs/FINAL_QA.md`; продуктовые файлы не редактировались.

## Каждый FAIL полного прогона f12cea4 и отдельный повтор

| Тест | Классификация / владелец | Повтор и вывод |
|---|---|---|
| `test_evidence_report::test_report_renders_and_prints_in_chromium` | Новый сбой окружения; A08 / QA | PASS отдельно с PYTHONUTF8=1; продукт не менялся; [лог 1](../qa/results/final_f12cea4/isolated-1.txt) |
| `test_config_manifest_prepare::test_install_success_writes_atomically` | Известный QA-WIN-003; A03 | FAIL отдельно: разделитель пути; [лог 2](../qa/results/final_f12cea4/isolated-2.txt) |
| `test_config_manifest_prepare::test_install_source_error_leaves_nothing_and_keeps_existing_target` | Известный QA-WIN-003; A03 | FAIL отдельно: разделитель пути; [лог 3](../qa/results/final_f12cea4/isolated-3.txt) |
| `test_config_manifest_prepare::test_real_prepare_from_file_installs_and_check_loads` | Известная причина QA-WIN-003, ранее SKIP; A03 | FAIL отдельно: разделитель пути; [лог 4](../qa/results/final_f12cea4/isolated-4.txt) |
| `test_config_manifest_prepare::test_app_health_lists_phone_model_missing` | Известный QA-WIN-003; A03 | FAIL отдельно: запрет socketpair; [лог 5](../qa/results/final_f12cea4/isolated-5.txt) |
| `test_real_model::test_real_phone_only_config_does_not_report_other_objects` | Новый QA-FINAL-007; A03 | FAIL отдельно: object_class_names пересекается с class_names теста; [лог 6](../qa/results/final_f12cea4/isolated-6.txt) |
| `test_real_model::test_real_inference_is_deterministic` | Новый QA-FINAL-007; A03 | FAIL отдельно: raw_outputs=8 вместо 2, при first==second; тест не учитывает несколько проходов; [лог 7](../qa/results/final_f12cea4/isolated-7.txt) |
| `test_real_model::test_real_boxes_map_to_unmirrored_normalized_frame_coordinates[320-rect]` | Новый QA-FINAL-007; A03 | FAIL отдельно: конфигурация теста пересекает классы; [лог 8](../qa/results/final_f12cea4/isolated-8.txt) |
| `test_real_model::test_real_boxes_map_to_unmirrored_normalized_frame_coordinates[320-square]` | Новый QA-FINAL-007; A03 | FAIL отдельно: конфигурация теста пересекает классы; [лог 9](../qa/results/final_f12cea4/isolated-9.txt) |
| `test_real_model::test_real_boxes_map_to_unmirrored_normalized_frame_coordinates[640-rect]` | Новый QA-FINAL-007; A03 | FAIL отдельно: конфигурация теста пересекает классы; [лог 10](../qa/results/final_f12cea4/isolated-10.txt) |
| `test_real_model::test_real_boxes_map_to_unmirrored_normalized_frame_coordinates[640-square]` | Новый QA-FINAL-007; A03 | FAIL отдельно: конфигурация теста пересекает классы; [лог 11](../qa/results/final_f12cea4/isolated-11.txt) |
| `test_real_model::test_real_prepadded_frame_gives_identical_boxes` | Новый QA-FINAL-007; A03 | FAIL отдельно: конфигурация теста пересекает классы; [лог 12](../qa/results/final_f12cea4/isolated-12.txt) |
| `test_uplink_app::test_without_env_the_backend_has_no_uplink` | Известный плавающий эффект QA-WIN-003; A03 / C2 | PASS отдельно; отложенный BaseEventLoop.__del__ после A03; [лог 13](../qa/results/final_f12cea4/isolated-13.txt) |
| `test_persistence_audio::test_audio_signaling_is_bound_to_an_acked_audio_session` | QA-FINAL-001; T01/T05 | FAIL отдельно на f12cea4; в f6525a6 пришло исправление теста, перепроверяется; [лог 14](../qa/results/final_f12cea4/isolated-14.txt) |
| `test_persistence_audio::test_audio_ends_when_student_goes_offline` | QA-FINAL-001; T01/T05 | FAIL отдельно на f12cea4; в f6525a6 пришло исправление теста, перепроверяется; [лог 15](../qa/results/final_f12cea4/isolated-15.txt) |
| `test_review_e2e::test_teacher_flow_in_browser_with_restart` | Новый QA-FINAL-008; T03 | FAIL отдельно: нет bundled Chromium; с QA adapter для установленного Chrome — FAIL strict locator .t3-test-label (2 элемента вместо 1), клип HTTP 206 получен; [лог 16](../qa/results/final_f12cea4/isolated-16.txt) |

Для T03 [повтор с установленным Chrome](../qa/results/final_f12cea4/classreview-installed-chrome.txt). Ничего не устанавливалось; NODE_OPTIONS подключает только QA adapter выбора существующего Chrome. Тест останавливается на неоднозначном селекторе до проверки рестарта; не считается PASS.
