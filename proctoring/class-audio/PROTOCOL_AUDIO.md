# Qorgau Class — аудиосвязь, контракт `qorgau.class.audio.v1`

Статус: **ПРЕДЛОЖЕНИЕ T05**, аддитивное расширение `qorgau.class.v1` §6 (тот же WebSocket, те же оболочки сообщений).
Утверждает T01/A01 (протокол класса заморожен, менять его может только капитан). Ни одно существующее поле v1 не
меняет смысл: добавлены поля в `command.payload`, поля в `audio_signal`, новые типы `audio_*` (неизвестный `type` по
v1 игнорируется — старые клиенты не ломаются).

## 1. Решение: WebRTC, точка-точка в LAN

| Вариант | Почему нет / да |
|---|---|
| **WebRTC (выбран)** | Opus, задержка ~100–300 мс, джиттер-буфер, эхоподавление/шумодав из `getUserMedia`, обязательное шифрование DTLS-SRTP. Есть в браузере преподавателя и в Electron студента без зависимостей. Сервер звук не видит и не может записать. |
| PCM/Opus поверх WebSocket через сервер | свой кодек/буфер/AEC, звук идёт через сервер (лишняя точка записи), задержка выше |
| SFU/медиасервер | избыточно для одного разговора 1:1, новая зависимость |

ICE: только host-кандидаты (`iceServers: []`) — STUN/TURN не используются (нет интернета по условию; v1 §6).
Сигнализация — через сервер класса: панель ↔ `/ws/teacher` ↔ сервер ↔ `/ws/student`.

## 2. Участники и идентификаторы

* **Преподаватель (T)** — модуль «Аудиосвязь» панели класса (браузер на ПК преподавателя, origin сервера на loopback).
* **Сервер (S)** — сервер класса (C1) с хабом `AudioHub` из `class-audio/server`. Авторизует и пересылает, звук не трогает.
* **Студент (E)** — приложение студента (Electron renderer: `getUserMedia` + `RTCPeerConnection`); транспорт до сервера —
  `/ws/student` (в продукте через uplink C2). Эталонная реализация: `web/shared/student-endpoint.js`.
* `audio_session_id` — выдаёт только S: `as-` + 32 hex. Привязан к паре (учётная запись преподавателя, `student_id`).

## 3. Сообщения (оболочка v1: `{type, v:1, msg_id, sent_at, …}`)

### T → S (`/ws/teacher`)
| type | поля | ответ S |
|---|---|---|
| `audio_request` | `student_id`, `listen: bool`, `talk: bool` (хотя бы одно true) | `audio_state{state:"requested"}` или `audio_error` |
| `audio_update` | `audio_session_id`, `listen`, `talk` | пересылает студенту `command audio_update` |
| `audio_signal` | `audio_session_id`, `kind: "offer"\|"ice"`, `sdp?`, `ice?` | пересылает студенту |
| `audio_media` | `audio_session_id`, `connection`, `receiving: bool`, `sending: bool` | фиксирует «connected» |
| `audio_stop` | `audio_session_id`, `reason?` | `audio_state{state:"ended"}` + студенту `command audio_stop` |

### S → E (`/ws/student`)
| type | поля |
|---|---|
| `command` `kind:"audio_start"` | `payload: {audio_session_id, listen, talk, direction: "listen"\|"talk"\|"both"}` (`direction` — поле v1) |
| `command` `kind:"audio_update"` | `payload: {audio_session_id, listen, talk, direction}` — **новый kind** |
| `command` `kind:"audio_stop"` | `payload: {audio_session_id, reason}` |
| `audio_signal` | `audio_session_id`, `command_id` (команды `audio_start`), `kind: "offer"\|"ice"`, `sdp?`, `ice?` |

### E → S
| type | поля |
|---|---|
| `ack` (v1) | `command_id`, `ok`, `error_ru?`, **`error_code?`** — для `audio_start`: `ok:true` = индикатор показан и (если `listen`) микрофон получен |
| `audio_signal` (v1) | `audio_session_id`, `command_id`, `kind: "answer"\|"ice"`, `sdp?`, `ice?` |
| `audio_media` | `audio_session_id`, `mic_live`, `indicator_shown`, `teacher_audio_playing`, `connection` |
| `status` (v1) | `mic_active` = `mic_live` (поле v1) |

