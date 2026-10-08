#Requires -Version 5.1
<#
.SYNOPSIS
Единый запуск Adal: Teacher (C1), Student (Electron+C2), Standalone (локальный Electron).
.EXAMPLE
.\Start-Adal.ps1 -Role Teacher -Lan
.EXAMPLE
.\Start-Adal.ps1 -Role Student -Server '192.168.1.10:8765' -JoinCode $code -Label PC2 -Enforce
#>
[CmdletBinding()]
param(
    [ValidateSet('Teacher','Student','Standalone')][string]$Role = 'Standalone',
    [string]$Python,
    [string]$Server,
    [string]$JoinCode,
    [string]$Label = $env:COMPUTERNAME,
    [string]$DataDir,
    [string]$ModelsDir,
    [ValidateRange(0.5,5.0)][double]$PreviewFps = 0.5,
    [switch]$Lan,
    [ValidateRange(0,65535)][int]$Port = 8765,
    [switch]$CheckOnly,
    [switch]$BackendOnly,
    [switch]$Enforce,
    [switch]$DemoOperator,
    [ValidatePattern('^[0-9a-fA-F]{40}$')][string]$ExpectedSha,
    [ValidateRange(1,120)][int]$ReadyTimeoutSeconds = 60,
    [ValidateRange(0,3600)][int]$StopAfterSeconds = 0
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
try {
    if ($Role -ne 'Student' -and $PSBoundParameters.ContainsKey('PreviewFps')) {
        throw '-PreviewFps относится только к Student.'
    }
    if ($BackendOnly -and $Enforce) { throw '-BackendOnly + -Enforce недопустимы: backend не включает системную защиту.' }
    if ($Role -eq 'Teacher' -and ($Enforce -or $BackendOnly -or $DemoOperator -or $Server -or $JoinCode -or $ModelsDir)) {
        throw 'Teacher запускает C1. -Enforce/-BackendOnly/-DemoOperator/-Server/-JoinCode/-ModelsDir относятся к студенту.'
    }
    if ($Role -ne 'Teacher' -and ($Lan -or $PSBoundParameters.ContainsKey('Port'))) {
        throw '-Lan/-Port относятся только к Teacher; студент получает -Server адрес:порт.'
    }
    if ($Role -eq 'Standalone' -and ($Server -or $JoinCode)) { throw 'Standalone не принимает -Server/-JoinCode. Для класса выберите -Role Student.' }
    $sha = $null
    $dirty = $null
    if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\.git'))) {
        $revision = & git -C $PSScriptRoot rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0) {
            $sha = ([string]$revision).Trim()
            $changes = & git -C $PSScriptRoot status --porcelain -- . 2>$null
            if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить состояние исходников Git.' }
            $dirty = [bool]$changes
        }
    }
    if ($ExpectedSha -and ($sha -ne $ExpectedSha -or $dirty)) {
        throw "Кандидат не совпадает: текущий SHA=$sha, ожидаемый=$ExpectedSha, изменённые файлы=$dirty. Сверьте копию и пересоберите её."
    }
    if ($sha) { Write-Host "Adal | SHA=$sha | изменённые файлы=$dirty | роль=$Role" }
    else { Write-Host 'Adal | SHA недоступен (Git отсутствует или это копия без .git). Актуальность сборки проверяется отдельно.' }
    $options = @{ Python=$Python; DataDir=$DataDir; CheckOnly=$CheckOnly;
        ReadyTimeoutSeconds=$ReadyTimeoutSeconds; StopAfterSeconds=$StopAfterSeconds }
    if ($Role -eq 'Teacher') {
        $options.Lan=$Lan; $options.Port=$Port
        & (Join-Path $PSScriptRoot 'acceptance\classroom\Start-Teacher.ps1') @options
    } else {
        $options.ModelsDir=$ModelsDir; $options.BackendOnly=$BackendOnly
        $options.Enforce=$Enforce; $options.DemoOperator=$DemoOperator
        if ($Role -eq 'Student') {
            $options.Server=$Server; $options.JoinCode=$JoinCode; $options.Label=$Label
            $options.PreviewFps=$PreviewFps
            & (Join-Path $PSScriptRoot 'acceptance\classroom\Start-Student.ps1') @options
        } else {
            & (Join-Path $PSScriptRoot 'packaging\launch-windows.ps1') @options
        }
    }
    exit $LASTEXITCODE
} catch {
    Write-Host "Не готово: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
