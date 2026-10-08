# Запуск класса Qorgau на Windows

`Start-Teacher.ps1` запускает **настоящий C1/T01** (`classroom.server`) и текущую панель T02 в режиме REAL.
`Start-Student.ps1` запускает **текущую Electron-сборку** и backend/C2; `-BackendOnly` — отдельная диагностика backend без окна.
Симулятор не запускается. Сервер преподавателя, PIN входа и шестизначный код класса — разные вещи.

Команды ниже выполняются из корня вашей копии репозитория в Windows PowerShell 5.1 или PowerShell 7.
Скрипты также можно вызвать по абсолютному пути из любой папки. Они сами находят свой `proctoring/`.
Ничего не устанавливают, не скачивают, не меняют брандмауэр и не меняют постоянную ExecutionPolicy.

## Подготовка — один раз на каждом ПК

Нужны Python **3.12 x64**, а для студента — Node.js **22.12 или новее** и npm.
Установка зависимостей — отдельное явное действие с доступом в сеть:

```powershell
Set-Location .\proctoring
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements\full.txt
# Только для PC2/PC3 (студенты):
Set-Location .\desktop
npm ci
npm run build
Set-Location ..\..
```

Если `.venv` уже подготовлена в другой папке, передайте **обоим** launcher параметр
`-Python 'C:\полный путь\.venv\Scripts\python.exe'`. Пути конкретного ноутбука в launcher не зашиты.
`PYTHONPATH` принудительно указывает на исходники этой копии, даже если editable-install окружения указывает на старую.
Если Python отсутствует или его версия отличается, будет короткая ошибка и код завершения 1.

`npm ci` должен скачать и Windows binary Electron. Если пакет есть, а binary отсутствует,
из **этой** `proctoring\desktop` выполните `node node_modules/electron/install.js`, затем `npm run build`.
Не копируйте `dist` из другой версии проекта. После изменения исходников повторите `npm run build`;
launcher откажется запускать заведомо устаревшую сборку.

Проверка launcher не проверяет CV-модели/качество камеры. Для реального наблюдения используйте подготовленные модели
и штатную проверку оборудования внутри приложения. Если модели хранятся вне репозитория, до запуска укажите:

```powershell
$env:QORGAU_MODELS_DIR = "$env:LOCALAPPDATA\QorgauExam\models"
```

Это указание каталога, не загрузка моделей. Согласие, проверка камеры и калибровка выполняются в приложении.

## PC1 — преподаватель

Сначала безопасная локальная проверка:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\acceptance\classroom\Start-Teacher.ps1 -CheckOnly
```

Для просмотра только на PC1 запустите без `-CheckOnly`: адрес привязки будет `127.0.0.1`.
Для реальных PC2/PC3 в одной локальной сети запустите явно:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\acceptance\classroom\Start-Teacher.ps1 -Lan -Port 8765
```

Дождитесь «Готово», откройте **на PC1** напечатанный адрес `http://127.0.0.1:8765/` и введите PIN из консоли.
При `-Port 0` порт выбирает Windows; launcher покажет фактический URL.
Окно PowerShell оставьте открытым. PIN предназначен только преподавателю; студентам нужен код класса.
`-Lan` слушает `0.0.0.0`, но панель и API преподавателя по-прежнему доступны только с PC1.

### Создать сессию и получить код класса

В базовой панели T02 ещё нет создания сессии. В **другом окне PowerShell на PC1** выполните этот блок.
Замените `$base` на адрес, напечатанный launcher. PIN вводится скрыто, cookie хранится только в памяти:

```powershell
$base = 'http://127.0.0.1:8765'
$securePin = Read-Host 'PIN преподавателя из первого окна' -AsSecureString
$pin = [Net.NetworkCredential]::new('', $securePin).Password
$teacher = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-RestMethod "$base/api/teacher/login" -Method Post -ContentType 'application/json' `
  -Body (@{pin=$pin} | ConvertTo-Json -Compress) -WebSession $teacher | Out-Null
