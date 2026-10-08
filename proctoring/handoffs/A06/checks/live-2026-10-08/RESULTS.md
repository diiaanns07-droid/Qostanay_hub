# A06-native: реальный тест на ноутбуке, 8 октября 2026

**Аварийный выход:** Ctrl+Alt+Shift+F12; закрытие тестовой консоли; Ctrl+Alt+Del → Диспетчер задач.
В этих двух запусках фактически использовалось только первое сочетание.

Код: `29778e9f23200132427c7a31d10098e044464c92`, ветка `codex/proctor-A06-native`.
Windows 11: `platform=win32`, `os_release=10.0.26200`; Python 3.12.14, обычный пользователь.
Проверка выполнялась отдельно от Electron через `desktop/native/live_console.py`.
Разрешение капитана в чате: «Да, готов проверить оба режима».
Реальные нажатия выполнял капитан; автоматической генерации нажатий не было.

| Запуск | Начало UTC | Параметры | Фактический выход |
|---|---|---|---|
| dry-run | 2026-10-08 10:37:41 | `--mode dry-run`, helper `--max-minutes 2` | `emergency_hotkey` |
| enforce | 2026-10-08 10:40:13 | `--mode enforce`, helper `--max-minutes 2` | `emergency_hotkey` |

| Проверка | Наблюдение | Результат |
|---|---|---|
| dry-run: Win | `key:win`, `swallowed:false`; затем SearchHost.exe | PASS по журналу |
| dry-run: Alt+Tab | `key:alt_tab`, `swallowed:false`; затем смена foreground explorer/Code | PASS по журналу |
| dry-run: PrtScn | `key:print_screen`, `swallowed:false`; затем SnippingTool.exe | PASS по журналу |
| Foreground watch | Только basename процесса, `foreign:true` для сторонних процессов | PASS: detected_only |
| enforce: Win | `swallowed:true`; капитан подтвердил, что меню «Пуск» не открывалось | PASS |
| enforce: Alt+Tab | `swallowed:true`; капитан подтвердил отсутствие переключения | PASS |
| enforce: PrtScn | `swallowed:true`; капитан подтвердил отсутствие снимка | PASS |
| Ctrl+Alt+Shift+F12, оба режима | `bye:emergency_hotkey`; капитан подтвердил выход | PASS |
| Клавиатура и фокус после enforce | Капитан подтвердил ввод, Win, Alt+Tab и переключение фокуса | PASS |

Сообщение капитана после dry-run: «после ктрл алт шифт ф12 терминал закрылся» (фрагмент).
После вопроса о фактическом подавлении Win/Alt+Tab/PrtScn и восстановлении ввода, Win/Alt+Tab/фокуса:
«Да, всё подавлялось; после выхода всё работает».
Само по себе `swallowed:true` не использовалось как доказательство поведения Windows.

Приложенные файлы — копии реальных журналов и метаданных без изменения содержимого:
[dry-run.machine.json](dry-run.machine.json), [dry-run.events.jsonl](dry-run.events.jsonl),
[enforce.machine.json](enforce.machine.json), [enforce.events.jsonl](enforce.events.jsonl).
Поле `acceptance: NOT_RECORDED` в исходных метаданных намеренно сохранено: автоматический harness
не выносит решение о приёмке. Подтверждение человека записано здесь отдельно.

В обоих журналах нет `error`, последний event — `bye:emergency_hotkey`. Проверка процессов после
завершения не обнаружила A06 `qorgau_guard.py` / `live_console.py`. Windows-настройки не менялись,
чужие процессы не завершались. Никакие набранные тексты, заголовки окон или содержимое буфера не записывались.

Не проверены LIVE: Alt+Esc, Ctrl+Esc, LWin/RWin по отдельности, восстановление PrtScn после выхода,
`stop`, EOF, потеря heartbeat, смерть родителя, истечение 2 минут, закрытие консоли, переполнение очереди,
ошибки хука, длительная нагрузка, запуск и остановка helper из реального Electron.
Эти журналы не заменяют автоматические тесты жизненного цикла и приёмку полной сборки.

QA-WIN-001: тест отдельного helper для трёх основных сочетаний и аварийного выхода — PASS;
закрытие пункта на уровне общей Electron-сборки остаётся у A01/A09. В проведённых проверках P0 не выявлены.
Python-helper — неподписанный прототип. Ctrl+Alt+Del и UAC не блокируются.

После записи результатов: `node main/tests/run.mjs capabilities native` — 21 PASS, только заглушки.
Сам файл VERIFICATION.json проверен существующим парсером: 4 валидные записи, точное совпадение
platform/os_release с машиной, ограничения длины соблюдены. Отдельная проверка матрицы подтвердила:
три `blocked` появляются при matching enforce; dry-run, отсутствие helper, другая версия ОС или
другая платформа не получают эти статусы. При этих проверках реальный hook не устанавливался.
