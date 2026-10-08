# Офлайн-репетиция Adal на 3 ПК (Windows x64)

Протокол для людей у трёх реальных ПК. Из облачного контейнера он **не выполнялся**: здесь проверены только
инструмент, пины, загрузка из заявленных источников и офлайн-разрешение колёс (см. `handoffs/ADAL-OFFLINE-KIT/STATUS.md`).
Физический пилот считается состоявшимся только с заполненной таблицей «Результат» внизу.

Роли: **PC1** — преподаватель (C1 + панель), **PC2/PC3** — студенты (Electron + backend/C2 + веб-камера).
Используется текущий единый запуск класса: `acceptance\classroom\Start-Teacher.ps1` и `Start-Student.ps1`.
Новых лаунчеров, серверов и UI комплект не добавляет; preflight приложения не обходится.

Правила: никакие сетевые адаптеры/брандмауэр/реестр скриптами не меняются. PIN, код класса, токены, видео,
лица и голоса **не** записываются в протокол и не публикуются. Участник перед камерой — член команды, давший согласие.

## Этап 0. С интернетом, заранее (на каждом ПК, одна и та же фиксация)

Путь копии без пробелов и не-ASCII, например `C:\Adal\Qostanay_hub` (имя пользователя Windows может быть
кириллическим — тогда не кладите копию в профиль). Причина: FaceLandmarker открывает модель по пути
(`attention/landmarker.py:131`), поведение MediaPipe с не-ASCII путём в Windows не проверено.

```powershell
git -C C:\Adal\Qostanay_hub rev-parse HEAD           # записать в таблицу, одинаково на 3 ПК
Set-Location C:\Adal\Qostanay_hub\proctoring
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements\full.txt
```

Только PC2/PC3 (студенты), там же:

```powershell
$env:PYTHONPATH = "$PWD\backend;$PWD\contracts\python"
.\.venv\Scripts\python.exe -m proctor.phone.prepare --download --models-dir "$PWD\models"
.\.venv\Scripts\python.exe -m proctor.attention.model_tool fetch --models-dir "$PWD\models"
# рекомендуется (без Silero микрофон работает по эвристике энергии и может открыть непомеченный инцидент «речь»):
.\.venv\Scripts\python.exe -m proctor.audio.prepare --download     # кладёт в %LOCALAPPDATA%\QorgauExam\models\audio
# необязательно (без него «Сверка лица: Недоступно», экзамен не блокируется):
.\.venv\Scripts\python.exe -m proctor.identity.prepare --download --models-dir "$PWD\models"
Remove-Item Env:PYTHONPATH
Set-Location desktop; npm ci; node node_modules/electron/install.js; npm run build; Set-Location ..
node desktop\main\tools\hash-pin.mjs    # PIN оператора для экспорта отчёта; значение не записывать в протокол
```

Каталог моделей здесь — `proctoring\models` (это значение backend по умолчанию, `settings.py:39`), поэтому
`QORGAU_MODELS_DIR` при запуске не нужен. Если всё же используете другой каталог, задайте `QORGAU_MODELS_DIR`
в **том же** окне PowerShell перед запуском лаунчера и проверкой. Silero всегда читается из
`%LOCALAPPDATA%\QorgauExam\models\audio` учётной записи, под которой запускается Adal.

Комплект (на одном ПК с подготовленными файлами; на выходе — новый каталог вне репозитория, например флешка):

```powershell
.\.venv\Scripts\python.exe -m pip download --only-binary=:all: --require-hashes --no-deps -r requirements\full.txt -d D:\AdalPrep\wheels
.\.venv\Scripts\python.exe acceptance\offline\adal_offline_kit.py build --models-dir "$PWD\models" `
    --audio-dir "$env:LOCALAPPDATA\QorgauExam\models\audio" --wheelhouse D:\AdalPrep\wheels `
    --electron-zip "$env:LOCALAPPDATA\electron\Cache" --out E:\AdalKit
```

`build` копирует только файлы, совпавшие с пинами репозитория, и отказывается собирать комплект, если не хватает
обязательного. Он не скачивает, не запускает pip/npm и не пишет в исходные каталоги.

