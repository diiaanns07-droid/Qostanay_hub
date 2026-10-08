#Requires -Version 5.1
<#
.SYNOPSIS
Установка Adal на ПК с Windows из клонированного репозитория: требования, подготовка, проверка моделей, ярлыки.

.DESCRIPTION
Скрипт не устанавливает системные программы, не требует прав администратора и не меняет реестр,
службы, автозагрузку, брандмауэр и постоянную ExecutionPolicy. Отсутствующие программы он только
называет и печатает ссылки для установки.

Подготовку выполняет существующий packaging\prepare-windows.ps1 -FetchModels (закреплённые
Python-пакеты через uv, npm-пакеты и Electron, сборка desktop, модели YOLO11n и FaceLandmarker
с проверкой SHA256). Для неё один раз нужен интернет. Модели затем проверяются существующими
proctor.phone.prepare --check и proctor.attention.model_tool verify, запуск — Start-Adal.ps1 -CheckOnly
(без окна, камеры, микрофона, сети и hooks).

Ярлыки — файлы .lnk текущего пользователя: меню «Пуск» (папка Adal) и рабочий стол. Они запускают
Start-Adal.ps1 через Windows PowerShell с -ExecutionPolicy Bypass только для этого процесса.

Коды выхода: 0 — готово; 1 — ошибка подготовки, проверки или ярлыков; 2 — не выполнены требования
или неверные параметры.

.PARAMETER CheckOnly
Пробный запуск: проверяет требования и состояние, показывает план. Ничего не скачивает, не собирает,
не создаёт и не удаляет.
.PARAMETER NoShortcuts
Подготовить и проверить без создания ярлыков.
.PARAMETER Uninstall
Удалить только ярлыки, созданные этим скриптом. Окружение, модели и данные экзаменов остаются.
.PARAMETER Force
Повторить подготовку, даже если проверка показывает, что всё уже готово.
.PARAMETER Server
Адрес ПК преподавателя (адрес:порт) для ярлыка студента. Без него ярлык спрашивает адрес при запуске.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\Install-Adal.ps1 -CheckOnly
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\Install-Adal.ps1
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\Install-Adal.ps1 -Server 192.168.1.10:8765
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\Install-Adal.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$NoShortcuts,
    [switch]$Uninstall,
    [switch]$Force,
    [string]$Server
)

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$StartAdal = Join-Path $ProjectRoot 'Start-Adal.ps1'
$PrepareScript = Join-Path $PSScriptRoot 'prepare-windows.ps1'
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ShortcutMarker = 'Adal (Install-Adal.ps1)'
$MinNode = [version]'22.12.0'
$MinFreeGb = 3
# Same address format as acceptance\classroom\Start-Student.ps1.
$ServerPattern = '^(?:[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:]+\]):([0-9]{1,5})$'

function Write-Section([string]$Text) {
    Write-Host ''
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Line([string]$Status, [string]$Text) {
    $color = switch ($Status) { 'OK' { 'Green' } 'НЕТ' { 'Red' } 'ВНИМАНИЕ' { 'Yellow' } default { 'Gray' } }
    Write-Host ('  {0,-11} {1}' -f "[$Status]", $Text) -ForegroundColor $color
}

function Write-Detail([string]$Text) { Write-Host "              $Text" }

function Invoke-Probe([string]$Exe, [string[]]$Arguments) {
    # Short read-only probe: stdout as text, stderr discarded, never a terminating error.
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @Arguments 2>$null
        $code = $LASTEXITCODE
    } catch {
        return [pscustomobject]@{ Code = -1; Text = '' }
    }
    [pscustomobject]@{ Code = $code; Text = ((@($output) | ForEach-Object { [string]$_ }) -join "`n").Trim() }
}

