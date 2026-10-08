#Requires -Version 5.1
<#
.SYNOPSIS
Запуск текущей сборки Adal и C2 uplink. -CheckOnly не запускает приложение.
.EXAMPLE
.\Start-Student.ps1 -Server '192.168.1.10:8765' -JoinCode '123456' -Label 'PC2'
#>
[CmdletBinding()]
param(
    [string]$Server,
    [string]$JoinCode,
    [string]$Label = $env:COMPUTERNAME,
    [string]$Python,
    [string]$DataDir,
    [string]$ModelsDir,
    [ValidateRange(0.5,5.0)][double]$PreviewFps = 0.5,
    [switch]$CheckOnly,
    [switch]$BackendOnly,
    [switch]$Standalone,
    [switch]$Enforce,
    [switch]$DemoOperator,
    [ValidateRange(1, 120)][int]$ReadyTimeoutSeconds = 60,
    [ValidateRange(0, 3600)][int]$StopAfterSeconds = 0
)

# Keep parameters before dot-sourcing the shared functions (the teacher script has its own defaults).
$student = @{ Server=$Server; JoinCode=$JoinCode; Label=$Label; Python=$Python; DataDir=$DataDir; ModelsDir=$ModelsDir; PreviewFps=$PreviewFps;
    CheckOnly=[bool]$CheckOnly; BackendOnly=[bool]$BackendOnly; Standalone=[bool]$Standalone;
    Enforce=[bool]$Enforce; DemoOperator=[bool]$DemoOperator; Timeout=$ReadyTimeoutSeconds; StopAfter=$StopAfterSeconds }
