[CmdletBinding()]
param([switch]$BackendOnly, [switch]$FetchModels)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$oldLocation = Get-Location
try {
    Set-Location -LiteralPath $projectRoot
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv, then run this script again. Python 3.12 is required.' }
    if (-not $BackendOnly) {
        foreach ($relative in @('desktop\main\src\main.ts','desktop\renderer\src\main.tsx')) {
            if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relative))) { throw "A01 candidate is incomplete: $relative" }
        }
    }
    & uv sync --frozen --extra cv --extra dev
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Python installation failed.' }
    $pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if ($FetchModels) {
        foreach ($moduleName in @('phone','attention')) {
            if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "backend\proctor\$moduleName\model_tool.py"))) { throw "Missing A01 module: $moduleName" }
            & $pythonExe -m "proctor.$moduleName.model_tool" fetch
            if ($LASTEXITCODE -ne 0) { throw "Model preparation failed: $moduleName" }
        }
    }
    if ($BackendOnly) {
        & $pythonExe (Join-Path $PSScriptRoot 'preflight.py') --profile backend
        if ($LASTEXITCODE -ne 0) { throw 'Backend readiness check failed.' }
    } else {
        Set-Location -LiteralPath (Join-Path $projectRoot 'desktop')
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
        & npm.cmd run typecheck
        if ($LASTEXITCODE -ne 0) { throw 'Type checking failed.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
        & $pythonExe (Join-Path $PSScriptRoot 'preflight.py') --profile desktop
        if ($LASTEXITCODE -ne 0) { throw 'Desktop readiness failed; read the failed checks.' }
    }
} finally { Set-Location -LiteralPath $oldLocation.Path }