function Invoke-Visible([string]$Exe, [string[]]$Arguments, [hashtable]$Environment = @{}) {
    # Runs a child process with visible output; variables are set only for it and restored afterwards.
    $ErrorActionPreference = 'Continue'
    $saved = @{}
    foreach ($key in $Environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], 'Process')
    }
    try {
        & $Exe @Arguments | Out-Host
        return $LASTEXITCODE
    } catch {
        Write-Line 'НЕТ' "Не удалось запустить ${Exe}: $($_.Exception.Message)"
        return -1
    } finally {
        foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
    }
}

function Get-Application([string]$Name) {
    Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
}

function Find-Python312([string]$Uv) {
    # uv (it finds python.org and uv-managed Python), then py launcher, then python on PATH.
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($Uv) {
        $found = Invoke-Probe $Uv @('python', 'find', '3.12')
        if ($found.Code -eq 0 -and $found.Text) { $candidates.Add(($found.Text -split "`n")[-1].Trim()) }
    }
    $py = Get-Application 'py.exe'
    if ($py) {
        $found = Invoke-Probe $py.Source @('-3.12', '-I', '-c', 'import sys; print(sys.executable)')
        if ($found.Code -eq 0 -and $found.Text) { $candidates.Add(($found.Text -split "`n")[-1].Trim()) }
    }
    $python = Get-Application 'python.exe'
    if ($python) { $candidates.Add($python.Source) }
    $seen = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $probe = Invoke-Probe $candidate @('-I', '-B', '-c',
            "import platform, struct, sys; print('%d.%d %s %d' % (sys.version_info[0], sys.version_info[1], platform.machine(), struct.calcsize('P') * 8))")
        if ($probe.Code -ne 0) { continue }
        if ($probe.Text -eq '3.12 AMD64 64') { return [pscustomobject]@{ Path = $candidate; Seen = $seen } }
        $seen.Add("$($probe.Text) — $candidate")
    }
    [pscustomobject]@{ Path = $null; Seen = $seen }
}

function Test-AdalPrerequisites {
    $missing = New-Object System.Collections.Generic.List[string]
    $result = [pscustomobject]@{ Ok = $false; Python = $null }

    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    $os = [Environment]::OSVersion
    if ($os.Platform -ne [PlatformID]::Win32NT -or $os.Version.Major -lt 10) {
        Write-Line 'НЕТ' "Нужна Windows 10/11 x64; обнаружено: $($os.VersionString)."
        $missing.Add('windows')
    } elseif ($arch -ne 'AMD64') {
        Write-Line 'НЕТ' "Нужна Windows x64 (AMD64); архитектура этого ПК: $arch."
        $missing.Add('windows')
    } else {
        Write-Line 'OK' "Windows x64, версия $($os.Version), PowerShell $($PSVersionTable.PSVersion)"
    }

    $uv = Get-Application 'uv.exe'
    if ($uv) {
        Write-Line 'OK' "uv: $((Invoke-Probe $uv.Source @('--version')).Text) — $($uv.Source)"
    } else {
        Write-Line 'НЕТ' 'uv не найден в PATH.'
        $missing.Add('uv')
    }

    $python = Find-Python312 $(if ($uv) { $uv.Source } else { $null })
    if ($python.Path) {
        Write-Line 'OK' "Python 3.12 x64: $($python.Path)"
        $result.Python = $python.Path
    } else {
        Write-Line 'НЕТ' 'Python 3.12 x64 не найден.'
        foreach ($other in $python.Seen) { Write-Detail "не подходит: $other" }
        $missing.Add('python')
    }

    $node = Get-Application 'node.exe'
    $npm = Get-Application 'npm.cmd'
    $nodeText = if ($node) { (Invoke-Probe $node.Source @('--version')).Text } else { '' }
    if (-not $node) {
        Write-Line 'НЕТ' 'Node.js не найден в PATH.'
        $missing.Add('node')
    } elseif ($nodeText -notmatch '^v(\d+)\.(\d+)\.(\d+)' -or [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])" -lt $MinNode) {
        Write-Line 'НЕТ' "Node.js $nodeText — нужна версия не ниже $MinNode."
        $missing.Add('node')
    } elseif (-not $npm) {
        Write-Line 'НЕТ' "Node.js $nodeText найден, но npm.cmd отсутствует."
        $missing.Add('node')
    } else {
        Write-Line 'OK' "Node.js $nodeText и npm — $($node.Source)"
    }

    $git = Get-Application 'git.exe'
    if ($git) {
        Write-Line 'OK' "$((Invoke-Probe $git.Source @('--version')).Text) — $($git.Source)"
    } else {
        Write-Line 'НЕТ' 'Git не найден в PATH.'
        $missing.Add('git')
    }

    if ($missing.Count) {
        Write-Host ''
        Write-Host 'Установите недостающее (скрипт сам системные программы не ставит):' -ForegroundColor Yellow
        foreach ($key in ($missing | Select-Object -Unique)) {
            switch ($key) {
                'windows' { Write-Detail 'Adal подготовлен только для Windows 10/11 x64; на этом ПК установка невозможна.' }
                'python' {
                    Write-Detail 'Python 3.12 x64: https://www.python.org/downloads/release/python-31210/'
                    Write-Detail '  установщик: https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
                    Write-Detail '  или: winget install -e --id Python.Python.3.12   (или, если uv уже есть: uv python install 3.12)'
                }
                'uv' {
                    Write-Detail 'uv: https://docs.astral.sh/uv/getting-started/installation/'
                    Write-Detail '  или: winget install -e --id astral-sh.uv'
                }
                'node' {
                    Write-Detail "Node.js LTS x64 (не ниже $MinNode, npm входит в установщик): https://nodejs.org/en/download"
                    Write-Detail '  или: winget install -e --id OpenJS.NodeJS.LTS'
                }
                'git' {
                    Write-Detail 'Git для Windows: https://git-scm.com/downloads/win'
                    Write-Detail '  или: winget install -e --id Git.Git'
                }
            }
        }
        Write-Detail 'После установки закройте и снова откройте PowerShell (обновится PATH) и повторите команду.'
    }
    $result.Ok = ($missing.Count -eq 0)
    $result
}