ПК, которому интернет недоступен вовсе, готовится из комплекта (все команды без сети):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links E:\AdalKit\wheels --require-hashes -r requirements\full.txt
$env:PYTHONPATH = "$PWD\backend;$PWD\contracts\python"
.\.venv\Scripts\python.exe -m proctor.phone.prepare --from-file E:\AdalKit\models\phone\yolo11n.onnx --models-dir "$PWD\models"
New-Item -ItemType Directory -Force "$PWD\models\attention" | Out-Null
Copy-Item -LiteralPath E:\AdalKit\models\attention\face_landmarker.task -Destination "$PWD\models\attention\"
$audio = Join-Path $env:LOCALAPPDATA 'QorgauExam\models\audio'
New-Item -ItemType Directory -Force $audio | Out-Null
Copy-Item -Path E:\AdalKit\models\audio\* -Destination $audio
Remove-Item Env:PYTHONPATH
```

Electron без интернета (если `node_modules\electron` есть, а `dist` нет/повреждён): распаковать архив
комплекта в `desktop\node_modules\electron\dist` и записать `path.txt` (иначе `node_modules\electron\index.js`
при запуске через `electron.cmd`/`npm start` попробует скачать Electron):

```powershell
Expand-Archive -LiteralPath E:\AdalKit\electron\electron-v43.7.5-win32-x64.zip -DestinationPath desktop\node_modules\electron\dist -Force
Set-Content -LiteralPath desktop\node_modules\electron\path.txt -Value 'electron.exe' -NoNewline -Encoding ascii
```

`npm ci`/`npm install` без интернета **не запускать**: npm-кэша в комплекте нет, а `npm ci` начинает с удаления
`node_modules` (документированное поведение npm, здесь не проверено). Сборку desktop делайте на этапе 0. После
`npm run build` не меняйте исходники и не делайте `git pull`/`checkout`: `Start-Student.ps1:62-70` сравнивает время
изменения исходников и сборки и откажется запускаться («Исходники новее сборки»); пересборка офлайн возможна только
если Node и полный `node_modules` этого же ПК уже на месте. Копировать `.venv` или `desktop\dist` с другого ПК не
нужно: проверка распознаёт перенесённый venv. Альтернатива распаковке Electron: положить архив комплекта в кэш
`%LOCALAPPDATA%\electron\Cache\2dc4f83cdcc3b8446f9d909bd697caa69bbfef832d7e01faba947002008f886b\` и выполнить
`node node_modules/electron/install.js` (без сети, но нужен VC++ 2015-2022 x64 runtime для распаковщика).

## Этап 1. Отключить интернет, сохранив локальную сеть (вручную)

Рекомендуемый способ — отключить **WAN-кабель/uplink роутера** (DHCP и Wi-Fi/Ethernet между ПК остаются).
Не отключайте адаптеры ПК и не меняйте брандмауэр скриптами. На каждом ПК зафиксируйте:

```powershell
Test-NetConnection 1.1.1.1 -Port 443 -InformationLevel Quiet     # ожидается False
Resolve-DnsName pypi.org -ErrorAction SilentlyContinue           # ожидается пусто/ошибка
ipconfig | Select-String IPv4                                     # адрес в ЛВС есть
```

## Этап 2. Офлайн-проверка комплекта и ПК

```powershell
Set-Location C:\Adal\Qostanay_hub\proctoring
.\.venv\Scripts\python.exe acceptance\offline\adal_offline_kit.py verify --kit E:\AdalKit          # любой ПК
.\.venv\Scripts\python.exe acceptance\offline\adal_offline_kit.py check-pc --role teacher          # PC1
.\.venv\Scripts\python.exe acceptance\offline\adal_offline_kit.py check-pc --role student --kit E:\AdalKit --deep   # PC2/PC3
```

Код 0 — файлы/хэши/платформа в порядке; 1 — что-то отсутствует/повреждено/не та платформа (строка «Как исправить»);
2 — BLOCKED (не подтверждены источник/лицензия/пин); 64 — ошибка вызова; 70 — внутренняя ошибка инструмента.
`[ВНИМАНИЕ]` не блокирует, но фиксируется.
Затем штатные проверки лаунчеров:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\acceptance\classroom\Start-Teacher.ps1 -CheckOnly                        # PC1
powershell -NoProfile -ExecutionPolicy Bypass -File .\acceptance\classroom\Start-Student.ps1 -Server <PC1>:8765 -JoinCode <код> -Label PC2 -CheckOnly
```

