# ADAL-AUDIO-BRIDGE — shutdown checkpoint

Branch: codex/adal-audio-bridge. Baseline 308b8ac; imported sibling lock537ab55 (and rootfc8a3a8) via merge24f110c, resolving preload to retain both helper imports. No push by this agent; parent owns final GitHub save.

## Implemented
- C1 default factory classroom.server.audio_feature:create_classroom_feature wraps T05 AudioHub, authenticated /api/teacher/audio/ws plus /api/teacher/audio/assets/{teacher|shared}/{file}. Existing loopback/Host/Origin gate retained; cookie checked every0.5s while idle; logout/disconnect/session replacement stops audio.
- Explicit audio_protocol=qorgau.class.audio.v1 negotiation in student hello and every extension message; bounded whitelist relay binds session and current student connection. Old REST audio_start/audio_stop refuse audio_extension_required, preventing ACK-only active claims.
- C2 proctor/uplink/audio.py: ephemeral scoped renderer relay, active audio_session_id and owned command ACK checks; heartbeat readiness, truthful mic_live, terminal/source-session/disconnect cleanup. Sibling537ab55 supplies actual client.py/app.py hook integration.
- Desktop main/src/class-audio.ts: fixed token-private IPC, exact trusted main frame, audio-only permission; teacher listen request + 5-second post-banner capture lease. Camera, subframes and foreign WebContents denied. Own preload/renderer main mount done.
- Renderer banner paints before getUserMedia/ACK; talk-only never captures student mic; actual StudentAudioEndpoint transports audio. Generation cancellation prevents delayed mic acquisition after stop. TeacherAudio listening also requires successful playback.

## ROOT STILL MUST WIRE main.ts
import createClassAudio from ./class-audio; after client construction: const classAudio=createClassAudio(client,trustedSender,()=>mainWindow?.webContents??null,devOrigin);
stream onEnvelope: classAudio.observe(env); stream onClose: ()=>classAudio.reset(); supervisor onLost: classAudio.reset(); registerIpc(): classAudio.register(ipcMain);
hardenSession: replace ONLY request/check deny handlers with classAudio.installPermissions(ses); KEEP device/display denies. Exam external Session stays all deny. Exam owner confirmed min native view y112 so72px banner visible.
Merge classreview factory into ServerConfig features comma-list rather than replace either factory.

## Observed verification
- Desktop contracts/main/renderer typecheck PASS before final small race hardening.
- class-audio strict JS typecheck PASS before final small race hardening.
- Existing AudioHub tests16 PASS.
- New real C1 websocket tests2 PASS: ACK remains accepted, forged other-student signal rejected, teacher logout sends audio_stop, legacy command refused.
- New Electron permission unit test PASS: exact main frame +audio+active lease only; no camera/subframe/foreign URL/window.
- Real C1→real C2 Uplink→real Electron fixed IPC→actual WebRTC fake-tone fixture PASS (hidden Electron, headless Chrome, generated WAV, both outputs muted, no real mic/camera, no guards).
  receiver bytes2778/packets34/audioLevel0.0267; recorded playable received synthetic Opus10922bytes.
  talk-only received teacher RTP+playback with unchanged student capture count.
  injected NotAllowedError→mic_denied and NotFoundError→mic_not_found, no live tracks.
  exam finish and classroom disconnect ended all student tracks/peers; zero false listening states.
  Evidence local ignored desktop/out/audio-electron-1791462430152/results.json and received-synthetic-tone.webm.
  Test fixture uses production helpers+IPC/permission policy with synthetic backend snapshot, not production main/OS guards.

## Latest unverified small edits after full fake-tone pass
Student update/offer generation guards, teacher delayed-microphone generation cancellation + playback invariant, C1 reconnect old-session termination, visible Adal rename. Rerun typechecks + fixture after root integration. This shutdown checkpoint deliberately saves these without claiming a rerun.

## Reproduction
Python: shared Qorgau-run/proctoring/.venv/Scripts/python.exe; PYTHONPATH=proctoring;proctoring/backend;proctoring/contracts/python;proctoring/class-audio/server.
pytest --basetemp=proctoring/desktop/out/<fresh-name> proctoring/classroom/server/tests/test_audio_extension.py proctoring/class-audio/server/tests/test_hub.py
From desktop: node scripts/typecheck.mjs; node node_modules/typescript/bin/tsc -p ../class-audio/tsconfig.json; node main/tests/run.mjs class-audio.
PLAYWRIGHT_MODULE=C:/Users/LEGION/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright and QORGAU_PYTHON=<shared python>; node renderer/tests/audio-electron/run.mjs.
Esbuild/test subprocesses needed sandbox escalation here; all commands authorized safe fixture only.

## Remaining
Root main.ts hooks + merged full build; final race tests/re-run; panel integration by teacher wave; real two-computer LAN/firewall/mDNS/TURN and physical device quality unverified. No STUN/TURN configured (LAN host candidates only).
All owned test processes exited normally; fixture finally closed hidden Electron, headless Chrome, C2 and C1. No production app, real capture or OS restrictions launched.