function Test-AdalProject {
    $ok = $true
    $required = @('Start-Adal.ps1', 'packaging\prepare-windows.ps1', 'packaging\preflight.py', 'pyproject.toml', 'uv.lock',
        'desktop\package.json', 'desktop\package-lock.json', 'desktop\native\qorgau_guard.py', 'class-panel\index.html',
        'backend\proctor\phone\models.manifest.json', 'backend\proctor\attention\models.manifest.json')
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $relative) -PathType Leaf)) {
            Write-Line 'НЕТ' "В копии проекта нет $relative. Склонируйте репозиторий полностью."
            $ok = $false
        }
    }
    if ($ok) { Write-Line 'OK' "Копия проекта: $ProjectRoot" }

    $git = Get-Application 'git.exe'
    if ($git -and (Test-Path -LiteralPath (Join-Path $ProjectRoot '..\.git'))) {
        $sha = Invoke-Probe $git.Source @('-C', $ProjectRoot, 'rev-parse', 'HEAD')
        $branch = Invoke-Probe $git.Source @('-C', $ProjectRoot, 'rev-parse', '--abbrev-ref', 'HEAD')
        $changes = Invoke-Probe $git.Source @('--no-optional-locks', '-C', $ProjectRoot, 'status', '--porcelain', '--', '.')
        $note = if ($changes.Text) { '; есть локальные изменения в proctoring/' } else { '' }
        Write-Line 'OK' "Git: ветка $($branch.Text), SHA $($sha.Text)$note"
    } else {
        Write-Line 'ВНИМАНИЕ' 'Это не git-клон: версию (SHA) проверить нельзя. Рекомендуется git clone.'
    }

    if ($ProjectRoot -match '[^\x00-\x7F]') {
        Write-Line 'ВНИМАНИЕ' 'В пути проекта есть не-ASCII символы. Работа MediaPipe с такими путями не проверялась; рекомендуется, например, C:\Adal.'
    }
    try {
        $drive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($ProjectRoot))
        $freeGb = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
        if ($freeGb -lt $MinFreeGb) { Write-Line 'ВНИМАНИЕ' "Свободно на диске $($drive.Name) $freeGb ГБ; для подготовки нужно около $MinFreeGb ГБ." }
        else { Write-Line 'OK' "Свободно на диске $($drive.Name) $freeGb ГБ" }
    } catch { Write-Line 'ВНИМАНИЕ' 'Не удалось узнать свободное место на диске.' }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Line 'ВНИМАНИЕ' "PowerShell запущен от администратора ($($identity.Name)): ярлыки появятся у этой учётной записи. Права администратора не нужны."
    } else {
        Write-Line 'OK' "Пользователь: $($identity.Name) (без прав администратора; ярлыки — для него)"
    }
    $ok
}