Remove-Variable pin, securePin
$body = @{title='Проверка класса'; mode='url'; allowed_urls=@('https://example.org/*')}
$session = Invoke-RestMethod "$base/api/teacher/session" -Method Post -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress))) -WebSession $teacher
$session.join_code
```

Адрес экзамена здесь — пример: замените его на адрес вашего задания. Передайте ученикам код, полученный от сервера.
Это сессия класса для подключения; готовность локального экзамена, камера и калибровка у студента проверяются отдельно.

## PC2 и PC3 — студенты

Используйте напечатанный адрес PC1 **той же Wi-Fi/Ethernet-сети**, например `192.168.1.10:8765`.
Не используйте `127.0.0.1` на другом ПК. Если выданы несколько адресов (например VPN), выберите адрес общей сети.
Одна копия приложения и один каталог данных на одного студента/ПК.

```powershell
$server = '192.168.1.10:8765' # заменить на адрес PC1
$code = Read-Host 'Шестизначный код класса'
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\acceptance\classroom\Start-Student.ps1 `
  -Server $server -JoinCode $code -Label 'PC2' -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\acceptance\classroom\Start-Student.ps1 `
  -Server $server -JoinCode $code -Label 'PC2'
```

На PC3 повторите с `-Label 'PC3'`. Аргумент `-JoinCode` принимает строку, включая ведущие нули.
В блоке «Класс» приложения дождитесь подтверждения подключения; запись «backend готов» сама по себе
не означает, что код принят сервером. Launcher скрывает код в своём выводе и не сохраняет логи.
Передача параметров выполняется только в окружении дочернего процесса; родительское окружение не изменяется.
`ELECTRON_RUN_AS_NODE` и внешний dev-renderer отключены для дочернего Electron, чтобы открылась эта сборка.
Нативное принудительное перехватывание клавиш выключено, даже если в родительской среде оно было включено.

## Остановка, данные и диагностика

- Преподаватель: `Ctrl+C` в окне launcher. Студент: завершите экзамен и закройте окно приложения.
  Аварийная клавиша оболочки: `Ctrl+Alt+Shift+F12`.
- Закрытие/аварийное завершение launcher останавливает его дочерние процессы через Windows Job Object.
  Для C1/backend обычная остановка сначала закрывает stdin и ждёт до 8 секунд; затем завершается дерево.
  Принудительное закрытие окна во время экзамена — аварийная остановка, а не нормальное завершение экзамена.
- По умолчанию данные C1 — `%LOCALAPPDATA%\QorgauClassroom`, студента — `%LOCALAPPDATA%\QorgauExam`
  (либо явно заданные переменные окружения приложения). Можно передать `-DataDir 'C:\QorgauData\class'`.
  Здесь находятся персональные данные/токены возобновления; не выбирайте папку репозитория и не коммитьте её.
- `-CheckOnly` проверяет локальный Python, импорты и пути; для студента также binary и актуальность сборки.
  Он **не** открывает камеры, микрофон, hooks, приложение или сетевые соединения; не создаёт сессию/каталог данных
  и не проверяет доступность PC1, правильность кода или реальное соединение по LAN.
- Для диагностики без Electron добавьте `-BackendOnly`. Это подключает настоящий C2/backend,
  но не создаёт экзамен и не включает камеру. Это не замена студенческому приложению.
- Если PC1 недоступен: убедитесь в `-Lan`, правильном адресе и одной сети; затем вручную проверьте разрешение
  входящего TCP выбранного порта в **частном** профиле Windows Firewall. Launcher не меняет эти правила.
- «Порт занят»: закройте прежний сервер или выберите другой `-Port`; используйте его и в `-Server` студентов.
- `-StopAfterSeconds 3` — диагностическая автоостановка после готовности C1 или `-BackendOnly`.
  Для Electron этот параметр недоступен.

## Проверки launcher

```powershell
.\proctoring\.venv\Scripts\python.exe .\proctoring\acceptance\classroom\launcher-tests\test_launchers.py
```

Тесты используют временные каталоги вне репозитория, фактический C1, loopback и свободные порты.
Проверяются parse PowerShell, режим проверки, ошибки, HTTP REAL-панель, штатное EOF-завершение и уничтожение
родительского launcher без оставшегося слушателя. PIN не сохраняется в отчёт.
Эти результаты не подтверждают работу настоящих камер, микрофона, LAN и одновременно работающих учебных ПК.