. (Join-Path $PSScriptRoot 'Start-Teacher.ps1') -Library
$ErrorActionPreference = 'Stop'
$child = $null
try {
    if ($student.BackendOnly -and $student.Enforce) { throw '-BackendOnly и -Enforce несовместимы: системная защита требует Electron.' }
    if ($student.Standalone -and ($student.Server -or $student.JoinCode)) { throw '-Standalone не принимает -Server/-JoinCode; для класса выберите Student.' }
    if (-not $student.Standalone -and ($student.Server -notmatch '^(?:[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:]+\]):([0-9]{1,5})$' -or
        [int]$Matches[1] -lt 1 -or [int]$Matches[1] -gt 65535)) {
        throw 'Укажите -Server в формате адрес:порт, например 192.168.1.10:8765 (без http://).'
    }
    if (-not $student.Standalone -and $student.JoinCode -notmatch '^[0-9]{6}$') { throw 'Укажите -JoinCode: шесть цифр из панели преподавателя.' }
    if (-not $student.Standalone -and (-not $student.Label -or $student.Label.Trim().Length -lt 1 -or $student.Label.Trim().Length -gt 64 -or $student.Label -match '[\x00-\x1f]')) {
        throw 'Укажите -Label: имя ПК/студента от 1 до 64 символов.'
    }
    $root = Get-QorgauRoot
    $pythonExe = Get-QorgauPython $student.Python $root
    $environment = Get-QorgauEnvironment $root
    $environment.QORGAU_PYTHON = $pythonExe
    $environment.QORGAU_CLASS_SERVER = if ($student.Standalone) { $null } else { $student.Server }
    $environment.QORGAU_CLASS_CODE = if ($student.Standalone) { $null } else { $student.JoinCode }
    $environment.QORGAU_CLASS_LABEL = if ($student.Standalone) { $null } else { $student.Label.Trim() }
    $environment.QORGAU_CLASS_PREVIEW_FPS = if ($student.Standalone) { $null } else { $student.PreviewFps.ToString([Globalization.CultureInfo]::InvariantCulture) }
    $environment.QORGAU_HOST = '127.0.0.1'
    $environment.QORGAU_PORT = '0'
    $environment.QORGAU_SHELL_NATIVE_ENFORCE = if ($student.Enforce) { '1' } else { '0' }
    $environment.QORGAU_SHELL_NATIVE_HELPER = $null # current checkout's exe/Python helper, not an inherited path
    $environment.QORGAU_SHELL_SELFTEST = '1'
    $environment.QORGAU_SHELL_ALLOW_DEVTOOLS = '0'
    $environment.QORGAU_SHELL_DEMO_OPERATOR = if ($student.DemoOperator) { '1' } else { '0' }
    $environment.QORGAU_SHELL_EMERGENCY_ACCELERATOR = 'CommandOrControl+Alt+Shift+F12'
    $environment.ELECTRON_RUN_AS_NODE = $null
    $environment.NODE_OPTIONS = $null
    $environment.NODE_INSPECT_RESUME_ON_START = $null
    $environment.QORGAU_SHELL_DEV_RENDERER_URL = $null
    if ($student.DataDir) { $environment.QORGAU_DATA_DIR = [IO.Path]::GetFullPath($student.DataDir) }
    if ($student.ModelsDir) { $environment.QORGAU_MODELS_DIR = [IO.Path]::GetFullPath($student.ModelsDir) }
    Test-QorgauPython $pythonExe $root $environment -Student
    $desktop = Join-Path $root 'desktop'
    $electron = Join-Path $desktop 'node_modules\electron\dist\electron.exe'
    if (-not $student.BackendOnly) {
        if ($student.Enforce -and -not ((Test-Path -LiteralPath (Join-Path $desktop 'native\bin\qorgau-guard.exe') -PathType Leaf) -or
            (Test-Path -LiteralPath (Join-Path $desktop 'native\qorgau_guard.py') -PathType Leaf))) {
            throw 'Для -Enforce отсутствует нативный помощник этой копии. Подготовьте desktop/native; защита не подтверждена.'
        }
        if (-not (Test-Path -LiteralPath $electron -PathType Leaf)) {
            throw "Electron отсутствует в этой копии. В '$desktop' выполните npm ci; затем npm run build. Если binary не скачан: node node_modules/electron/install.js."
        }
        $artifacts = @('dist\main\main.cjs', 'dist\preload\preload.cjs', 'dist\renderer\index.html') |
            ForEach-Object { Join-Path $desktop $_ }
        foreach ($artifact in $artifacts) {
            if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Сборка студента отсутствует. В '$desktop' выполните npm run build." }
        }
        $oldestBuild = ($artifacts | Get-Item | Sort-Object LastWriteTimeUtc | Select-Object -First 1).LastWriteTimeUtc
        # Bundled imports also cross into shared lock contracts and the class-audio web modules.
        $inputs = @(@('main\src', 'preload\src', 'renderer\src', 'shared', '..\contracts\ts', '..\class-audio\web') |
            ForEach-Object { Get-ChildItem -LiteralPath (Join-Path $desktop $_) -File -Recurse })
        $inputs += @('package.json', 'package-lock.json', 'vite.config.ts', 'renderer\index.html', 'tsconfig.json',
            'tsconfig.main.json', 'tsconfig.renderer.json', 'scripts\build-electron.mjs') |
            ForEach-Object { Get-Item -LiteralPath (Join-Path $desktop $_) }
        if ($inputs | Where-Object { $_.LastWriteTimeUtc -gt $oldestBuild } | Select-Object -First 1) {
            throw "Исходники новее сборки. В '$desktop' выполните npm run build."
        }
        if ($student.Standalone) { Test-QorgauPython $pythonExe $root $environment -Student -DesktopAssets }
    }
    Write-Host "Исходники студента: $root"
    if (-not $student.Standalone) { Write-Host "Сервер класса: $($student.Server); студент: $($student.Label). Код входа скрыт." }
    if (-not $student.Standalone) { Write-Host "Превью для преподавателя: запрошено $($student.PreviewFps) кадр/с; фактическая частота зависит от камеры и лимита сервера." }
    $nativeMode = if ($student.Enforce) { 'ENFORCE запрошен явно; фактический blocked проверяется на этом ПК' } else { 'DRY-RUN: нативное подавление клавиш выключено' }
    Write-Host "Adal: $nativeMode."
    if ($student.CheckOnly) {
        $kind = if ($student.BackendOnly) { 'backend' } else { 'backend и Electron' }
        Write-Host "Проверка готова: $kind. Камера, микрофон, hooks, окно и сеть не запускались. Доступность PC1 и код не проверялись."
        exit 0
    }
    if ($student.BackendOnly) {
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $token = [Convert]::ToBase64String($bytes)
        Write-Host 'Диагностика backend: без окна и сессии экзамена. Остановить: Ctrl+C.'
        $child = Start-QorgauProcess $pythonExe $root $environment @{kind='backend'; module='proctor'; args=@('serve','--token-stdin','--port','0')}
        $child.Process.StandardInput.WriteLine($token)
        $child.Process.StandardInput.Flush()
        Wait-QorgauProcess $child 'backend' $student.Timeout $student.StopAfter $false @($token, $student.JoinCode)
    } else {
        Write-Host 'Открывается Adal. Проверки LIVE и готовность среды выполняются внутри приложения.'
        Write-Host 'Аварийное завершение Adal: Ctrl+Alt+Shift+F12. Удерживайте сочетание до выхода.'
        if ($student.StopAfter) { Write-Host "Пробный запуск: Adal автоматически завершится через $($student.StopAfter) секунд." }
        $child = Start-QorgauProcess $pythonExe $root $environment @{
            kind='desktop'; electron=$electron; desktop=$desktop
            emergency_watchdog=$true; enforce=$student.Enforce; max_duration_seconds=$student.StopAfter
        }
        Write-Host "ADAL_LAUNCHER_PID $($child.Process.Id)"
        Wait-QorgauProcess $child 'desktop' $student.Timeout 0 $false @($student.JoinCode)
    }
} catch {
    Write-Host "Не готово: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally { Stop-QorgauProcess $child }
