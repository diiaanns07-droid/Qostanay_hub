# Режим класса на одном ноутбуке — проверено и как записать видео (C2, 08.10)

База: `codex/proctor-integration` @ `ff3a889` (T01 сервер класса, T02 панель, C2 uplink, A01–A08), ветка
`codex/class-C2` (исправления C2 ниже). Ноутбук: ASUS TUF A15, Windows 11. Сервер, панель и студент — на одном
компьютере (`127.0.0.1`). Это НЕ проверка сети класса и не нагрузка: один реальный студент (ADAL), REPLAY-ролик.

## Что проверено сквозняком (17:32–17:37)
Настоящий сервер T01 (`python -m classroom.server`), панель T02 в Chrome (REAL-режим, вход по PIN), настоящий ADAL
(Electron + backend + C2 uplink) с `QORGAU_CLASS_SERVER=127.0.0.1:8765`, ролик `demo_class_red_loop`
(= `zone_c_red_01`, по кругу, согласие людей в кадре есть, файл вне Git). Автоматический прогон (Playwright):

| Шаг | Результат |
|---|---|
| ADAL подключается к `/ws/student` T01 (hello → welcome) | PASS (после исправления C2 №1 ниже) |
| карточка студента в панели: имя, компьютер (`Kayenlap`), «на связи», «Камера работает» | PASS |
| блок «Класс» в ADAL: «подключено» | PASS |
| преподаватель `start_exam` → экзамен идёт в ADAL (сессия была `ready`) | PASS, ack `succeeded` |
| эпизод (телефон) → карточка «Проверить в первую очередь», причины «Телефон поднят — 00:04», «Телефон в кадре — 00:04» | PASS (зона от A05 через `status`) |
| эпизоды в карточке студента (drawer), «есть клип» | PASS |
| **клип открывается** | **FAIL — сторона друга**: сервер отвечает `501 feature_not_installed` «Модуль истории и клипов (T03) не подключён»; C2 честно отвечает на `request_clip` `ok:false` «Не удалось загрузить клип (HTTP 501)» |
| `lock` с причиной → экран блокировки у студента с причиной, панель: «экран заблокирован», камера ok | PASS |
| `unlock` → экран снят | PASS |
| `audio_start` → отказ `not_supported` (WebRTC нет) | ожидаемо: команда `failed` с причиной |
Итог: 14–15 из 15–16 проверок; единственный FAIL — клип (T03 не подключён к T01). Снимки — во временной папке
(лица из согласованного ролика, в Git не кладутся).

## Исправлено в C2 по ходу (ветка `codex/class-C2`)
1. `13a7a23` — T01 отвечает на устаревший токен `error{code:"resume_rejected"}` (в протоколе только
   `join_rejected`); C2 бесконечно повторял старый токен. Теперь любой отказ по токену → сразу вход по коду; токен
   привязан к серверу+коду (новая сессия класса никогда не «возобновляет» старую).
2. `23345e7` — REPLAY выглядел как живая камера («Камера работает»): на превью — красная полоса «REPLAY · запись» /
   «СИНТЕТИКА · не камера», у объяснения эпизода — префикс `[REPLAY · запись]`. Очередь сообщений прошлой сессии
   класса (другой код) удаляется, а не досылается в новую.
3. `23345e7` — `python -m proctor.uplink.demo_teacher`: команды преподавателя из терминала через API T01 (у панели T02
   пока нет кнопок команд).

## Как записать видео (один ноутбук)
Порядок окон: **слева** — Chrome с панелью преподавателя, **справа** — окно ADAL студента, **снизу** — небольшой
терминал «Преподаватель» (команды). Запись экрана — Win+G (Xbox Game Bar) или OBS, весь экран.

### 0. Подготовка (один раз, до записи)
```powershell
cd C:\Qostanay_hub-codex-proctoring-prompts\A02-camera\proctoring
git switch codex/class-C2; git pull --ff-only          # или codex/proctor-integration после того, как A01 вольёт C2
uv sync --frozen --extra cv --extra dev
cd desktop; npm ci; node node_modules/electron/install.js; npm run build; cd ..
```
Ролик для REPLAY уже лежит в `%LOCALAPPDATA%\QorgauExam\replay\demo_class_red_loop.json` (копия `zone_c_red_01`
с `"loop": true`). PIN оператора ADAL (нужен, чтобы пропустить калибровку в REPLAY — человек в записи не смотрит на
точки): `$env:QORGAU_OPERATOR_PIN_HASH = (Write-Output 2468 | node desktop\main\tools\hash-pin.mjs)` → PIN `2468`.

### 1. Окно «Сервер класса» (PowerShell №1)
```powershell
cd C:\Qostanay_hub-codex-proctoring-prompts\A02-camera\proctoring
$env:PYTHONPATH = "."; .venv\Scripts\python -m classroom.server --port 8765 --data-dir "$env:LOCALAPPDATA\QorgauClassroom-demo"
```
В консоли: `PIN преподавателя: NNNNNN` и адрес панели. Окно свернуть.

### 2. Окно «Преподаватель» (PowerShell №2) — создать сессию, получить код
```powershell
cd C:\Qostanay_hub-codex-proctoring-prompts\A02-camera\proctoring
$env:PYTHONPATH = "backend;contracts/python"; $env:QORGAU_CLASS_TEACHER_PIN = "NNNNNN"
.venv\Scripts\python -m proctor.uplink.demo_teacher session "10А, физика"      # -> join code: 123456
```
Chrome: `http://127.0.0.1:8765/` → PIN → пустая панель «Qorgau · Класс».

