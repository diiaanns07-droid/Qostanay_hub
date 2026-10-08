# Qorgau Class — панель преподавателя (T02)

Самостоятельно запускаемая панель наблюдения за классом (2–100 студентов) по протоколу
`qorgau.class.v1` (`proctoring/contracts/class/PROTOCOL_v1.md`, baseline `65c8c17`).
Без зависимостей и сборки: ES-модули, HTML, CSS. Нужен только Node.js 18+ для локального сервера.

## Запуск

```bash
node proctoring/class-panel/serve.mjs            # http://127.0.0.1:8790/  — DEMO (config.json)
```

| Адрес | Что показывает |
|---|---|
| `http://127.0.0.1:8790/` | DEMO, 30 студентов (из `config.json`) |
| `…/?students=2` · `?students=100` | DEMO на 2 / 100 студентах |
| `…/?rate=busy` | DEMO с частыми событиями |
| `…/?adapter=real` | реальный адаптер: только `/api/teacher/*` и `/ws/teacher` того же origin |

Реальный режим вне C1: `node serve.mjs --upstream http://127.0.0.1:8765` и `?adapter=real` — сервер панели
проксирует ТОЛЬКО контрактные пути к серверу класса (cookie преподавателя работает, origin один).

## Режимы данных (не смешиваются)

* **DEMO** — `src/adapters/demo.js`: имитация студентов и «сервера» в полях протокола. Полоса «DEMO-режим»,
  метка в шапке, водяной знак DEMO на превью, «DEMO» в карточке. Управление имитацией — кнопка «DEMO · управление».
* **REAL** — `src/adapters/real.js`: `GET /api/teacher/students`, `GET /api/teacher/students/{id}/incidents`,
  `WS /ws/teacher`. Никаких придуманных ответов: непонятный формат → сообщение об ошибке, отсутствующее поле →
  «нет данных».

Адаптер выбирается один раз при загрузке (`config.json` → `adapter`, или `?adapter=`). Переключение — только
перезагрузкой страницы.

## Структура

```
class-panel/
  index.html  styles.css  config.json  serve.mjs
  src/app.js            сборка панели, точка расширения window.QorgauClassPanel
  src/model.js          нормализация полей протокола, зоны, тексты (без своей формулы риска)
  src/store.js          состояние, пакетные обновления, устойчивый порядок, очередь внимания
  src/extensions.js     реестр модулей карточки студента (history / commands / audio)
  src/adapters/         real.js, demo.js
  src/ui/               header, grid, card, queue, drawer, demoPanel, icons, dom
  tests/                unit (node:test) и e2e (Playwright)
```

## Точка расширения

```js
window.QorgauClassPanel.registerStudentModule({
  id: "c1-commands", slot: "commands", title: "Команды",
  mount(el, ctx) { /* ctx.studentId, ctx.getStudent(), ctx.subscribe(fn), ctx.getIncidents() */ return () => {}; },
});
```
Модули, объявленные до загрузки панели, можно положить в `window.QorgauClassPanelModules = [...]`.
