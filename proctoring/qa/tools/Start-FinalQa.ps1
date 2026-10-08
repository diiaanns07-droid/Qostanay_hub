#Requires -Version 5.1
# QA-only wrapper: product files and Windows settings are untouched.
[CmdletBinding()]
param(
    [string]$CandidateRoot = 'C:\Qostanay_hub-codex-proctoring-prompts\verify-candidate',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$candidate = (Resolve-Path -LiteralPath $CandidateRoot).Path
$root = Join-Path $candidate 'proctoring'
$desktop = Join-Path $root 'desktop'
$python = Join-Path $root '.venv\Scripts\python.exe'
$adapter = Join-Path $PSScriptRoot 'final_guard_limit.py'
$realHelper = Join-Path $desktop 'native\qorgau_guard.py'
$electron = Join-Path $desktop 'node_modules\electron\cli.js'
foreach ($path in @($python, $adapter, $realHelper, $electron)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing existing file: $path" }
}
$changes = @{
    ELECTRON_RUN_AS_NODE = $null; NODE_OPTIONS = $null
    QORGAU_SHELL_DEV_RENDERER_URL = $null
    QORGAU_PROCTORING_ROOT = $root; QORGAU_PYTHON = $python
    PYTHONPATH = (Join-Path $root 'backend') + ';' + (Join-Path $root 'contracts\python') + ';' + $root
    QORGAU_MODELS_DIR = (Join-Path $env:LOCALAPPDATA 'QorgauExam\models')
    QORGAU_REPLAY_DIR = (Join-Path $env:LOCALAPPDATA 'QorgauExam\replay')
    QORGAU_SHELL_DEMO_OPERATOR = '1'; QORGAU_SHELL_NATIVE_ENFORCE = '1'
    QORGAU_SHELL_NATIVE_HELPER = $adapter; QORGAU_QA_REAL_HELPER = $realHelper
    QORGAU_SHELL_EMERGENCY_ACCELERATOR = 'CommandOrControl+Alt+Shift+F12'
}
Write-Host '============================================================' -ForegroundColor Yellow
Write-Host 'Adal FINAL QA: EXIT Ctrl+Alt+Shift+F12' -ForegroundColor Yellow
Write-Host 'Native enforce hard limit: --max-minutes 2 (QA adapter)' -ForegroundColor Yellow
Write-Host 'Fallback exit: Ctrl+Alt+Del -> Task Manager' -ForegroundColor Yellow
Write-Host 'Close this Adal instance immediately after the check.' -ForegroundColor Yellow
Write-Host '============================================================' -ForegroundColor Yellow
if ($CheckOnly) { Write-Host 'CHECK OK: no application or hook started'; return }
$previous = @{}
foreach ($key in $changes.Keys) { $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
Push-Location -LiteralPath $desktop
try {
    foreach ($key in $changes.Keys) { [Environment]::SetEnvironmentVariable($key, $changes[$key], 'Process') }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Candidate build failed; Electron not started' }
    & node.exe $electron '.'
    if ($LASTEXITCODE -ne 0) { throw "Electron exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
    foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') }
}