function Get-AdalModelsDir {
    if ($env:QORGAU_MODELS_DIR) { return $env:QORGAU_MODELS_DIR }
    Join-Path $ProjectRoot 'models'
}

function Get-AdalState {
    $items = New-Object System.Collections.Generic.List[object]
    $items.Add([pscustomobject]@{ Name = 'Python-окружение proctoring\.venv'; Path = $VenvPython })
    $items.Add([pscustomobject]@{ Name = 'Electron (npm)'; Path = Join-Path $ProjectRoot 'desktop\node_modules\electron\dist\electron.exe' })
    foreach ($relative in @('dist\main\main.cjs', 'dist\preload\preload.cjs', 'dist\renderer\index.html')) {
        $items.Add([pscustomobject]@{ Name = "Сборка desktop\$relative"; Path = Join-Path $ProjectRoot "desktop\$relative" })
    }
    foreach ($module in @('phone', 'attention')) {
        $manifestPath = Join-Path $ProjectRoot "backend\proctor\$module\models.manifest.json"
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $file = Join-Path (Get-AdalModelsDir) ($manifest.file -replace '/', '\')
        $items.Add([pscustomobject]@{ Name = "Модель $($manifest.model_id) ($([math]::Round($manifest.size_bytes / 1MB, 1)) МБ)"; Path = $file })
    }
    foreach ($item in $items) {
        $item | Add-Member -NotePropertyName Present -NotePropertyValue (Test-Path -LiteralPath $item.Path -PathType Leaf)
    }
    $items
}

function Invoke-AdalVerification {
    # Existing read-only checks only: model tools and the unified launcher in -CheckOnly mode.
    $self = (Get-Process -Id $PID).Path
    $pythonEnvironment = @{
        PYTHONPATH = (@($ProjectRoot, (Join-Path $ProjectRoot 'backend'), (Join-Path $ProjectRoot 'contracts\python')) -join ';')
        PYTHONDONTWRITEBYTECODE = '1'
        PYTHONIOENCODING = 'utf-8'
    }
    $launcher = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $StartAdal)
    $checks = @(
        @{ Name = 'Модель телефона YOLO11n: размер, SHA256, загрузка (proctor.phone.prepare --check)'; Exe = $VenvPython
           Arguments = @('-B', '-m', 'proctor.phone.prepare', '--check'); Environment = $pythonEnvironment },
        @{ Name = 'Модель лица FaceLandmarker: размер, SHA256 (proctor.attention.model_tool verify)'; Exe = $VenvPython
           Arguments = @('-B', '-m', 'proctor.attention.model_tool', 'verify'); Environment = $pythonEnvironment },
        @{ Name = 'Преподаватель: Start-Adal.ps1 -Role Teacher -CheckOnly'; Exe = $self
           Arguments = ($launcher + @('-Role', 'Teacher', '-CheckOnly')); Environment = @{ GIT_OPTIONAL_LOCKS = '0' } },
        @{ Name = 'Студент: Start-Adal.ps1 -Role Standalone -Enforce -CheckOnly'; Exe = $self
           Arguments = ($launcher + @('-Role', 'Standalone', '-Enforce', '-CheckOnly')); Environment = @{ GIT_OPTIONAL_LOCKS = '0' } }
    )
    # Known launcher limitation: in a UTF-8 console (chcp 65001) .NET adds a BOM to the launcher's stdin
    # payload and its Python probe rejects it. A shortcut opens a new console with the system OEM code page,
    # so the checks run with that code page; the console setting is restored afterwards.
    $savedInput = $null
    $inputCodePage = 0
    try { $inputCodePage = [Console]::InputEncoding.CodePage } catch { }
    $oemCodePage = 0
    try { $oemCodePage = [int](Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage' -Name OEMCP).OEMCP } catch { }
    if ($inputCodePage -eq 65001 -and $oemCodePage -gt 0 -and $oemCodePage -ne 65001) {
        try {
            $savedInput = [Console]::InputEncoding
            [Console]::InputEncoding = [Text.Encoding]::GetEncoding($oemCodePage)
            Write-Line 'ИНФО' "Консоль в UTF-8 (65001); проверки запуска идут с системной кодовой страницей $oemCodePage, как в окне ярлыка."
        } catch { $savedInput = $null }
    } elseif ($inputCodePage -eq 65001) {
        Write-Line 'ВНИМАНИЕ' 'Консоль Windows работает в UTF-8 (65001). Известное ограничение: запускатель Adal в такой консоли может не пройти проверку Python.'
    }
    $allOk = $true
    try {
        foreach ($check in $checks) {
            Write-Host "  -> $($check.Name)"
            $code = Invoke-Visible $check.Exe $check.Arguments $check.Environment
            if ($code -eq 0) { Write-Line 'OK' $check.Name }
            else { Write-Line 'НЕТ' "$($check.Name): код $code"; $allOk = $false }
        }
    } finally {
        if ($savedInput) { try { [Console]::InputEncoding = $savedInput } catch { } }
    }
    $allOk
}

function Show-RemoteAccessCheck([string]$Python) {
    # Existing read-only helper mode: lists known remote-access processes and the RDP flag.
    $helper = Join-Path $ProjectRoot 'desktop\native\qorgau_guard.py'
    if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        Write-Line 'ВНИМАНИЕ' 'Python не найден — программы удалённого доступа не проверялись.'
        return
    }
    $probe = Invoke-Probe $Python @('-B', '-E', $helper, '--environment-check')
    $snapshot = $null
    try { $snapshot = $probe.Text | ConvertFrom-Json } catch { $snapshot = $null }
    if ($probe.Code -ne 0 -or $null -eq $snapshot -or $snapshot.type -ne 'environment') {
        Write-Line 'ВНИМАНИЕ' 'Проверка программ удалённого доступа не удалась (это не значит, что их нет).'
        return
    }
    $names = @($snapshot.processes | Where-Object { $_ })
    if ($snapshot.remote_session) { $names += 'RDP-сеанс' }
    if ($names.Count) {
        Write-Line 'ВНИМАНИЕ' ('Сейчас работают: ' + ($names -join ', ') + '. Пока они запущены, Adal не начнёт экзамен.')
        Write-Detail 'Перед экзаменом закройте их (у RustDesk и подобных — также службу). Adal сам ничего не закрывает.'
    } else {
        Write-Line 'OK' 'Известные Adal программы удалённого доступа не запущены; это не RDP-сеанс.'
    }
}

function Get-AdalShortcutDirectories {
    # Owned = this script created the folder and may remove it when it is empty.
    @(
        [pscustomobject]@{ Path = Join-Path ([Environment]::GetFolderPath('Programs')) 'Adal'; Owned = $true },
        [pscustomobject]@{ Path = [Environment]::GetFolderPath('Desktop'); Owned = $false }
    )
}

function Get-AdalShortcutSpecs([string]$Script, [string]$ClassServer) {
    $quoted = "'" + $Script.Replace("'", "''") + "'"
    $close = "[void](Read-Host 'Нажмите Enter, чтобы закрыть окно')"
    if ($ClassServer) { $askServer = "`$s = '$ClassServer'" }
    else { $askServer = "`$s = (@(Read-Host 'Адрес ПК преподавателя, например 192.168.1.10:8765') -join '').Trim()" }
    # -join always yields a string (Read-Host may return nothing); no double quotes inside the .lnk argument.
    @(
        [pscustomobject]@{
            Name = 'Adal — студент'
            Command = "& { $askServer; `$c = (@(Read-Host 'Код класса (6 цифр)') -join '').Trim(); & $quoted -Role Student -Server `$s -JoinCode `$c -Enforce }; $close"
            Description = "${ShortcutMarker}: студент — подключение к классу, экзамен с -Enforce"
        },
        [pscustomobject]@{
            Name = 'Adal — преподаватель (класс)'
            Command = "& $quoted -Role Teacher -Lan; $close"
            Description = "${ShortcutMarker}: сервер класса и панель преподавателя (-Role Teacher -Lan)"
        },
        [pscustomobject]@{
            Name = 'Adal — проверка (CheckOnly)'
            Command = "& $quoted -Role Teacher -CheckOnly; & $quoted -Role Standalone -Enforce -CheckOnly; $close"
            Description = "${ShortcutMarker}: проверка готовности без окна, камеры, сети и hooks"
        }
    )
}

function Get-ShortcutOwner([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return 'absent' }
    # CreateShortcut on an existing file only loads it; nothing is written without Save().
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
    if (([string]$link.Description).StartsWith($ShortcutMarker)) { 'ours' } else { 'foreign' }
}

function Set-AdalShortcuts($Directories, [string]$ClassServer, [switch]$DryRun) {
    $target = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $ok = $true
    foreach ($directory in $Directories) {
        foreach ($spec in (Get-AdalShortcutSpecs $StartAdal $ClassServer)) {
            $path = Join-Path $directory.Path ($spec.Name + '.lnk')
            $owner = Get-ShortcutOwner $path
            if ($owner -eq 'foreign') {
                Write-Line 'ВНИМАНИЕ' "$path уже есть и создан не этим скриптом — не изменяю."
                continue
            }
            if ($DryRun) {
                $verb = if ($owner -eq 'ours') { 'будет обновлён' } else { 'будет создан' }
                Write-Line 'ПЛАН' "ярлык $verb`: $path"
                continue
            }
            try {
                [void][IO.Directory]::CreateDirectory($directory.Path)
                $link = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
                $link.TargetPath = $target
                $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -Command `"$($spec.Command)`""
                $link.WorkingDirectory = $ProjectRoot
                $link.Description = $spec.Description
                $link.WindowStyle = 1
                $link.Save()
                $verb = if ($owner -eq 'ours') { 'обновлён' } else { 'создан' }
                Write-Line 'OK' "ярлык ${verb}: $path"
            } catch {
                Write-Line 'НЕТ' "не удалось создать ${path}: $($_.Exception.Message)"
                $ok = $false
            }
        }
    }
    $ok
}

function Remove-AdalShortcuts($Directories, [switch]$DryRun) {
    $found = 0
    foreach ($directory in $Directories) {
        foreach ($spec in (Get-AdalShortcutSpecs $StartAdal '')) {
            $path = Join-Path $directory.Path ($spec.Name + '.lnk')
            $owner = Get-ShortcutOwner $path
            if ($owner -eq 'absent') { continue }
            if ($owner -eq 'foreign') { Write-Line 'ВНИМАНИЕ' "$path создан не этим скриптом — оставлен."; continue }
            $found++
            if ($DryRun) { Write-Line 'ПЛАН' "будет удалён: $path"; continue }
            Remove-Item -LiteralPath $path -Force
            Write-Line 'OK' "удалён: $path"
        }
        if ($directory.Owned -and (Test-Path -LiteralPath $directory.Path -PathType Container) -and
            -not (Get-ChildItem -LiteralPath $directory.Path -Force | Select-Object -First 1)) {
            if ($DryRun) { Write-Line 'ПЛАН' "будет удалена пустая папка: $($directory.Path)" }
            else { Remove-Item -LiteralPath $directory.Path -Force; Write-Line 'OK' "удалена пустая папка: $($directory.Path)" }
        }
    }
    if (-not $found) { Write-Line 'OK' 'Ярлыков, созданных Install-Adal.ps1, не найдено.' }
}

function Assert-AdalNotRunning {
    # npm ci and uv sync replace files that a running Adal keeps open.
    $prefix = $ProjectRoot.TrimEnd('\') + '\'
    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $path = $null
        try { $path = $_.Path } catch { }
        $path -and $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($running.Count) {
        $list = ($running | ForEach-Object { "$($_.ProcessName) (PID $($_.Id))" }) -join ', '
        throw "Сначала закройте Adal и консоли запуска этой копии: $list."
    }
}

# Dot-sourcing (. .\Install-Adal.ps1) only defines the functions above.
if ($MyInvocation.InvocationName -eq '.') { return }

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false) } catch { }
try {
    $mode = 'установка'
    if ($CheckOnly) { $mode = 'пробный запуск: ничего не меняется' }
    if ($Uninstall) { $mode = 'удаление ярлыков' + $(if ($CheckOnly) { ', пробный запуск' } else { '' }) }
    Write-Host "Adal — Install-Adal.ps1 ($mode)"
    Write-Host "Проект: $ProjectRoot"

    if ($Uninstall) {
        if ($Force -or $Server -or $NoShortcuts) { Write-Line 'ВНИМАНИЕ' '-Force, -Server и -NoShortcuts при удалении не используются.' }
        Write-Section 'Ярлыки, созданные Install-Adal.ps1'
        Remove-AdalShortcuts (Get-AdalShortcutDirectories) -DryRun:$CheckOnly
        Write-Detail 'Окружение (.venv, desktop\node_modules, desktop\dist), модели и данные экзаменов'
        Write-Detail '(%LOCALAPPDATA%\QorgauExam, %LOCALAPPDATA%\QorgauClassroom) не удалялись.'
        exit 0
    }

    if ($Server -and ($Server -notmatch $ServerPattern -or [int]$Matches[1] -lt 1 -or [int]$Matches[1] -gt 65535)) {
        Write-Host 'Не готово: -Server указывается как адрес:порт, например 192.168.1.10:8765 (без http://).' -ForegroundColor Red
        exit 2
    }

    Write-Section 'Требования (скрипт их только проверяет)'
    $prerequisites = Test-AdalPrerequisites
    Write-Section 'Копия проекта и этот ПК'
    $projectOk = Test-AdalProject
    if (-not $prerequisites.Ok -or -not $projectOk) {
        Write-Host ''
        Write-Host 'Не готово: выполните требования выше и повторите команду. Ничего не изменено.' -ForegroundColor Red
        exit 2
    }

    Write-Section 'Состояние подготовки'
    $state = @(Get-AdalState)
    foreach ($item in $state) {
        if ($item.Present) { Write-Line 'OK' "$($item.Name) — есть" }
        else { Write-Line 'ПЛАН' "$($item.Name) — нет, появится после подготовки" }
    }
    $allPresent = -not ($state | Where-Object { -not $_.Present })

    if ($CheckOnly) {
        $ready = $false
        if ($allPresent) {
            Write-Section 'Проверка готовности (только чтение: без окна, камеры, сети и hooks)'
            $ready = Invoke-AdalVerification
        }
        Write-Section 'План установки'
        if ($ready -and -not $Force) {
            Write-Line 'ПЛАН' 'Подготовка не потребуется: всё уже готово (повторить принудительно: -Force).'
        } else {
            Write-Line 'ПЛАН' 'Будет запущен packaging\prepare-windows.ps1 -FetchModels (нужен интернет один раз):'
            Write-Detail 'uv sync --frozen (Python-пакеты), npm ci + Electron, typecheck и сборка desktop,'
            Write-Detail 'загрузка моделей с проверкой SHA256, затем проверка моделей и Start-Adal.ps1 -CheckOnly.'
        }
        if ($NoShortcuts) { Write-Line 'ПЛАН' 'Ярлыки не будут созданы (-NoShortcuts).' }
        else { [void](Set-AdalShortcuts (Get-AdalShortcutDirectories) $Server -DryRun) }
        Write-Section 'Программы удалённого доступа'
        $python = if (Test-Path -LiteralPath $VenvPython -PathType Leaf) { $VenvPython } else { $prerequisites.Python }
        Show-RemoteAccessCheck $python
        Write-Host ''
        Write-Host 'Пробный запуск завершён: требования выполнены. Ничего не скачано, не собрано и не изменено.' -ForegroundColor Green
        exit 0
    }

    Write-Section 'Подготовка'
    $ready = $false
    if ($allPresent -and -not $Force) {
        Write-Host 'Все файлы на месте — проверяю, нужна ли подготовка (ошибки ниже означают, что нужна).'
        $ready = Invoke-AdalVerification
    }
    if ($ready) {
        Write-Line 'OK' 'Уже подготовлено; prepare-windows.ps1 не запускался (повторить: -Force).'
    } else {
        Assert-AdalNotRunning
        Write-Host 'Запуск packaging\prepare-windows.ps1 -FetchModels. Нужен интернет; первый раз — около 5–15 минут.'
        $self = (Get-Process -Id $PID).Path
        # UV_PYTHON_DOWNLOADS=never: uv uses the Python found above and never installs one by itself.
        $code = Invoke-Visible $self @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PrepareScript, '-FetchModels') @{ UV_PYTHON_DOWNLOADS = 'never' }
        if ($code -ne 0) {
            Write-Line 'НЕТ' "Подготовка завершилась с кодом $code. Причина — в сообщениях выше; проверьте интернет и повторите команду."
            exit 1
        }
        Write-Line 'OK' 'prepare-windows.ps1 завершён.'
        Write-Section 'Проверка моделей и запуска (без окна, камеры, сети и hooks)'
        if (-not (Invoke-AdalVerification)) {
            Write-Line 'НЕТ' 'Проверка не пройдена: см. сообщения выше. Повторите установку с -Force.'
            exit 1
        }
    }

    Write-Section 'Ярлыки'
    if ($NoShortcuts) {
        Write-Line 'OK' 'Пропущено (-NoShortcuts).'
    } elseif (-not (Set-AdalShortcuts (Get-AdalShortcutDirectories) $Server)) {
        exit 1
    }

    Write-Section 'Программы удалённого доступа'
    Show-RemoteAccessCheck $VenvPython

    Write-Section 'Готово'
    if (-not $NoShortcuts) {
        Write-Host '  «Adal — преподаватель (класс)»: сервер класса; панель http://127.0.0.1:8765/, PIN — в консоли.'
        Write-Host '  «Adal — студент»: спросит адрес ПК преподавателя и код класса; экзамен с -Enforce.'
        Write-Host '  «Adal — проверка (CheckOnly)»: проверка готовности без окна, камеры, сети и hooks.'
    }
    Write-Host '  Вручную из папки proctoring: .\Start-Adal.ps1 -Role Teacher -Lan'
    Write-Host '                               .\Start-Adal.ps1 -Role Student -Server <адрес:порт> -JoinCode <код> -Enforce'
    Write-Host '  Аварийный выход из экзамена: Ctrl+Alt+Shift+F12.' -ForegroundColor Yellow
    Write-Host '  Проверки выше не подтверждают камеру, микрофон, сеть класса и блокировку Windows на этом ПК — их проверяют вручную.'
    Write-Host '  Инструкция: proctoring\packaging\INSTALL_RU.md'
    exit 0
} catch {
    Write-Host "Не готово: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