## 2026-10-08 вечер — доводка аудиосвязи на одном ноутбуке (сессия A13, ветки `codex/class-audio` и `codex/class-audio-safe`)
База: `codex/proctor-integration` @ `2799163`.

Сделано:
1. `test_persistence_audio.py`: 2 падающих теста (KeyError `command_id`) гоняли аудио через старый REST `audio_start`. Ядро
   его намеренно отклоняет (422 `audio_extension_required`). Теперь те же гарантии проверяются через расширение T05
   (`/api/teacher/audio/ws`): сигналинг только для подтверждённой сессии; стоп преподавателя → `audio_stop` студенту и
   `ended(teacher_stop)`, после этого ничего не пересылается; уход студента → `ended(student_disconnected)`.
2. `audio_feature.py`: в allowlist добавлен `teacher/class-panel-module.js`, этот файл грузит панель T02. JS/CSS отдаются
   с явным типом. Баннера «Не удалось загрузить аудиосвязь» больше нет.
3. `desktop/main/src/main.ts`: подключён мост по разделу «ROOT STILL MUST WIRE» выше (`createClassAudio`: observe / reset /
   register / installPermissions; запреты device/display-media не тронуты). Экспорт там именованный, не default.
4. `class-audio/web/teacher/class-panel-module.js`: модуль панели подключался к `/ws/teacher`, это адрес dev-сервера T05.
   В сервере класса это поток ядра, и «Слушать» уходила в никуда. Теперь `/api/teacher/audio/ws`.
C2 (`proctor/uplink/audio.py`) не менялся: `ack ok:true` отправляет сам рендерер, только после того как плашка отрисована и
микрофон получен (`student-endpoint.js` `_start`). Если рендерер не готов, C2 отвечает `ok:false not_supported`.

Проверка на этом ноутбуке. Настоящий C1 (audio + T03 + T04), настоящий Adal (production main, Electron) с **фиктивным
аудиоустройством Chromium, на вход — сгенерированный тон 440 Гц** (реальный микрофон не использовался, звук не записывался).
Панель преподавателя — в headless Edge (Playwright).
* «Слушать» → у студента плашка аудио-модуля и оверлей **«Микрофон включён преподавателем для проверки»**, живой трек
  микрофона. У преподавателя входящий RTP (≈21–27 пакетов, audioLevel ≈0,03) через 2,3–3,8 с.
* «Завершить связь» → плашка и оверлей исчезают, трек микрофона остановлен (`readyState=ended`), в карточке
  `mic_active=false`, всё это за 0–0,5 с.
* **37 прогонов: 28 успешны, 9 нет.** Причины сбоев:
  - 3 — отказ до начала связи (`rejected`). Вероятная причина: окно Adal было закрыто другими окнами, Chromium на
    Windows считает такое окно `hidden`, а рендерер намеренно не берёт микрофон, если плашку не видно (`visibilityState`
    в тех прогонах не записывался). Когда окно выводилось вперёд, этот сбой больше не
    повторился (0 из 20);
  - 5 — связь завершилась со стороны студента сразу после принятия: окно Adal закрыли (приложение вышло, код 0) или в Adal
    нажали «Создать сессию» (в логе `mode=preflight session=…` через 2–4 с после «Слушать»). Мост намеренно завершает
    аудио при смене сессии прокторинга. Скрипт эти кнопки не нажимал; вероятно, всплывающее окно трогал человек. Это не
    доказано;
  - 1 — сигналинг прошёл, но RTP за 20 с не пришёл (до вывода окна вперёд; причина не установлена).
* Регрессии: `classroom` + uplink + хаб T05 — 207 passed; desktop typecheck PASS; main 90/91 (тот же
  «.py protocol-only helper» падает и на `main.ts` из 2799163); renderer unit 17/17.

Решение (правило «не вливать, если не надёжно»):
* **`codex/class-audio-safe`** — только пп. 1, 2, 4, без подключения микрофона в Electron. Это можно вливать для видео:
  панель грузит аудио-модуль без ошибки, а «Слушать» получает честный отказ C2. Проверено без GUI на настоящем сервере
  и backend: `requested → rejected(not_supported)`. Тесты 207 passed.
* **`codex/class-audio`** — то же + п. 3 (настоящий микрофон). Вливать после ручной проверки капитаном (1 минута):
  `Start-AdalClassDemo.ps1` → в панели карточка студента → «Слушать» → у студента плашка «Микрофон включён преподавателем
  для проверки», преподаватель слышит → «Завершить связь» → плашка исчезла, индикатор микрофона Windows погас.
  На одном ноутбуке нужны **наушники** (или выключенный звук вкладки панели), иначе будет свист обратной связи.
  Окно Adal держать на экране.

Осталось: надёжность на двух реальных ПК в LAN (ICE только host-кандидаты, без STUN/TURN; панель для «Слушать» не
берёт микрофон и отдаёт mDNS-кандидаты); понятное сообщение у преподавателя на отказ «окно студента скрыто».
