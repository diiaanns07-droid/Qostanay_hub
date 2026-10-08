# Qorgau Class: условия экзамена и команды преподавателя (T04, интерфейс)

Это панель преподавателя для задач T04. В ней можно:
* создать и изменить экзамен в режиме «Внешний сайт» или «Отдельная программа»;
* вести списки разрешённых адресов и программ, домены входа с объяснением и подсказками, политики с версиями;
* назначать политику выбранным студентам;
* отправлять команды «Начать экзамен и наблюдение», «Заблокировать» (с причиной), «Разблокировать», «Завершить экзамен» и видеть их фактический результат;
* смотреть журнал и управлять SIMULATOR-студентами (только в DEV-сервере).

Сборки и npm-зависимостей нет: только HTML, CSS и ES-модули. Работает только через API T04 `/api/teacher/control/*`, бэкенд находится в `proctoring/backend/proctor_classctl/`.
Блокировки Windows и оболочка студента сюда не входят: это сторона клиента студента.

## Запуск (DEV, студенты-симуляторы)

```bash
cd proctoring
.venv/bin/python -m proctor_classctl.devserver --port 8791     # только 127.0.0.1
# открыть http://127.0.0.1:8791/        (для тестов: ?poll_ms=250&teacher=t-observer)
```

* Над страницей идёт фиолетовая полоса «СИМУЛЯТОР — … это не реальные компьютеры». В строках студентов есть метка «симулятор», есть вкладка «Симулятор: поведение клиента». Там можно выбрать поведение (успех, задержка 12 с, ошибка, нет поддержки блокировки, обрыв до или после выполнения, нет ответа, клиент v1, нет связи) и включить или выключить сеть.
* «Преподаватель (DEV)» задаёт заголовок `X-Qorgau-Dev-Teacher`: `t-aigerim` (владелец), `t-observer` (только просмотр), `t-bolat` (чужой экзамен). Выбор появляется, только если отвечает `/sim/info`. На сервере класса вход идёт по PIN-cookie, а роль проверяет сервер (см. `API_NOTES.md`, A1).

## Правила отображения (что обещает интерфейс)

| Правило | Где |
|---|---|
| `202` на POST значит только «сервер принял». Результат пишется как «команда принята сервером…», без слов «выполнено» или «заблокирован». | `model.commandResultLines`, `ui/common.js` |
| Значок команды берётся из `command.group`: pending «ожидает доставки», executing «выполняется», done «выполнено», error «ошибка», no_connection «нет связи», cancelled «отменена». У каждой группы своя форма значка. Рядом выводится `label_ru` сервера. Неизвестная группа никогда не показывается как «выполнено». | `model.commandStatus`, `dom.icon` |
| Колонка «Экран студента» выводит `lock.label_ru` дословно. «Заблокирован (по статусу клиента)» появляется только после подтверждения клиента (проверено e2e). | `model.lockStatus` |
| Один щелчок даёт один `idempotency_key` (UUID). После сетевой ошибки кнопка «Повторить тот же запрос» отправляет то же тело с тем же ключом, сервер отвечает «повтор». | `model.OneShotRequest`, `ui/common.sendOneShot` |
| Кнопка недоступного действия (`actions[kind].available=false`) отключена и зачёркнута, под ней указана причина `reason_ru`. Перед массовой командой окно показывает, кому команда уйдёт, а кому недоступна и почему. Окончательно решает сервер (`unavailable_ru` в ответе). | `ui/studentsTab.js` |
| Блокировка нашим клиентом и пауза таймера сайта разделены. В окне блокировки крупно выводится `site_timer_note_ru`. Кнопка «Пауза таймера сайта» всегда отключена с объяснением «интеграции с сайтом нет». | `ui/studentsTab.js` |
| Срок действия: перед отправкой показывается `ttl_default_s`, и сказано, что просроченная команда не выполнится после переподключения. | `model.expiryText` |
| Права: наблюдатель видит все кнопки отключёнными, формы доступны только для просмотра. Чужой преподаватель получает «Нет доступа к этому экзамену». Сервер всё равно проверяет каждую операцию сам. | `model.effectiveRole/roleDenial` |
| Всё, что приходит с сервера (подписи студентов, причины, названия), вставляется только как текст. `innerHTML` нигде не используется (статический тест). CSP без inline-скриптов. | `dom.js`, `tests/escaping.test.mjs` |
| Нет связи с сервером: полоса «Нет связи с сервером класса» с кнопкой «Повторить сейчас», данные помечены как устаревшие, все команды отключены. | `app.js` |

## Структура

```
class-control-ui/
  index.html  styles.css            страница (DEV-сервер отдаёт её на "/", папку — на "/ui/")
  src/model.js                      чистая логика: статусы, формы → payload, роли, ошибки, ключи повтора, журнал
  src/api.js  src/dom.js            HTTP-клиент; DOM-помощники и значки (без innerHTML)
  src/app.js                        состояние, опрос (1 с), шапка, вкладки, полоса «нет связи»
  src/ui/studentsTab.js             таблица, массовые команды, окно подтверждения, карточка студента с историей и отменой
  src/ui/examTab.js  policyForm.js  создание и изменение экзамена, поля политики, списки, домены входа
  src/ui/policiesTab.js             политики, версии, назначение выбранным
  src/ui/journalTab.js              журнал: когда / кто / кому / что / результат, фильтр по студенту
  src/ui/simulatorTab.js            «Симулятор: поведение клиента» (только DEV)
  t02-module.js  t02-module.css     модуль для панели T02 (слот "commands")
  tests/                            unit (node:test), fake-dom, e2e (Playwright), страница-обвязка модуля T02
  API_NOTES.md                      чего не хватает в API
```

