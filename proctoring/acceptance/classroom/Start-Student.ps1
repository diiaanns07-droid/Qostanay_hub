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
    [switch]$CheckOnly,
    [switch]$BackendOnly,
    [ValidateRange(1, 120)][int]$ReadyTimeoutSeconds = 60,
    [ValidateRange(0, 3600)][int]$StopAfterSeconds = 0
)

# Keep parameters before dot-sourcing the shared functions (the teacher script has its own defaults).
$student = @{ Server=$Server; JoinCode=$JoinCode; Label=$Label; Python=$Python; DataDir=$DataDir;
    CheckOnly=[bool]$CheckOnly; BackendOnly=[bool]$BackendOnly; Timeout=$ReadyTimeoutSeconds; StopAfter=$StopAfterSeconds }
. (Join-Path $PSScriptRoot 'Start-Teacher.ps1') -Library
$ErrorActionPreference = 'Stop'
$child = $null
try {
    if ($student.Server -notmatch '^(?:[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:]+\]):([0-9]{1,5})$' -or
        [int]$Matches[1] -lt 1 -or [int]$Matches[1] -gt 65535) {
        throw 'Укажите -Server в формате адрес:порт, например 192.168.1.10:8765 (без http://).'
    }
    if ($student.JoinCode -notmatch '^[0-9]{6}$') { throw 'Укажите -JoinCode: шесть цифр из панели преподавателя.' }
    if (-not $student.Label -or $student.Label.Trim().Length -lt 1 -or $student.Label.Trim().Length -gt 64 -or $student.Label -match '[\x00-\x1f]') {
        throw 'Укажите -Label: имя ПК/студента от 1 до 64 символов.'
    }
    $root = Get-QorgauRoot
    $pythonExe = Get-QorgauPython $student.Python $root
    $environment = Get-QorgauEnvironment $root
    $environment.QORGAU_PYTHON = $pythonExe
    $environment.QORGAU_CLASS_SERVER = $student.Server
    $environment.QORGAU_CLASS_CODE = $student.JoinCode
    $environment.QORGAU_CLASS_LABEL = $student.Label.Trim()
    $environment.QORGAU_HOST = '127.0.0.1'
    $environment.QORGAU_PORT = '0'
    $environment.QORGAU_SHELL_NATIVE_ENFORCE = '0'
    $environment.ELECTRON_RUN_AS_NODE = $null
    $environment.NODE_OPTIONS = $null
    $environment.QORGAU_SHELL_DEV_RENDERER_URL = $null
    if ($student.DataDir) { $environment.QORGAU_DATA_DIR = [IO.Path]::GetFullPath($student.DataDir) }
    Test-QorgauPython $pythonExe $root $environment -Student
    $desktop = Join-Path $root 'desktop'
    $electron = Join-Path $desktop 'node_modules\electron\dist\electron.exe'
    if (-not $student.BackendOnly) {
        if (-not (Test-Path -LiteralPath $electron -PathType Leaf)) {
            throw "Electron отсутствует в этой копии. В '$desktop' выполните npm ci; затем npm run build. Если binary не скачан: node node_modules/electron/install.js."
        }
        $artifacts = @('dist\main\main.cjs', 'dist\preload\preload.cjs', 'dist\renderer\index.html') |
            ForEach-Object { Join-Path $desktop $_ }
        foreach ($artifact in $artifacts) {
            if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Сборка студента отсутствует. В '$desktop' выполните npm run build." }
        }
        $oldestBuild = ($artifacts | Get-Item | Sort-Object LastWriteTimeUtc | Select-Object -First 1).LastWriteTimeUtc
        $inputs = @('main\src', 'preload\src', 'renderer\src', '..\contracts\ts') |
            ForEach-Object { Get-ChildItem -LiteralPath (Join-Path $desktop $_) -File -Recurse }
        $inputs += @('package.json', 'package-lock.json', 'vite.config.ts', 'renderer\index.html', 'tsconfig.json',
            'tsconfig.main.json', 'tsconfig.renderer.json', 'scripts\build-electron.mjs') |
            ForEach-Object { Get-Item -LiteralPath (Join-Path $desktop $_) }
        if ($inputs | Where-Object { $_.LastWriteTimeUtc -gt $oldestBuild } | Select-Object -First 1) {
            throw "Исходники новее сборки. В '$desktop' выполните npm run build."
        }
    }
    Write-Host "Исходники студента: $root"
    Write-Host "Сервер класса: $($student.Server); студент: $($student.Label). Код входа скрыт."
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
        if ($student.StopAfter) { throw '-StopAfterSeconds применяется только с -BackendOnly. Electron завершайте через окно приложения.' }
        Write-Host 'Открывается Adal. Подключение к классу смотрите в блоке «Класс». Нативное перехватывание клавиш выключено.'
        Write-Host 'Завершить: закройте приложение; аварийный выход из экзамена: Ctrl+Alt+Shift+F12.'
        $child = Start-QorgauProcess $pythonExe $root $environment @{kind='desktop'; electron=$electron; desktop=$desktop}
        Wait-QorgauProcess $child 'desktop' $student.Timeout 0 $false @($student.JoinCode)
    }
} catch {
    Write-Host "Не готово: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally { Stop-QorgauProcess $child }
