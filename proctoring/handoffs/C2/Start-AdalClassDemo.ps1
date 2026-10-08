#Requires -Version 5.1
<#
.SYNOPSIS
Демо режима класса Adal на ОДНОМ ноутбуке одной командой (для записи видео).

.DESCRIPTION
1. Сервер класса T01 на 127.0.0.1:<Port> (по умолчанию 8765) в окне «Adal · Сервер класса»; PIN задаётся скриптом.
2. Сессия класса через API T01 (python -m proctor.uplink.demo_teacher session) -> печатает шестизначный код.
3. Проверка T03: /api/teacher/info (history mounted), модуль панели 200, клипы не 501; иначе сцену клипа НЕ снимать.
4. Панель T02 в браузере по умолчанию (PIN копируется в буфер обмена).
5. Окно «Adal · Преподаватель» с функцией demo_teacher (start / lock "причина" / unlock / finish / students).
6. Adal-студент (acceptance\classroom\Start-Student.ps1) с QORGAU_CLASS_SERVER/QORGAU_CLASS_CODE и
   REPLAY-роликом <ReplayId> (по умолчанию demo_class_red_loop): на превью полоса «REPLAY · запись».
Ничего не устанавливает и не скачивает, ExecutionPolicy меняется только для дочерних окон (-ExecutionPolicy Bypass).
Сценарий видео: handoffs\C2\CLASS_DEMO.md, раздел «Видео 2 минуты».

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\handoffs\C2\Start-AdalClassDemo.ps1
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\proctoring\handoffs\C2\Start-AdalClassDemo.ps1 -Python 'C:\path\.venv\Scripts\python.exe'
.EXAMPLE
... Start-AdalClassDemo.ps1 -CheckOnly   # без окон: сервер, код, проверка T03, студент без окна (backend+C2) подключается; всё останавливается
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [string]$ClassTitle = '10А, физика — демо',
    [string]$StudentLabel = 'Студент 1 (REPLAY-демо)',
    [ValidatePattern('^[A-Za-z0-9_.:-]{1,128}$')][string]$ReplayId = 'demo_class_red_loop',
    [string]$Python,
    [string]$Features = $env:QORGAU_CLASS_FEATURES,
    [ValidatePattern('^[0-9]{4,8}$')][string]$OperatorPin = '2468',
    [string]$DataDir,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$savedEnv = @{}
$server = $null
$student = $null

function Q([string]$s) { "'" + $s.Replace("'", "''") + "'" }

function Set-DemoEnv([string]$Name, [string]$Value) {
    if (-not $savedEnv.ContainsKey($Name)) { $savedEnv[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process') }
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}

function Test-PortBusy([int]$P) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $P, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(400)) { $client.EndConnect($iar); return $true }
        return $false
    } catch { return $false } finally { $client.Close() }
}

