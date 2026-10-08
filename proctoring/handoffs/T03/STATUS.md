# T03 — STATUS (история эпизодов, клипы, решения преподавателя)

Ветка: `codex/proctor-T03` от `65c8c17` (протокол `qorgau.class.v1` + OWNERSHIP C1/C2/A12; та же база, что у T02).
T01 baseline/OWNERSHIP для ролей T* ещё не опубликован (T02 отмечает то же) → пути T03 заняты как
`proctoring/classreview/` и `proctoring/handoffs/T03/`; регистрация запрошена в DEPENDENCIES.txt.

## Checkpoint 1 (WIP)
Серверная часть: хранилище (SQLite + файлы вне исходников), приём эпизодов с дедупликацией, приём клипов
с проверкой формата/размера, выдача клипов с перемоткой (HTTP Range), журнал решений, зона после проверки
через общий A05 `assess_session_zone`. Тесты: `python -m pytest -q classreview/tests` (из `proctoring/`).
Дальше: DEV-стенд, UI-модуль панели, пример клиента, e2e.