### 3. Окно студента ADAL (PowerShell №3)
```powershell
cd C:\Qostanay_hub-codex-proctoring-prompts\A02-camera\proctoring\desktop
$env:QORGAU_CLASS_SERVER = "127.0.0.1:8765"; $env:QORGAU_CLASS_CODE = "123456"; $env:QORGAU_CLASS_LABEL = "Студент 1 (REPLAY-демо)"
$env:QORGAU_REPLAY_DIR = "$env:LOCALAPPDATA\QorgauExam\replay"; $env:QORGAU_MODELS_DIR = "$env:LOCALAPPDATA\QorgauExam\models"
$env:QORGAU_OPERATOR_PIN_HASH = "<значение из шага 0>"
npm start
```
(Если запускаете из терминала VS Code — сначала `Remove-Item Env:ELECTRON_RUN_AS_NODE`.)

### 4. Сцены на запись
1. **Подключение.** ADAL открылся → блок «Класс»: «подключено». В панели появилась карточка «Студент 1 (REPLAY-демо)»
   с именем компьютера, «на связи», превью с полосой «REPLAY · запись».
2. **Подготовка студента.** ADAL: «запись (replay)» → идентификатор `demo_class_red_loop` → согласие → «Создать сессию
   и проверить» → «К калибровке» → «Пропустить…» → PIN `2468` → «Пропустить…» → причина «REPLAY-демо» →
   «Пропустить калибровку» → кнопка «Преподаватель ✕» (вернуться к виду студента). Видно «Всё готово к началу».
   *Для LIVE-камеры вместо REPLAY: «камера (live)» и обычная полноэкранная калибровка (без PIN).*
3. **Преподаватель начинает экзамен.** Терминал «Преподаватель»: `… demo_teacher start` → у студента начался экзамен.
4. **Эпизод → красная карточка.** Через ~5 с (в ролике поднимают телефон) карточка становится «Проверить в первую
   очередь» с причинами «Телефон поднят — 00:04», справа очередь «Требуют внимания». Клик по карточке → список
   эпизодов, «есть клип».
5. **Клип.** `… demo_teacher clip` → сейчас отказ (сервер: модуль клипов T03 не подключён). **На видео эту сцену
   пропустить** или сказать честно: «клип записывается на компьютере студента и передаётся по запросу; модуль
   просмотра клипов на сервере подключается».
6. **Блокировка.** `… demo_teacher lock "Телефон в руках — подождите преподавателя"` → у студента полноэкранный экран
   «Преподаватель приостановил ваш экзамен» с причиной; в карточке «экран заблокирован», «Камера работает».
7. **Разблокировка.** `… demo_teacher unlock` → экран снят, экзамен продолжается.
8. (по желанию) `… demo_teacher finish` → экзамен завершён.
Говорить: зона — приоритет проверки, решение принимает преподаватель; REPLAY — запись, не живая камера.

## Расхождения — сторона друга (T01/T02/T03/T04), точный список
1. **T01 + T03/T04:** плагины `classreview.classroom_feature:create` и `proctor_classctl.classroom_feature:create`,
   указанные в `classroom/coordination/INTERFACES.md §3`, в интеграции отсутствуют → `POST /api/student/clips/{id}` и
   `GET /api/teacher/clips/{id}` отвечают `501 feature_not_installed`; нет истории/решений T03 и модуля команд T04.
2. **T02:** в панели нет кнопок команд (`start_exam`, `finish_exam`, `lock {reason_ru}`, `unlock`, `request_clip`) и
   просмотра клипа — сейчас только API (`demo_teacher`). Нужна кнопка «Заблокировать» с полем причины и «Клип».
3. **T01 ↔ протокол:** код ошибки `resume_rejected` (устаревший токен) не описан в `PROTOCOL_v1.md §2` (там только
   `join_rejected`). C2 теперь обрабатывает любой отказ по токену; просьба дописать код в протокол (аддитивно).
4. **Протокол/T02:** у `status` нет режима источника (live/replay/synthetic); у реального клиента REPLAY панель
   пишет «Камера работает». C2 временно ставит полосу на превью и префикс в объяснение; просьба — аддитивное поле
   `status.source_mode` (или `hello.source_mode`) и метка в карточке T02.
5. **T03 UI** (`classreview/ui/review-module.js`): нет подписей новых правил 1.1 (`foreign_object_visible`,
   `second_screen_visible`, `background_speech`, `headphones_visible`, `identity_mismatch`).
6. **T04:** путь команд `/api/teacher/control/exams/{exam_id}/commands` ≠ протокольный
   `/api/teacher/students/{id}/commands` (T01 реализует протокольный — работает); `STUDENT_CLIENT.md` всё ещё нет.
7. (Наблюдение, не ошибка) После `POST /api/teacher/session` прежние студенты остаются в списке «без связи»; для
   чистой записи использовать новый `--data-dir`.
Совпадает (проверено): `hello/welcome`, `status` (зона, причины, locked, mic_active=false), `incident` (+ повтор
`open` с `clip_available:true`), `preview`, `ping/pong`, `command/ack` (+`seq`, `code`, `result`), команды
`start_exam`, `lock`, `unlock`, отказ `audio_start` с `error_code:"not_supported"`.