function Get-HttpStatus([string]$Url) {
    try {
        $req = [Net.HttpWebRequest]::Create($Url)
        $req.Timeout = 2000
        $req.AllowAutoRedirect = $false
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        return $code
    } catch [Net.WebException] {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

function Invoke-Py([string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'  # PS 5.1: native stderr lines must not become terminating errors
    $out = & $script:py @Arguments 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw ("python " + ($Arguments[0..1] -join ' ') + ": " + (($out | Select-Object -Last 3) -join ' ')) }
    return $out
}

function Start-DemoWindow([string]$Title, [string]$Body, [switch]$Hidden) {
    $cmd = "`$Host.UI.RawUI.WindowTitle = $(Q $Title)`r`n" +
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8`r`n" +
           "Set-Location -LiteralPath $(Q $script:proctoring)`r`n" + $Body
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass')
    if (-not $Hidden) { $psArgs += '-NoExit' }
    $psArgs += @('-EncodedCommand', $encoded)
    $style = if ($Hidden) { 'Hidden' } else { 'Normal' }
    Start-Process -FilePath 'powershell.exe' -ArgumentList $psArgs -WindowStyle $style -PassThru
}

try {
    # ------------------------------------------------------------------ 0. paths, python, inputs
    $script:proctoring = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    if (-not (Test-Path -LiteralPath (Join-Path $proctoring 'classroom\server\__main__.py'))) {
        throw "Не найден proctoring\classroom\server рядом со скриптом ($proctoring)."
    }
    if (-not $Python) { $Python = Join-Path $proctoring '.venv\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python не найден: $Python. Подготовьте proctoring\.venv (acceptance\classroom\README.md) или передайте -Python 'C:\путь\python.exe'."
    }
    $script:py = (Resolve-Path -LiteralPath $Python).Path
    $local = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
    $replayDir = if ($env:QORGAU_REPLAY_DIR) { $env:QORGAU_REPLAY_DIR } else { Join-Path $local 'QorgauExam\replay' }
    $modelsDir = if ($env:QORGAU_MODELS_DIR) { $env:QORGAU_MODELS_DIR } else { Join-Path $local 'QorgauExam\models' }
    if (-not $DataDir) { $DataDir = Join-Path $local ('QorgauClassroom-demo\' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
    $DataDir = [IO.Path]::GetFullPath($DataDir)  # fresh dir: no "offline" students from earlier runs in the panel

    Set-DemoEnv 'PYTHONPATH' ((@($proctoring, (Join-Path $proctoring 'backend'), (Join-Path $proctoring 'contracts\python'))) -join ';')
    Set-DemoEnv 'PYTHONIOENCODING' 'utf-8'
    Set-DemoEnv 'PYTHONUNBUFFERED' '1'
    Set-DemoEnv 'PYTHONDONTWRITEBYTECODE' '1'

    $manifest = Join-Path $replayDir "$ReplayId.json"
    if (-not (Test-Path -LiteralPath $manifest)) {
        $source = Join-Path $replayDir 'zone_c_red_01.json'
        if ($ReplayId -ne 'demo_class_red_loop' -or -not (Test-Path -LiteralPath $source)) {
            throw "Нет REPLAY-ролика $manifest. Ролики с людьми (с их согласия) лежат вне Git: handoffs\A02\DEMO_CLIPS.md."
        }
        # demo_class_red_loop = zone_c_red_01 played in a loop (same media, consent recorded in its manifest)
        Invoke-Py @('-c', "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); d['replay_id']=sys.argv[3]; d['loop']=True; json.dump(d,open(sys.argv[2],'w',encoding='utf-8'),ensure_ascii=False,indent=2)", $source, $manifest, $ReplayId) | Out-Null
        Write-Host "Создан $ReplayId.json (копия zone_c_red_01 по кругу) в $replayDir"
    }
    if (Test-PortBusy $Port) {
        throw "Порт $Port уже занят (сервер класса из прошлого запуска?). Закройте его окно «Adal · Сервер класса» или укажите -Port."
    }

    # ------------------------------------------------------------------ 1. class server T01
    $bytes = New-Object byte[] 4
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $pin = '{0:D6}' -f ([BitConverter]::ToUInt32($bytes, 0) % 1000000)
    Set-DemoEnv 'QORGAU_CLASS_TEACHER_PIN' $pin  # the server takes its PIN from here; children inherit it (never on a command line)
    $serverArgs = "--host 127.0.0.1 --port $Port --data-dir $(Q $DataDir)"
    if ($Features) { $serverArgs += " --features $(Q $Features)" }
    $serverBody = "Write-Host 'Сервер класса T01: http://127.0.0.1:$Port/   PIN преподавателя: $pin' -ForegroundColor Cyan`r`n" +
                  "Write-Host 'Не закрывайте это окно во время записи. Остановить: закрыть окно.' -ForegroundColor DarkGray`r`n" +
                  "& $(Q $py) -m classroom.server $serverArgs`r`n" +
                  "Write-Host 'Сервер класса остановлен.' -ForegroundColor Yellow"
    Write-Host "1/6 Сервер класса T01 на 127.0.0.1:$Port ..."
    $server = Start-DemoWindow 'Adal · Сервер класса (T01)' $serverBody -Hidden:$CheckOnly
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-HttpStatus "http://127.0.0.1:$Port/login") -ne 200) {
        if ((Get-Date) -gt $deadline) { throw "Сервер класса не ответил за 45 с. Смотрите окно «Adal · Сервер класса»." }
        if ($server.HasExited) { throw "Сервер класса завершился при запуске (Python/зависимости?). Запустите вручную: python -m classroom.server --port $Port" }
        Start-Sleep -Milliseconds 300
    }

    # ------------------------------------------------------------------ 2. class session + join code
    Write-Host "2/6 Сессия класса «$ClassTitle» ..."
    $created = Invoke-Py @('-m', 'proctor.uplink.demo_teacher', '--server', "127.0.0.1:$Port", 'session', $ClassTitle)
    $joinLine = [string]($created | Where-Object { $_ -match 'join code:\s*[0-9]{6}' } | Select-Object -First 1)
    if ($joinLine -notmatch 'join code:\s*([0-9]{6})') { throw "Сервер не вернул код класса: $($created -join ' ')" }
    $code = $Matches[1]

    # ------------------------------------------------------------------ 3. T03 (clips) installed?
    $probe = Invoke-Py @('-c', "import os,sys,urllib.error`nfrom proctor.uplink.demo_teacher import Teacher`nt=Teacher(sys.argv[1], os.environ['QORGAU_CLASS_TEACHER_PIN'])`ndef code(path):`n    try:`n        with t.http.open(t.base+path, timeout=10) as r: return r.status`n    except urllib.error.HTTPError as e: return e.code`nst={f['name']:f['status'] for f in (t.call('GET','/api/teacher/info') or {}).get('features',[])}`nprint('T03_HISTORY', st.get('history','absent'))`nprint('T03_UI', code('/api/teacher/history/assets/register.js'))`nprint('T03_CLIPS', code('/api/teacher/clips/t03-probe'))", "127.0.0.1:$Port")
    $val = { param($k) (($probe | Where-Object { $_ -like "$k *" } | Select-Object -First 1) -replace "^$k\s*", '').Trim() }
    $t03History = & $val 'T03_HISTORY'; $t03Ui = & $val 'T03_UI'; $t03 = & $val 'T03_CLIPS'
    # T03 mounted: history feature "mounted", its panel module 200, clip path 404 for an unknown id (501 = not installed)
    $clipScene = $t03History -eq 'mounted' -and $t03Ui -eq '200' -and $t03 -ne '501'
    $t03Text = if ($clipScene) { "подключён (история: mounted, модуль панели: HTTP $t03Ui, клипы: HTTP $t03 на пробный id вместо 501) — сцену клипа можно снимать; клип AVI скачивается и открывается в плеере" } elseif ($t03 -eq '501') { 'НЕ подключён (HTTP 501 feature_not_installed) — сцену клипа НЕ снимать' } else { "неполный (история: $t03History, модуль панели: HTTP $t03Ui, клипы: HTTP $t03) — сцену клипа НЕ снимать" }
    Write-Host "3/6 Клипы (T03): $t03Text"

    # ------------------------------------------------------------------ 4..6 windows (or a headless check)
    $pinHash = ''
    Set-DemoEnv 'QORGAU_DEMO_OPERATOR_PIN' $OperatorPin
    $hashOut = Invoke-Py @('-c', "import hashlib,os,sys`npin=os.environ['QORGAU_DEMO_OPERATOR_PIN'].encode()`nsalt=os.urandom(16)`nprint('scrypt:'+salt.hex()+':'+hashlib.scrypt(pin,salt=salt,n=16384,r=8,p=1,dklen=32).hex())")
    Set-DemoEnv 'QORGAU_DEMO_OPERATOR_PIN' $null  # same scrypt parameters as desktop\main\tools\hash-pin.mjs; PIN only via env
    $pinHash = ($hashOut | Where-Object { $_ -like 'scrypt:*' } | Select-Object -First 1)
    Set-DemoEnv 'QORGAU_REPLAY_DIR' $replayDir
    Set-DemoEnv 'QORGAU_MODELS_DIR' $modelsDir
    if ($pinHash) { Set-DemoEnv 'QORGAU_OPERATOR_PIN_HASH' $pinHash }
    $studentScript = Join-Path $proctoring 'acceptance\classroom\Start-Student.ps1'

    if ($CheckOnly) {
        Write-Host '4/6 -CheckOnly: студент без окна (backend + C2 uplink, 25 с) подключается к классу ...'
        $studentBody = "& $(Q $studentScript) -Server '127.0.0.1:$Port' -JoinCode '$code' -Label $(Q $StudentLabel) -Python $(Q $py) -BackendOnly -StopAfterSeconds 25"
        $student = Start-DemoWindow 'Adal · студент (проверка)' $studentBody -Hidden
        $found = $null
        $deadline = (Get-Date).AddSeconds(40)
        while (-not $found -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
            $list = Invoke-Py @('-m', 'proctor.uplink.demo_teacher', '--server', "127.0.0.1:$Port", 'students')
            $found = $list | Where-Object { $_ -match 'REPLAY' -and $_ -match '\bonline\b' } | Select-Object -First 1
        }
        if (-not $found) { throw "Студент не появился в классе за 40 с. Список: $($list -join ' | ')" }
        Write-Host "5/6 Студент в классе: $found" -ForegroundColor Green
        # Electron build present and not older than its sources (the same check Start-Student.ps1 does before a window)
        $buildCheck = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $studentScript -Server "127.0.0.1:$Port" -JoinCode $code -Label $StudentLabel -Python $py -CheckOnly 2>&1 | ForEach-Object { "$_" }
        if ($LASTEXITCODE -ne 0) { throw "Сборка Adal не готова: $(($buildCheck | Select-Object -Last 1))" }
        Write-Host "6/6 Проверка без окон пройдена: сервер, код $code, T03 history=$t03History UI=$t03Ui clips=$t03, студент подключился, сборка Adal готова. Всё останавливается." -ForegroundColor Green
        exit 0
    }

    Write-Host '4/6 Панель преподавателя T02 в браузере ...'
    try { Set-Clipboard -Value $pin } catch { }
    Start-Process "http://127.0.0.1:$Port/login" | Out-Null

    Write-Host '5/6 Окно «Преподаватель» (команды demo_teacher) ...'
    $clipHint = if ($clipScene) { "Write-Host '  demo_teacher clip                     — запросить клип последнего эпизода; AVI: скачать и открыть в плеере'" } else { "Write-Host '  (clip — не использовать: модуль клипов T03 на сервере не подключён, HTTP 501)' -ForegroundColor DarkGray" }
    $teacherBody = @"
function global:demo_teacher { & $(Q $py) -m proctor.uplink.demo_teacher --server '127.0.0.1:$Port' @args }
Write-Host 'Adal · Преподаватель. Класс «$($ClassTitle.Replace("'", "''"))», код $code, PIN панели $pin' -ForegroundColor Cyan
Write-Host 'Команды (по очереди, после того как студент нажал «Пропустить калибровку»):' -ForegroundColor Cyan
Write-Host '  demo_teacher start                    — начать экзамен у студента'
Write-Host '  demo_teacher lock "Телефон в руках — подождите преподавателя"   — заблокировать экран с причиной'
Write-Host '  demo_teacher unlock                   — снять блокировку'
Write-Host '  demo_teacher finish                   — завершить экзамен'
Write-Host '  demo_teacher students                 — список студентов, зона, блокировка'
$clipHint
"@
    $teacher = Start-DemoWindow 'Adal · Преподаватель' $teacherBody

    Write-Host "6/6 Adal-студент «$StudentLabel» (REPLAY $ReplayId) ..."
    $studentBody = "Write-Host 'Adal-студент: класс 127.0.0.1:$Port, REPLAY-ролик $ReplayId (не живая камера). PIN оператора: $OperatorPin' -ForegroundColor Cyan`r`n" +
                   "& $(Q $studentScript) -Server '127.0.0.1:$Port' -JoinCode '$code' -Label $(Q $StudentLabel) -Python $(Q $py)"
    $student = Start-DemoWindow 'Adal · студент (REPLAY)' $studentBody
    $student = $null; $server = $null  # visible windows stay open after this script ends

    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Green
    Write-Host "  Код класса: $code        PIN панели: $pin (скопирован, Ctrl+V)" -ForegroundColor Green
    Write-Host "  PIN оператора Adal (пропуск калибровки в REPLAY): $OperatorPin" -ForegroundColor Green
    Write-Host "  Клипы T03: $t03Text"
    Write-Host '  В Adal: «запись (replay)» -> id ' -NoNewline; Write-Host $ReplayId -ForegroundColor Yellow -NoNewline; Write-Host ' -> согласие -> «Создать сессию и проверить» -> пропустить калибровку (PIN, причина «REPLAY-демо»).'
    Write-Host '  Сценарий видео: proctoring\handoffs\C2\CLASS_DEMO.md, «Видео 2 минуты».'
    Write-Host '  Закончить: закрыть Adal, окна «Преподаватель» и «Сервер класса».'
    Write-Host '================================================================' -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Не готово: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    $ErrorActionPreference = 'Continue'
    foreach ($p in @($student, $server)) {
        if ($p -and -not $p.HasExited) {
            # -CheckOnly or a failure: stop the hidden helpers and their children (python, electron)
            & taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null
        }
    }
    foreach ($name in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnv[$name], 'Process') }
}
