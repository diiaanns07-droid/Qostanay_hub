[CmdletBinding()]
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run packaging\prepare-windows.ps1 first.' }
& $pythonExe (Join-Path $PSScriptRoot 'preflight.py') --profile desktop
if ($LASTEXITCODE -ne 0) { throw 'Launch refused: incomplete dependencies, modules, build or model checksums.' }
if ($CheckOnly) { return }
$savedVars = @{}
foreach ($envName in @('QORGAU_PYTHON','QORGAU_PROCTORING_ROOT','QORGAU_SHELL_NATIVE_ENFORCE')) {
    $savedVars[$envName] = [Environment]::GetEnvironmentVariable($envName, 'Process')
}
$oldLocation = Get-Location
try {
    $env:QORGAU_PYTHON = $pythonExe
    $env:QORGAU_PROCTORING_ROOT = $projectRoot
    # Native enforcement remains an explicit A06 controlled-test operation.
    $env:QORGAU_SHELL_NATIVE_ENFORCE = '0'
    Set-Location -LiteralPath (Join-Path $projectRoot 'desktop')
    & '.\node_modules\.bin\electron.cmd' '.'
    if ($LASTEXITCODE -ne 0) { throw "Electron exited with code $LASTEXITCODE" }
} finally {
    Set-Location -LiteralPath $oldLocation.Path
    foreach ($envName in $savedVars.Keys) { [Environment]::SetEnvironmentVariable($envName, $savedVars[$envName], 'Process') }
}
