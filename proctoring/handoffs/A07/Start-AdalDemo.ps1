#Requires -Version 5.1
<#
.SYNOPSIS
Start the verified Adal desktop for a supervised demo. No packages are installed.
.EXAMPLE
.\Start-AdalDemo.ps1
.EXAMPLE
.\Start-AdalDemo.ps1 -Enforce
#>
[CmdletBinding()]
param(
    [string]$CandidateRoot,
    [switch]$Enforce,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'

if (-not $CandidateRoot) {
    $cursor = [IO.DirectoryInfo]$PSScriptRoot
    while ($cursor) {
        $possible = Join-Path $cursor.FullName 'verify-candidate'
        if (Test-Path -LiteralPath (Join-Path $possible 'proctoring\desktop\package.json') -PathType Leaf) {
            $CandidateRoot = $possible
            break
        }
        $cursor = $cursor.Parent
    }
}
if (-not $CandidateRoot) { throw 'verify-candidate was not found. Pass -CandidateRoot with its full path.' }
$candidate = (Resolve-Path -LiteralPath $CandidateRoot).Path
$root = Join-Path $candidate 'proctoring'
$desktop = Join-Path $root 'desktop'
$python = Join-Path $root '.venv\Scripts\python.exe'
$models = Join-Path $env:LOCALAPPDATA 'QorgauExam\models'
$replay = Join-Path $env:LOCALAPPDATA 'QorgauExam\replay'
$electronCli = Join-Path $desktop 'node_modules\electron\cli.js'
$node = (Get-Command node.exe -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$required = @($python, $electronCli, (Join-Path $desktop 'node_modules\electron\dist\electron.exe'),
    (Join-Path $desktop 'package.json'), (Join-Path $desktop 'native\qorgau_guard.py'),
    (Join-Path $models 'attention\face_landmarker.task'), (Join-Path $models 'phone\yolo11n.onnx'))
foreach ($file in $required) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required existing file is missing: $file" }
}

$changes = @{
    ELECTRON_RUN_AS_NODE = $null
    NODE_OPTIONS = $null
    QORGAU_SHELL_DEV_RENDERER_URL = $null
    QORGAU_SHELL_NATIVE_HELPER = $null
    QORGAU_PROCTORING_ROOT = $root
    QORGAU_PYTHON = $python
    PYTHONPATH = (Join-Path $root 'backend') + ';' + (Join-Path $root 'contracts\python')
    QORGAU_MODELS_DIR = $models
    QORGAU_REPLAY_DIR = $replay
    QORGAU_SHELL_DEMO_OPERATOR = '1'
    QORGAU_SHELL_NATIVE_ENFORCE = $(if ($Enforce) { '1' } else { '0' })
    QORGAU_SHELL_EMERGENCY_ACCELERATOR = 'CommandOrControl+Alt+Shift+F12'
}
Write-Host ''
Write-Host '===============================================================' -ForegroundColor Yellow
Write-Host '  Adal DEMO - EMERGENCY EXIT: Ctrl+Alt+Shift+F12' -ForegroundColor Yellow
Write-Host '  If needed: Ctrl+Alt+Del -> Task Manager' -ForegroundColor Yellow
Write-Host '===============================================================' -ForegroundColor Yellow
Write-Host "Desktop: $desktop"
Write-Host "Models:  $models"
Write-Host "Replay:  $replay"
Write-Host "QORGAU_SHELL_NATIVE_ENFORCE=$($changes.QORGAU_SHELL_NATIVE_ENFORCE)"
Write-Host 'ELECTRON_RUN_AS_NODE=unset; QORGAU_SHELL_DEMO_OPERATOR=1'
Write-Host 'The one-time DEMO teacher PIN appears in this console (unless a regular PIN is configured).'
Write-Host 'Keep this console out of the video. Close Adal normally after the demo.'
Write-Host 'Existing dependencies will be used to rebuild this candidate before launch (no install).'
if ($CheckOnly) {
    Write-Host 'CHECK OK: no application, camera, microphone or native hook was started.'
    return
}
$previous = @{}
foreach ($name in $changes.Keys) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
Push-Location -LiteralPath $desktop
try {
    foreach ($name in $changes.Keys) { [Environment]::SetEnvironmentVariable($name, $changes[$name], 'Process') }
    & $npm run build
    if ($LASTEXITCODE -ne 0) { throw "Desktop build failed with code $LASTEXITCODE; Electron was not started." }
    & $node $electronCli '.'
    if ($LASTEXITCODE -ne 0) { throw "Electron exited with code $LASTEXITCODE" }
} finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    Pop-Location
}
