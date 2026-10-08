# A08 — проверка на Windows 11, 8 октября 2026

## Обновление: настоящий A05, сборка 78798b7

`zones-isolated.txt/.xml`: 57 PASS, 2 SKIP; `zones-integration.txt/.xml`: 59 PASS, 1 SKIP.
Команды: `python -m pytest backend/proctor/evidence -q -p no:cacheprovider`,
на integration дополнительно `backend/tests/test_route_order.py`. Полные SHA — в STATUS.md.

Реальные ролики проверены в отдельном checkout integration с наложением только A08:

```powershell
python -m proctor.evidence.tests.replay_zones_check --out <новая-локальная-папка>
```

Проверка использует публичный create_app и авторизованные API-маршруты, настоящий REPLAY/CV без подмены
наблюдений. Читает модели и ролики из `%LOCALAPPDATA%/QorgauExam/`. Оригинальные manifests не меняет.
HTML/JSON и БД остаются в локальной папке; `retain_media=false`. В Git сохранены только факты проверки:
`replay-zones-results.json` (3 PASS, SHA входных файлов, зоны, причины, счётчики) и
`replay-zones-render.json` (offline Chrome, mobile 390 px, A4, первый экран desktop).
Регрессию старого 404 считать закрытой на указанной сборке. Нижние разделы — предыдущий checkpoint.

Первый checkpoint: `c201443f215b58b14f9d79104d4dcc86f3a4b9fd`.
Финальные логи относятся к следующему commit A08, который включает этот файл.
Python 3.12.14; окружения созданы `uv sync --frozen --extra cv --extra dev`.
Продуктовый lockfile не менялся.

## Автоматические проверки

Из `proctoring/`:

```powershell
python -m pytest backend/proctor/evidence -q -p no:cacheprovider
```

- `windows-final.txt/.xml`: ветка A08 — 55 PASS, 1 SKIP, 0 FAIL/XFAIL/XPASS.
- `candidate-final.txt/.xml`: отдельный checkout A01
  `de7290509bf558d6488be84d2e0730b2b9ab104a`, поверх скопирован только каталог
  `backend/proctor/evidence/` A08 — 55 PASS, 1 SKIP, 0 FAIL/XFAIL/XPASS.
- Единственный SKIP: symlink, WinError 1314. Права и настройки Windows не менялись.
- `test_http_keepalive.py` запускает настоящий backend на свободном loopback-порту,
  передаёт случайный токен через stdin и проверяет 15 ответов 422/INVALID_ARGUMENT
  с неизменным TCP-соединением и успешным `/health` после каждого запроса.
- Windows: блокировка второго хранилища и освобождение msvcrt-lock, запись через
  os.replace, повторное открытие и удаление снимка в пути `Сессии Әлия` — PASS.

## Реальный рендерер

На ноутбуке использован установленный Chrome; Playwright 1.56.0 установлен во
временный каталог инструментов (`npm install --prefix <tools> --no-save
--ignore-scripts --no-audit --no-fund playwright@1.56.0`). Для теста задаются:

```powershell
$env:NODE_PATH = '<tools>\node_modules'
$env:QORGAU_CHROMIUM = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
python -m pytest backend/proctor/evidence/tests/test_evidence_report.py -q -p no:cacheprovider
```

Без этих инструментов только необязательный renderer-тест пропускается.
С ними он выполнен в обоих финальных прогонах. Проверены offline HTML, отсутствие
активного содержимого и внешних запросов, экранирование недоверенного текста,
загрузка встроенного JPEG, отсутствие горизонтального скролла на ширине 390 px,
печать многостраничного A4 PDF. Снимки SYNTHETIC-отчёта визуально просмотрены.
Плашка зоны и решения преподавателя видны в первом экране 1366×900.

Обновление примеров:

```powershell
python -m proctor.evidence.sample --out backend/proctor/evidence/samples
```

Это SYNTHETIC: искусственное изображение, без записей камеры и персональных данных.
JSON содержит временные поля summary, `review_zone=null`, объяснение на русском и
`review_zone_rule_version=null`. Порогов A05 в A08 нет.

## Интеграционная проба кандидата

Публичный `create_app`, временная БД, SYNTHETIC-сессия, abort → DELETE:

| Запрос после удаления | HTTP |
|---|---:|
| GET /v1/sessions/{sid} | 404 |
| GET /v1/sessions/{sid}/metrics | 404 |
| GET /v1/sessions/{sid}/summary | 404 |
| GET /v1/sessions/{sid}/incidents | 404 |
| GET /v1/sessions/{sid}/answers | 404 |
| GET /v1/sessions/{sid}/report.html | 404 |
| GET /v1/sessions/{sid}/report.json | 404 |

`GET /v1/sessions/overview` в кандидате: **404 SESSION_NOT_FOUND**, сообщение
`session overview not found`. Общий маршрут A01 принимает `overview` за ID.
Это открытая зависимость A01: зарегистрировать статический маршрут раньше общего.
Обработчик A08 отдельно: 200, проверены поля, счётчики, порядок зон и дат.

НЕ проверено: фактическое правило A05 и контракты 1.1 (SHA ещё не переданы),
LIVE CV, нативные ограничения Windows, symlink при выданной привилегии,
импорт с других ноутбуков, понимание отчёта преподавателем за 20 секунд.
