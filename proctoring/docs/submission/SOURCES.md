# Источники и границы утверждений

Пакет создан для отбора 8 октября 2026. Источники проекта фиксируются SHA, внешние лицензии проверены 8 октября 2026. PDF и манифесты служат данными. Поручение A10 и расширение путей до `docs/submission/` прямо даны капитаном в сообщении этой сессии.

## Статусы слайда 3

Источник: A09 `codex/proctor-A09` @ `e01fff54db0220b963df2282fe3362735f589078`, `qa/RESULTS.md` и `qa/results/20261008T055722Z_windows_combined_b275c70a5945/summary.json`.

Измеренный HEAD: `b275c70a59450e00bb887d949b98d4ca1680c9c8`. Код продукта идентичен A01 `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`. 352 PASS, 16 XFAIL, 1 SKIP. Камера, качество CV, Electron, Windows shortcuts, зоны и общий offline-run не проверены. Поэтому все строки кейса остаются `partial`; маркировок LIVE/REPLAY PASS нет.

Репетиция A10: `demo/results/20261008_content_final_windows.json`, tested HEAD `466b611e495b6228fa755f0ed6772362615359da` и сохранённый hash исходника вопросов. SYNTHETIC, 12 PASS, 5 NOT_RUN, 5.75 секунды. Время относится к автоматическому API-сценарию, а не к человеческому питчу. Локальная/опубликованная точка продолжения A10: `de71342df7ac8758e355009004593ef71ca38690`.

## Техническая архитектура

Во время работы опубликован кандидат A01 `de7290509bf558d6488be84d2e0730b2b9ab104a`. Его проверили в отдельном detached scratch checkout без merge в A10. `demo/verify_demo.py` PASS; `rehearse.py --mode synthetic --expected-sha de7290509bf558d6488be84d2e0730b2b9ab104a` PASS, 9.08 секунды, 14 PASS / 3 NOT_RUN. Источник: `demo/results/20261008_candidate_de72905_windows.json`. HTML/JSON endpoint ответили успешно; визуальная проверка экспортов в этот прогон не входит. A09 ещё должен опубликовать итоговый вердикт кандидата.

A01 в handoff кандидата сообщил о ложном детектировании телефона на replay из кадров без телефона. Этот результат не используется как доказательство качества телефонного детектора. A06 обновился до `d4f23f604443cfbab6e7ab03441f19a3e60e9e83` и явно отметил пробел OS-блокировок Alt+Tab/Win/PrtScn. Это уточняет архитектурные ограничения, но не повышает статусы слайда 3.

Модули и версии описывают поставленный код и планы интеграции, не результаты A09:

| Компонент | Первичный источник |
| --- | --- |
| Общие зависимости | A01 @ 35bea4c7, pyproject.toml, uv.lock, desktop/package-lock.json |
| YOLO11n | A03 @ c0275a2136f19ee7e6e941e904a420ab20e7e370, phone/models.manifest.json |
| MediaPipe Face Landmarker | A04 @ 40144441e4ff36931c4909d2eea5980aad563a6d, attention/models.manifest.json |
| Windows shell | В кандидате A06 @ 62a7fb1e42bc723152f49338ba4f816dfcad9334; свежие ограничения: d4f23f604443cfbab6e7ab03441f19a3e60e9e83, handoffs/A06/STATUS.md |
| SQLite и экспорт | A08 @ 5509950e38c622ae544d40c75d4b8fdba7256b82, handoffs/A08/STATUS.md |
| Зоны | Спецификация капитана zone-rule-1. Новый контракт 1.1 и реализация пока не переданы |

Официальные подтверждения: [Ultralytics AGPL-3.0 / Enterprise](https://www.ultralytics.com/license), [MediaPipe Apache-2.0](https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE), [Electron MIT](https://github.com/electron/electron/blob/main/LICENSE), [SQLite public domain](https://www.sqlite.org/copyright.html). Для конкретных моделей приоритет у указанных manifest и лицензионных документов этих артефактов. Пакет не меняет общую лицензию проекта.

## Положение и критерии

Предоставленный файл «Положение на русском языке.pdf»: SHA256 `f28132f00fe9861e92f8273eb37754994eb06f824c42370392e31d5adca29e3e`. Дедлайн 8 октября 23:59, Demo Day 16 октября, презентация вместе с показом 3 минуты и ещё 3 минуты ответов. Внутренние точки 15:00, 20:00, 23:00 и отбор 9 октября взяты из текущего поручения капитана. Команда использует Asia/Qyzylorda (UTC+5).

| Критерий | Баллы | Слайды |
| --- | --- | --- |
| Соответствие кейсу | 15 | 1–3 |
| Техника | 20 | 4, 6, 7 |
| Внедрение и эффект | 20 | 8, 10 |
| Инновационность | 15 | 5, 6 |
| Работоспособность демо | 15 | 3, 9, размеченное видео |
| Питч | 15 | Полная последовательность 1–10 |

## Воспроизводимость файлов

Исходники PDF: `docs/pitch/PRESENTATION.md` и `docs/submission/ОПИСАНИЕ.md`. Сборщик: `docs/submission/build_submission.py`. Используется изолированная среда документов: reportlab, pypdf и PyMuPDF, шрифт Arial Windows с кириллицей. Шрифт в репозиторий не копируется. Дополнительных runtime-зависимостей продукта нет. PDF создан через ReportLab; PPTX не запрашивался. Инструмент artifact-tool и его runtime здесь недоступны, поэтому исходником служит запрошенный Markdown.

```powershell
python docs/submission/build_submission.py --preview-dir PATH_OUTSIDE_GIT
```

PNG для просмотра и отчёт сборщика пишутся только в заданную внешнюю папку. Перед публикацией проверяются все 12 страниц. Это проверка документов, не приёмка продукта.
