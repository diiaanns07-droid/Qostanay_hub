# Qorgau Class — протокол v1 (qorgau.class.v1)

Статус: **заморожен на 8 октября 2026, 14:45**. Изменения — только через капитана (A01), только аддитивно.
Цель MVP: 1 преподаватель + 2 студента в одной LAN или Wi-Fi. Сценарий: подключение → открытие экзамена → обнаружение телефона → смена статуса → просмотр клипа → аудиосвязь → блокировка с причиной → разблокировка.

## 1. Топология

```
[ПК преподавателя]  Qorgau Class Server (Python, FastAPI) — порт 8765
   ├─ /                     панель преподавателя (веб-страница, отдаёт сервер)  — только 127.0.0.1
   ├─ /api/teacher/*  /ws/teacher                                            — только 127.0.0.1
   └─ /ws/student  /api/student/*                                             — LAN (0.0.0.0:8765)
[ПК студента] существующее приложение Qorgau Exam (Electron + локальный backend)
   └─ модуль uplink (backend/proctor/uplink/) — WebSocket-клиент к серверу
```

- Анализ CV остаётся **на компьютере студента**. По сети идут события, статусы и маленькие превью. Видео и звук передаются только по запросу.
- Эндпоинты преподавателя принимают подключения **только с loopback**. Студенты не могут вызвать команды преподавателя.
- Windows при первом запуске сервера спросит разрешение брандмауэра. Разрешает человек; программно правила не меняем.

## 2. Подключение и аутентификация

1. Преподаватель создаёт сессию в панели. Сервер выдаёт **код подключения** — 6 цифр, действует до конца сессии.
2. На компьютере студента вводятся адрес сервера (`192.168.x.x:8765`) и код. Если задать адрес через `QORGAU_CLASS_SERVER` и код через `QORGAU_CLASS_CODE`, поля заполнятся сами.
3. Студент открывает `ws://<server>:8765/ws/student`. **Код и токены в URL не передаются.** Первым сообщением идёт `hello`.
4. Сервер отвечает `welcome` с `student_id` и `resume_token` (32 байта hex). При переподключении `hello` содержит `resume_token` вместо `join_code`, и сервер узнаёт того же студента.
5. Неверный код → `error{code:"join_rejected"}`, соединение закрывается. После 5 неудачных попыток с одного IP — пауза 30 с.
6. Доступ преподавателя: при запуске сервер печатает в консоль одноразовый **PIN преподавателя**. Панель спрашивает его один раз и получает cookie (`HttpOnly`, `SameSite=Strict`).

## 3. Формат сообщений (WebSocket, JSON, UTF-8)

Общая оболочка: `{"type": "...", "v": 1, "msg_id": "<uuid4>", "sent_at": "<ISO-8601 UTC>", ...поля}`
Максимальный размер сообщения — 256 КБ. Неизвестный `type` игнорируется и пишется в лог, соединение не рвётся.

### 3.1 Студент → сервер

| type | поля | когда |
|---|---|---|
| `hello` | `protocol:"qorgau.class.v1"`, `join_code` ИЛИ `resume_token`, `computer_name`, `student_label`, `app_version` | первым сообщением |
| `status` | `exam_state: "idle"\|"preflight"\|"calibrating"\|"running"\|"paused"\|"finished"`, `camera: "ok"\|"busy"\|"off"\|"unknown"`, `monitoring: "ok"\|"degraded"`, `zone: "green"\|"yellow"\|"red"\|"grey"\|null`, `zone_reasons_ru: [≤3]`, `incidents_total`, `incidents_by_priority: {low,medium,high}`, `locked: bool`, `mic_active: bool` | каждые 2 с и при любом изменении |
| `incident` | `seq` (растёт на клиенте с 1), `incident_id`, `rule_id`, `category`, `priority: "low"\|"medium"\|"high"`, `state: "open"\|"closed"`, `t_start_wall`, `duration_ms`, `explanation_ru`, `clip_available: bool`, `snapshot_jpeg_b64` (необязательно, ≤ 40 КБ) | при открытии и закрытии эпизода |
| `preview` | `jpeg_b64` (320×240, ≤ 30 КБ), `frame_wall` | каждые 2 с, пока идёт экзамен |
| `ack` | `command_id`, `ok: bool`, `error_ru?` | на каждую команду |
| `audio_signal` | `command_id`, `sdp?`, `ice?` | WebRTC-сигналинг (раздел 6) |
| `pong` | — | ответ на `ping` |