### Модуль для панели T02

```html
<script type="module" src="/ui/t02-module.js"></script>
```
Модуль регистрируется через `window.QorgauClassPanel.registerStudentModule`, а если панель ещё не загрузилась, ставит себя в очередь `window.QorgauClassPanelModules`. Слот: `"commands"`, id: `t04-commands`.
Для `ctx.studentId` модуль находит экзамен, показывает `lock.label_ru`, последнюю команду со статусом и 4 кнопки с причинами недоступности. Блокировка отправляется с причиной и предупреждением о таймере сайта. В DEMO-режиме (`ctx.mode !== "real"`) модуль выводит «Недоступно в DEMO-режиме» и ничего не запрашивает. Внутри панели T02 модуль здесь не проверен: этой панели нет в ветке. Проверки: unit-тест с поддельным ctx и e2e-шаг через `tests/t02-harness.html` (поддельный ctx, настоящий API).

## Проверки (запущены 8 октября 2026)

### Unit

```bash
node --test /home/user/Qostanay_hub-T04/proctoring/class-control-ui/tests/*.test.mjs
```
```
ok 1 - DEV teacher header is sent only when a DEV teacher is chosen
…
ok 24 - idempotency: one click = one key, reused for a retry after a network error
…
ok 35 - register(): uses the panel's registerStudentModule, or queues for a panel that loads later
# tests 35
# suites 0
# pass 35
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 211.890941
```
Файлы: `model.test.mjs` (статусы, формы → payload, роли, ошибки, ключи повтора, результаты, журнал, сроки), `api.test.mjs`, `escaping.test.mjs` (текстовые узлы, статический запрет `innerHTML`/`eval`, CSP), `t02-module.test.mjs` (поддельный ctx, поддельный fetch, поддельные таймеры).

### E2E в настоящем Chromium (Playwright 1.56.1, глобальная установка, без `playwright install`)

```bash
cd /home/user/Qostanay_hub-T04/proctoring/class-control-ui
NODE_PATH=$(npm root -g) node tests/e2e.cjs
```
Скрипт сам запускает `proctoring/.venv/bin/python -m proctor_classctl.devserver --port <свободный>` (cwd `proctoring/`), ждёт `/sim/info`, по окончании останавливает сервер. Скриншоты сохраняются в `$T04_SHOTS`, по умолчанию в каталог scratchpad, не в репозиторий.
```
dev server pid 6266 on http://127.0.0.1:35497, demo exam exam-62cb2654d96e
ok   SIMULATOR banner and simulated rows are labelled (171 ms)
ok   create exam in url mode: field errors, sign-in explanation, suggestion chip adds (never auto-added) (687 ms)
ok   extra policy (app mode) and policy edit -> new version (469 ms)
ok   stale edit (409) is shown as 'изменено другим действием — обновите' and nothing is overwritten (343 ms)
ok   unsupported action: lock button disabled AND the reason is shown (14 ms)
     sim-01 badge sequence: ["none","pending","executing","done"]
ok   lock 'success' student: dialog lists unavailable students, timer note, expiry; badge pending -> executing -> done; 'Заблокирован' only after done (1817 ms)
ok   lock 'delay' student shows 'выполняется' while the client works (381 ms)
ok   lock 'error' student -> ошибка with the client's error (1113 ms)
ok   lock offline student -> нет связи (queued until reconnect, not 'locked') (313 ms)
ok   timeout: client that never answers -> 'нет ответа клиента за 10 с'; drop after execution -> late confirmation after reconnect (10758 ms)
ok   reconnect: queued lock of the offline student is delivered and confirmed after it comes online (913 ms)
ok   expired lock is NOT executed after a long disconnect (12206 ms)
ok   cancel a queued command before delivery (401 ms)
ok   unlock works (confirmed by the client) (908 ms)
ok   retry after a lost response reuses the idempotency key -> server answers 'repeat', no second command (353 ms)
ok   server unreachable -> banner, commands disabled; retry restores (448 ms)
ok   T02 slot module in a real browser (fake ctx, real API): status, actions, DEMO mode (1278 ms)
ok   journal: who, to whom, when, what (+reason), result; filter by student (223 ms)
ok   observer: no enabled command buttons, edit controls disabled, server refuses edits and commands (614 ms)
ok   other teacher (t-bolat): 'нет доступа' (208 ms)
ok   delayed client eventually confirms (выполнено after ~12 s) (2 ms)
ok   no JavaScript errors in the page (0 ms)
dev server stopped (exit SIGTERM)
22 passed, 0 failed; screenshots: /tmp/claude-0/…/scratchpad/t04/ui-shots
```
Что проверяет e2e сверх перечисленного: последовательность значка записывается MutationObserver. Надпись «Заблокирован (по статусу клиента)» ни разу не появилась раньше «выполнено», пока команда в пути, видно «Блокировка: …». Название экзамена с `<img onerror>` выведено как текст. Просроченная блокировка (`ttl_s=10`, студент без связи) после переподключения осталась «ошибка: срок действия истёк», у клиента-симулятора `locked=false`. При повторе после потерянного ответа ключ совпал, а у студента одна команда `start_exam`. Наблюдатель: 0 включённых кнопок команд, `PATCH` и `POST /commands` отвечают 403.

Серверные тесты T04 этот интерфейс не затрагивает: `.venv/bin/python -m pytest -q backend/proctor_classctl` → `73 passed`.
