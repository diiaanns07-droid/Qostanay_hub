# classreview — история эпизодов, клипы и решения преподавателя (T03, qorgau.class.v1)

Серверный модуль для сервера класса (C1) + UI-модуль панели преподавателя (T02).

| Что | Где |
|---|---|
| Хранилище эпизодов/клипов/решений (SQLite + файлы вне исходников) | `store.py`, `db.py`, `media.py` |
| HTTP-маршруты протокола §5 (клипы с перемоткой, эпизоды, решения) | `router.py` → `create_review_router(...)` |
| Зона после проверки через общий A05 `assess_session_zone` | `zones.py` |
| UI-модуль (слот `history` панели T02) и DEV-страница | `ui/` |
| DEV-стенд (НЕ сервер C1) | `devserver.py` |
| Пример загрузки клипа студентом + синтетический студент | `examples/`, `CLIENT_CLIP_UPLOAD.md` |
| Синтетические тестовые клипы с надписью «TEST CLIP» | `testclips.py` |

Данные: `QORGAU_CLASS_DATA_DIR` (по умолчанию `%LOCALAPPDATA%\QorgauClass` / `~/.local/share/qorgau-class`).

```bash
cd proctoring
python -m pytest -q classreview/tests                       # 73 теста, e2e в Chromium если есть Node+Playwright
python -m classreview.devserver --pin 123456 --data-dir /tmp/qc   # DEV-стенд: http://127.0.0.1:8765/
python -m classreview.examples.fake_student --code <код>          # синтетический студент
```

Правила: эпизод = одна запись на `(student_id, incident_id)` (кадры — не эпизоды); повторная доставка
`(student_id, seq)` и повторная загрузка тех же байтов не создают дубликатов; клип принимается только по
запросу преподавателя, только от владельца эпизода, ≤ 8 МБ, формат проверяется по содержимому; доказательство
не заменяется; решения преподавателя — журнал только на добавление; отклонённые эпизоды не входят в зону
«с учётом решений», зона студента показывается рядом без изменений.