### S → T
| type | поля |
|---|---|
| `audio_state` | `audio_session_id`, `student_id`, `state`, `listen`, `talk`, `reason?`, `student_media?`, `since` |
| `audio_signal` | `audio_session_id`, `kind: "answer"\|"ice"`, `sdp?`, `ice?` |
| `audio_error` | `code`, `message_ru`, `audio_session_id?` |

`ice` = `{candidate, sdpMid, sdpMLineIndex}`; `null` — конец кандидатов. `sdp` ≤ 64 КБ.

## 4. Состояния сессии (ведёт S, их видит панель)

```
requested ──ack ok──► accepted ──audio_media(T: connected)──► connected
    │  └──ack !ok──► rejected (reason = error_code)                │
    └── 15 с без ack ─► ended(accept_timeout)    accepted ─ 20 с без connected ─► ended(connect_timeout)
любая ──► ended(reason)  reason ∈ teacher_stop, student_stop, teacher_disconnected, teacher_auth_lost,
                                 student_disconnected, network_lost, server_shutdown, error
```
`ended`/`rejected` — терминальные; новая связь = новый `audio_request` = новый `audio_session_id`.

## 5. Обязательные правила

1. **Одна активная сессия** у преподавателя и у студента. Второй `audio_request` → `audio_error{code:"teacher_busy"}`;
   сначала `audio_stop`. Переключение на другого студента — только явным завершением.
2. **Запрет чужого звука.** S пересылает `audio_signal`/`audio_media` только если `audio_session_id` — активная сессия
   отправителя и отправитель — её участник. Иначе сообщение отбрасывается, отправителю `audio_error{code:"session_mismatch"}`.
   Студент не может начать сессию и не может адресовать другого студента. Сессию можно начать только для `student_id`,
   который сейчас на связи.
3. **Завершение закрывает потоки.** При `ended` по любой причине: T закрывает `RTCPeerConnection` и останавливает свои
   треки; S отправляет E `command audio_stop`; E останавливает **все** треки микрофона (`track.stop()`), закрывает
   соединение и убирает индикаторы. Потеря авторизации преподавателя (выход, отзыв cookie) = `teacher_auth_lost`.
4. **E работает «fail closed».** Микрофон выключается сам при: `audio_stop`; потере связи с сервером; закрытии/сбое
   `RTCPeerConnection`; завершении экзамена; любой ошибке. Микрофон берётся только при `listen:true` и отпускается, когда
   `listen` становится `false` (только «говорить» — микрофон студента не трогается).
5. **Индикаторы у студента (обязательно, не скрываются):** пока трек микрофона живой — «Микрофон включён преподавателем»;
   пока идёт звук преподавателя — «Говорит преподаватель». `ack ok:true` на `audio_start` отправляется только после того,
   как индикатор показан; E сообщает `indicator_shown` в `audio_media`.
6. **Панель показывает «Слушаю»** только когда: `RTCPeerConnection` в `connected`, входящие RTP-пакеты растут
   (`getStats`) и E сообщил `mic_live:true`. «Говорю» — только когда исходящие пакеты растут.
7. **Без записи.** Ни S, ни T, ни E не записывают звук (нет `MediaRecorder`, нет медиа на сервере).
8. **Восстановление.** Кратковременный обрыв ICE при живой сигнализации — T делает ICE restart (новый `offer` с
   `iceRestart`) в той же сессии. Потеря сигнализации любой стороны — сессия `ended`; новая связь — только новым запросом
   преподавателя после переподключения студента (`resume_token` v1).

## 6. Коды

`audio_error.code`: `not_authorized`, `bad_request`, `student_unknown`, `student_offline`, `teacher_busy`, `student_busy`,
`session_mismatch`, `session_ended`.
`ack.error_code` студента: `mic_denied` (NotAllowedError), `mic_not_found` (NotFoundError), `mic_busy` (NotReadableError),
`insecure_context`, `not_supported`, `busy`, `exam_state`, `internal`.

## 7. Таймауты

ack на `audio_start` — 15 с; `connected` после accept — 20 с; ICE `disconnected` → restart через 3 с, `failed` → один
restart, затем 10 с → `ended(network_lost)`; ping/pong v1 (5 с / 15 с) определяет `student_disconnected`.