**Очередь при обрыве сети:** `incident` и `ack` складываются в локальную очередь (SQLite или JSONL, до 1000 записей). После переподключения отправляются по порядку `seq`. Сервер отбрасывает дубликаты по `(student_id, seq)`. `status` и `preview` в очередь не попадают: после переподключения сразу шлётся свежий `status`.

### 3.2 Сервер → студент

| type | поля |
|---|---|
| `welcome` | `student_id`, `resume_token`, `server_time`, `exam: {exam_id, title, mode: "url"\|"app", allowed_urls: [шаблоны], allowed_apps: [имена exe], instructions_ru}` |
| `command` | `command_id`, `kind`, `payload` (таблица ниже) |
| `ping` | раз в 5 с. Нет `pong` 15 с → студент считается «нет связи» |
| `error` | `code`, `message_ru` |

| `command.kind` | `payload` | что делает студент |
|---|---|---|
| `start_exam` | `{}` | открывает экзамен (`exam.mode`) и включает ограничения |
| `finish_exam` | `{}` | завершает сессию, снимает ограничения |
| `lock` | `{reason_ru}` (1–200 символов) | полноэкранный экран блокировки с причиной; камера и аудиосвязь продолжают работать |
| `unlock` | `{}` | снимает экран блокировки |
| `request_clip` | `{incident_id}` | загружает клип через HTTP (раздел 5) |
| `audio_start` | `{direction: "listen"\|"talk"\|"both"}` | включает микрофон и **показывает у студента заметный индикатор** |
| `audio_stop` | `{}` | выключает микрофон, индикатор пропадает |

Каждая команда — ровно один `ack`. Нет `ack` 10 с → в панели «команда не подтверждена».

## 4. Зоны (единая логика, серый считает сервер)

- Студент шлёт `zone` из A05 `assess_session_zone` (green / yellow / red / grey). Цвет в UI для `yellow` — **оранжевый**, значение в протоколе остаётся `yellow`.
- Сервер ставит **grey**, если нет `status` дольше 10 с или `camera` ≠ `ok`. Это перекрывает зону студента.
- Сортировка карточек: red → yellow → grey → green, внутри — по последнему событию.
- Один телефон, видимый на многих подряд кадрах, — один эпизод. Это уже делает A05.
- Слов «списывает», «нарушитель» и «вероятность» нет. Решение принимает преподаватель.

## 5. HTTP

- Преподаватель, только loopback:
  - `GET /api/teacher/students` → список карточек;
  - `GET /api/teacher/students/{id}/incidents`;
  - `POST /api/teacher/students/{id}/commands` `{kind, payload}` → `{command_id}`;
  - `POST /api/teacher/session` `{title, mode, allowed_urls, allowed_apps}` → `{join_code}`;
  - `GET /api/teacher/clips/{incident_id}`;
  - `POST /api/teacher/students/{id}/decision` `{incident_id, decision: "confirmed"|"dismissed"|"needs_followup", note_ru}`.
- `WS /ws/teacher` — поток обновлений карточек: `student_update`, `incident`, `preview`, `ack`.
- Студент: `POST /api/student/clips/{incident_id}`, заголовок `Authorization: Bearer <resume_token>`, тело `video/mp4` или `video/x-msvideo` ≤ 8 МБ. Клип: 5 с до и 5 с после начала эпизода.

## 6. Аудио (по возможности; базовый сценарий работает без него)

WebRTC прямо в LAN, без STUN и TURN (host-кандидаты). Сигналинг идёт через сервер: панель ↔ `/ws/teacher` ↔ сервер ↔ `/ws/student` в сообщениях `audio_signal`. Студенческая сторона — в Electron (renderer, `getUserMedia({audio:true})`). Пока идёт аудио, у студента видна плашка «Микрофон включён преподавателем для проверки».

## 7. Что НЕ входит в v1
Интернет и облако; запись звука на диск; управление чужими программами за пределами режима экзамена; изменение системных настроек Windows; 100 студентов (цель, не проверено).
