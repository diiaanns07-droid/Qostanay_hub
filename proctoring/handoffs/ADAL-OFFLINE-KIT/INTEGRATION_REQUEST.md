# Заявка root: вызвать офлайн-проверку из текущего лаунчера

Владелец изменений — root/интегратор (`acceptance/classroom/Start-*.ps1`, `launcher-tests/`, README).
ADAL-OFFLINE-KIT эти файлы не меняет. Новый лаунчер, сервер или UI не нужен; preflight приложения не обходится,
ключа «пропустить проверку» нет.

## 1. Что вызывать (обязательно)

Команда (только стандартная библиотека Python, без сети, без записи в checkout, ~1 с на быстром режиме):

```
<python.exe лаунчера> -I -B <root>\acceptance\offline\adal_offline_kit.py check-pc --role student|teacher --python <python.exe> [--kit <каталог комплекта>]
```

- `-I`: не берёт `PYTHONPATH`/user site (инструмент не импортирует приложение); `-B`: не пишет `__pycache__` в checkout.
- Окружение: **родительское**, как у backend, без изменений. Так `QORGAU_MODELS_DIR` и `LOCALAPPDATA` разрешаются так же,
  как в backend, который запускает Electron (`desktop/main/src/backend/process.ts:48-57` пропускает их дальше).
- Коды выхода: `0` готово; `1` нет/повреждено/не та платформа; `2` BLOCKED (не подтверждены источник/лицензия/пин);
  `64` ошибка вызова. Вывод уже на русском, со строкой «Как исправить» для каждого проблемного файла.
- Роль `student` проверяет: venv (PE x64, `pyvenv.cfg` 3.12.x, существующий `home` — ловит venv, скопированный с
  другого ПК), версии runtime+cv пакетов по `requirements/full.txt` (без импорта), модели phone/attention (required)
  и identity/audio (optional, не влияют на код выхода) по пинам владельцев, Electron (`package.json` = lock,
  `dist\electron.exe` PE x64, `dist\version`), сборку `desktop\dist\*`, а также `[ВНИМАНИЕ]`: `path.txt`
  (ловушка неявной загрузки Electron), не-ASCII путь к моделям, сетевые команды в скриптах запуска.
- Роль `teacher`: venv и runtime-пакеты, `class-panel\index.html`.

## 2. Куда вставить (точные места в baseline 2974f64)

`acceptance/classroom/Start-Teacher.ps1` — общая функция рядом с `Test-QorgauPython` (после строки 194):

```powershell
function Test-QorgauOfflineKit([string]$PythonExe, [string]$Root, [string]$Role, [string]$Kit, [switch]$BackendOnly) {
    $tool = Join-Path $Root 'acceptance\offline\adal_offline_kit.py'
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw 'В этой копии нет проверки офлайн-комплекта (acceptance\offline\adal_offline_kit.py).'
    }
    $arguments = @('-I', '-B', $tool, 'check-pc', '--role', $Role, '--python', $PythonExe)
    if ($Kit) { $arguments += @('--kit', [IO.Path]::GetFullPath($Kit)) }
    if ($BackendOnly) { $arguments += '--backend-only' }
    & $PythonExe @arguments
    switch ($LASTEXITCODE) {
        0 { Write-Host 'Офлайн-готовность: файлы, хэши и платформа в порядке. Камера и точность CV проверяются в приложении.' }
        1 { throw 'Офлайн-комплект не готов: см. строки [НЕТ]/[БИТЫЙ]/[ПЛАТФОРМА] и «Как исправить» выше. Подготовка — acceptance\offline\REHEARSAL_3PC.md, этап 0 (нужен интернет до отключения).' }
        2 { throw 'Офлайн-комплект заблокирован (BLOCKED): не подтверждены источник, лицензия или хэш. Демонстрацию не начинать до решения в handoffs\ADAL-OFFLINE-KIT.' }
        default { throw "Проверка офлайн-комплекта не выполнена (код $LASTEXITCODE)." }
    }
}
```

- Учитель: строка 264, сразу после `Test-QorgauPython $pythonExe $root $environment`:
  `Test-QorgauOfflineKit $pythonExe $root 'teacher' $null`.
