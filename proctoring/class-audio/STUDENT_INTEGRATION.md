# Аудиосвязь: что сделать в приложении студента

Для разработчика студенческого приложения (Electron + локальный backend). Контракт — `PROTOCOL_AUDIO.md`.
Эталон студенческой стороны: `web/shared/student-endpoint.js` (`StudentAudioEndpoint`), его же использует тестовый
peer. **Интеграция с приложением студента T05 не проверялась** — этот документ описывает, что нужно сделать и как проверить.

## 1. Куда встраивать

Звук захватывается и проигрывается в **renderer** (там есть `getUserMedia` и `RTCPeerConnection`); связь с сервером
класса — у uplink (C2, `backend/proctor/uplink/`). Рекомендуемый путь (renderer не получает токены и адрес сервера):

```
сервер класса ──/ws/student──► uplink C2 (Python) ──поток backend──► Electron main ──IPC──► renderer: StudentAudioEndpoint
renderer.send(msg) ──IPC (фиксированный метод)──► main ──► backend ──► uplink ──/ws/student──► сервер класса
```

Нужны (согласовать с T01/A01; T05 общие файлы не менял):
1. **C2 uplink:** пересылать в локальный backend `command` с `kind` ∈ {`audio_start`, `audio_update`, `audio_stop`} и
   `audio_signal`; отправлять на сервер `ack`, `audio_signal`, `audio_media` от renderer; при потере связи с сервером —
   сообщить renderer (он выключит микрофон). `status.mic_active` брать из renderer.
2. **A01 (контракт backend):** тип сообщения потока, например `class_audio {payload}` (сервер → renderer), и маршрут
   `POST /v1/class/audio` (renderer → сервер, только `ack|audio_signal|audio_media`, ≤ 256 КБ).
3. **A06 (оболочка):** метод моста `classAudioSend(msg)` с проверкой типа и размера; доставка `class_audio` в renderer.
   **Разрешение микрофона:** сейчас оболочка отклоняет все разрешения. Нужно разрешить ровно аудио и ровно окну экзамена:
   ```js
   session.setPermissionRequestHandler((wc, permission, cb, details) =>
     cb(permission === "media" && isMainWindow(wc) && isTrustedOrigin(details.requestingUrl) &&
        (details.mediaTypes ?? []).length > 0 && details.mediaTypes.every((t) => t === "audio")));
   session.setPermissionCheckHandler((wc, permission, origin) => permission === "media" && isMainWindow(wc) && isTrustedOrigin(origin));
   ```
   Видео и экран — по-прежнему запрещены. `qorgau://app` уже зарегистрирован как secure → `isSecureContext === true`.
4. **A07 (интерфейс):** индикаторы (раздел 3) поверх любого экрана, включая экран блокировки и режим экзамена.

## 2. Подключение эталона

```js
import { StudentAudioEndpoint } from "./student-endpoint.js"; // скопировать файл или подключить как модуль
const endpoint = new StudentAudioEndpoint({
  send: (msg) => window.qorgau.classAudioSend(msg),          // метод моста (п. 1.3)
  audioElement: document.getElementById("teacher-audio"),   // <audio autoplay>, без controls
  onIndicators: (ind) => showIndicators(ind),                // ДОЛЖЕН нарисовать и вернуть Promise после отрисовки
});
bridge.onClassAudio((msg) => endpoint.handleMessage(msg));  // команды audio_* и audio_signal
bridge.onClassLinkLost(() => endpoint.transportLost());     // нет связи с сервером → микрофон выключается
onExamFinished(() => endpoint.stop("exam_finished"));
statusTimer(() => sendStatus({ mic_active: endpoint.micActive }));
```

## 3. Обязательные индикаторы

| Условие | Показать (не скрывается, поверх всего) |
|---|---|
| `ind.micLive` | «● Микрофон включён преподавателем» — заметная плашка |
| `ind.teacherSpeaking` | «🔊 Говорит преподаватель» |
| `ind.sessionActive` и микрофон выключен | «Аудиосвязь с преподавателем» |

`ack ok:true` эталон отправляет только после того, как `onIndicators` завершился (индикатор уже на экране).

## 4. Поведение, которое нельзя менять

* Микрофон берётся **только** при `listen:true` и останавливается (`track.stop()`), когда `listen` выключили.
* «Fail closed»: `audio_stop`, потеря связи с сервером, сбой соединения дольше 10 с, конец экзамена, любая ошибка →
  все треки остановлены, `RTCPeerConnection` закрыт, индикаторы сняты.
* Сообщения чужой сессии игнорируются (эталон проверяет `audio_session_id`).
* Никакой записи: не использовать `MediaRecorder`, не сохранять звук.
* Ошибки микрофона → `ack ok:false` с `error_code`: `mic_denied`, `mic_not_found`, `mic_busy`, `insecure_context`,
  `not_supported` (эталон уже это делает через `media-errors.js`).

## 5. Как проверить у себя (без приложения преподавателя)

1. Запустить DEV-сервер (`README.md`), открыть пульт `http://127.0.0.1:8765/`, войти по PIN из консоли.
2. Подключить приложение студента к серверу с кодом подключения (или открыть тестовый peer для сравнения).
3. «Слушать» → у студента «Микрофон включён преподавателем», у преподавателя «Слушаю студента.» и уровень звука.
4. «Говорить» → у студента «Говорит преподаватель».
5. «Завершить связь» → индикаторы пропали, значок использования микрофона в области уведомлений Windows исчез.
6. Запретить микрофон в Windows (Параметры → Конфиденциальность → Микрофон) → у преподавателя «у студента запрещён доступ».
7. Выдернуть сетевой кабель студента / выключить Wi-Fi → микрофон у студента выключается сразу; у преподавателя
   «студент потерял связь»; после возврата сети — «Подключиться снова».
8. Выйти из пульта преподавателя → у студента микрофон выключается.