## Этап 3. Запуск класса без интернета

1. PC1: `Start-Teacher.ps1 -Lan -Port 8765`; дождаться «Готово»; открыть `http://127.0.0.1:8765/`, ввести PIN из консоли;
   создать класс; код передать устно. Если PC2 не подключается — вручную проверить входящий TCP 8765 в **частном**
   профиле брандмауэра PC1 (`acceptance/classroom/README.md`).
2. PC2/PC3: в окне PowerShell задать `$env:QORGAU_OPERATOR_PIN_HASH` (из этапа 0), затем `Start-Student.ps1 -Server <IP PC1>:8765 -JoinCode <код> -Label PC2`.
3. В приложении: согласие → проверка оборудования. Зафиксировать строки preflight: `phone_model`, `face_model` = PASS
   (это загрузка реальных весов), `offline_assets` = NOT_RUN (ожидаемо до интеграции, см. INTEGRATION_REQUEST.md).
   Записать время от нажатия старта проверки до готовности (гипотеза о задержке телеметрии MediaPipe без интернета).
4. Калибровка → начать экзамен в режиме LIVE (не REPLAY/синтетика). На Windows старт экзамена проверяет нативный
   помощник (`desktop/native/qorgau_guard.py --environment-check`, таймаут 3 с, `desktop/main/src/environment/remote.ts:30`).
   Если старт отклонён с «Не удалось проверить программы удалённого доступа» — записать и повторить (холодный старт
   Python/антивирус), это не сетевая проблема.

## Этап 4. Один CV-эпизод

На PC2 участник держит телефон в кадре ~5 с (`phone_visible`: ≥1 с, ≥3 наблюдения, `fusion/config.py:85`)
**или** смотрит в сторону ~5 с (`gaze_prolonged_side`: ≥3 с, `fusion/config.py:90`). Зафиксировать:
время, правило, появилось ли событие у студента и в карточке/очереди преподавателя на PC1 (без скриншотов лиц).
Это проверка сквозного пути офлайн, не измерение точности CV.

## Этап 5. Локальный отчёт

Завершить экзамен на PC2 → экран итога → разблокировать оператором (PIN) → «Экспорт HTML» и «Экспорт JSON»
(`desktop/main/src/ipc/api.ts:259-269`; формат `qorgau.report.v1`, HTML без внешних ссылок, CSP `default-src 'none'`,
`backend/proctor/evidence/report.py`). Сохранить во временную папку вне репозитория, открыть HTML **без интернета**,
убедиться, что эпизод виден; записать только id сессии/инцидента и SHA-256 файла отчёта. Отчёт не публиковать.

## Этап 6. Перезапуск

1. PC2: закрыть приложение и убедиться в Диспетчере задач, что `electron.exe` не остался (иначе второй запуск молча
   завершится из-за single-instance, `desktop/main/src/main.ts:58-61`); снова `Start-Student.ps1 …`; убедиться, что
   подключение к классу восстановлено, прежняя сессия доступна в истории/отчёте.
2. PC1: `Ctrl+C`, снова `Start-Teacher.ps1 -Lan -Port 8765`; войти, проверить, что класс/история сохранились
   (данные C1 в `%LOCALAPPDATA%\QorgauClassroom`).
3. Повторить `check-pc` на PC2 (код 0) — комплект и ПК не изменились от работы приложения.
4. Вернуть интернет (WAN роутера) только после записи результатов.

## Результат (заполняют люди у ПК)

| Шаг | PC1 | PC2 | PC3 | Примечание |
|---|---|---|---|---|
| HEAD одинаков | | | | |
| Интернет недоступен / ЛВС есть | | | | |
| `verify --kit` код | | | | |
| `check-pc` код (+ ВНИМАНИЕ) | | | | |
| `-CheckOnly` лаунчера | | | | |
| preflight phone_model / face_model | — | | | |
| время до готовности preflight, с | — | | | |
| CV-эпизод: правило, видно у преподавателя | — | | | |
| отчёт HTML/JSON сохранён и открыт офлайн | — | | | |
| перезапуск студента / C1 | | | | |
| `check-pc` после перезапуска | | | | |

Любая ячейка «нет»/«не делали» — это блокер, а не PASS. Отсутствующие веса — не PASS.