- Студент (`Start-Student.ps1`): строка 50, сразу после `Test-QorgauPython ... -Student`, до проверок Electron
  (строки 51-71): `Test-QorgauOfflineKit $pythonExe $root 'student' $student.OfflineKit -BackendOnly:$student.BackendOnly`.
  Новый необязательный параметр `[string]$OfflineKit` (в `param` и в хэш `$student`); с ним дополнительно
  сравнивается `desktop\node_modules\electron\dist` с архивом комплекта.
- Вызов выполняется и при `-CheckOnly`, и при обычном запуске. Для `-BackendOnly` добавить к аргументам
  `--backend-only` (Electron и `desktop\dist` не проверяются, модели и пакеты — проверяются) — это делает
  переключатель `-BackendOnly` функции выше.
- Сообщения существующих `catch` («Не готово: …», код 1) сохраняются: исключение из функции попадает туда.

## 3. Последствия, которые root должен учесть

- `launcher-tests/test_launchers.py`: на машинах **без весов** `Start-Student.ps1 -CheckOnly` станет возвращать 1
  («Офлайн-комплект не готов»). Это правильное поведение (отсутствие весов — не PASS). Тесты должны ожидать
  это сообщение без весов, а PASS — только на подготовленном ПК.
- `packaging/launch-windows.ps1:21` запускает `node_modules\.bin\electron.cmd` → `electron\index.js`, который при
  отсутствии `path.txt` или `dist\<exe>` вызывает `install.js` и **скачивает Electron** (`@electron/get`, GitHub).
  Это неявный сетевой фолбэк. Рекомендация: запускать `node_modules\electron\dist\electron.exe` напрямую, как
  `Start-Student.ps1:52,93`, или удалить этот лаунчер, если он устарел. `Start-Student.ps1` этим не затронут.
- `Test-QorgauPython` импортирует только fastapi/uvicorn/websockets/pydantic/numpy; отсутствие cv-пакетов и весов
  раньше обнаруживалось лишь в preflight приложения. `check-pc` закрывает это до старта, без импорта.

## 4. Предложения другим владельцам (решение за root)

1. **Каталог моделей** (A13/A14/root). Runtime: `QORGAU_MODELS_DIR`, иначе `proctoring\models` (`settings.py:39`).
   `identity.prepare` по умолчанию пишет в `%LOCALAPPDATA%\QorgauExam\models` (`identity/prepare.py:31-36`) —
   веса могут оказаться не там, где их ищет backend. Предложение: `default_models_dir()` →
   `Settings.from_env().models_dir`, как в `phone/prepare.py:90` и `attention/model_tool.py:67`.
   Silero читается только из `%LOCALAPPDATA%\QorgauExam\models\audio` (`audio/assets.py:14-18`) и игнорирует
   `QORGAU_MODELS_DIR`; это нужно хотя бы явно указать в README класса (сейчас там совет про `QORGAU_MODELS_DIR`).
2. **Телеметрия MediaPipe** (A04/root, приватность). Факт на Linux: создание/закрытие FaceLandmarker открывает HTTPS к
   `play.googleapis.com:443` и отправляет данные; без сети — быстрые неудачные DNS-попытки, без зависания.
   Windows-DLL импортирует WinINet с теми же строками (гипотеза о том же поведении). Нужны: решение о раскрытии/
   блокировке (правило исходящего брандмауэра — решение IT, не скрипта) и замер задержки старта сессии в ЛВС без интернета.
3. **OFFLINE_ASSETS в preflight приложения** (A01/root, `session.py:420-426`, сейчас NOT_RUN). Не дублировать
   phone_model/face_model. Минимум: `required=False`, `PASS`, если identity и Silero прошли проверку пинов,
   иначе `FAIL` с `message_ru` «Сверка лица/детектор речи недоступны: нет файла модели» и `details` с кодами здоровья.
4. **Энергетический фолбэк звука** (A14/fusion). Без Silero микрофон в LIVE всё равно работает
   (`session.py:713-723`), а инциденты «речь» из `energy_fallback` не помечены как эвристические
   (`audio/fusion.py:26-27,108-112`). Предложение: причина/пометка в инциденте и запись в конфиг сессии.
