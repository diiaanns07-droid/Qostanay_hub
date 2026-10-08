# Qorgau Class — аудиосвязь преподаватель ↔ студент (T05)

Преподаватель выбирает **одного** студента, включает «Слушать» и при необходимости «Говорить». Звук идёт напрямую по
WebRTC (Opus, DTLS-SRTP) между браузером преподавателя и приложением студента; сервер только авторизует и передаёт
сигнализацию, звук не видит и не записывает. Контракт: [`PROTOCOL_AUDIO.md`](PROTOCOL_AUDIO.md) (расширение
`qorgau.class.v1` §6, требует утверждения T01/A01). Студенту: [`STUDENT_INTEGRATION.md`](STUDENT_INTEGRATION.md).
Сеть: [`LAN.md`](LAN.md).

## Состав

| Путь | Что это |
|---|---|
| `server/qorgau_class_audio/hub.py` | `AudioHub` — авторизация, одна сессия, пересылка только между участниками, таймауты, завершение |
| `server/qorgau_class_audio/devserver.py` | **DEV-сервер** сигнализации для проверки (не сервер класса C1) |
| `web/teacher/` | модуль пульта: `teacher-audio.js` (контроллер), `audio-panel.js` (UI), `class-panel-module.js` (слот «Аудиосвязь» панели T02), отдельная страница |
| `web/shared/student-endpoint.js` | эталон студенческой стороны |
| `web/test-peer/` | **ТЕСТОВЫЙ PEER** — не приложение студента |
| `server/tests/`, `tests/e2e/` | pytest и браузерные проверки |

Новых зависимостей нет: Python — `fastapi`, `uvicorn`, `websockets`, `pydantic`, `httpx`, `pytest` из
`proctoring/requirements/full.txt`; браузер — стандартный WebRTC.

## Запуск (DEV)

```bash
cd proctoring/class-audio/server
PYTHONPATH=. ../../.venv/bin/python -m qorgau_class_audio.devserver --host 127.0.0.1 --port 8765
# консоль: PIN преподавателя и код подключения
# пульт:          http://127.0.0.1:8765/            (только с этого компьютера)
# тестовый peer:  http://127.0.0.1:8765/test-peer/  (с другого ПК — только по https, см. LAN.md)
# панель T02:     --class-panel-dir <путь к proctoring/class-panel> → http://127.0.0.1:8765/class-panel/
# LAN + TLS:      --host 0.0.0.0 --tls-cert cert.pem --tls-key key.pem
```

## Проверки

```bash
cd proctoring/class-audio/server && ../../.venv/bin/python -m pytest -q tests          # хаб + сервер
node <typescript>/bin/tsc -p proctoring/class-audio/tsconfig.json                    # checkJs strict
QORGAU_PYTHON=proctoring/.venv/bin/python node proctoring/class-audio/tests/e2e/audio.e2e.mjs out/
#   нужен Playwright (не зависимость проекта); QORGAU_CLASS_PANEL_DIR=… включает проверку в панели T02
```

## Встраивание в сервер класса (C1)

```python
from qorgau_class_audio import AudioHub
hub = AudioHub()
# /ws/teacher после проверки loopback + cookie + Origin:
hub.teacher_online(teacher_id, link);  await hub.on_teacher_message(link, msg)   # type audio_*
await hub.teacher_offline(link)        # закрытие сокета;   await hub.teacher_auth_lost(teacher_id)  # выход/отзыв
# /ws/student после welcome:
hub.student_online(student_id, link)
if msg["type"] == "ack" and hub.owns_command(msg.get("command_id")): await hub.on_student_ack(student_id, msg)
if msg["type"] in ("audio_signal", "audio_media"): await hub.on_student_message(student_id, msg)
await hub.student_offline(student_id, link)
# остановка сервера: await hub.shutdown()
```
`link` — любой объект с `async send(dict)`; отправка в закрытый сокет должна бросать исключение.
