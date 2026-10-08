#Requires -Version 5.1
# Explicit, operator-started microphone check. No audio recording, camera or keyboard guard.
[CmdletBinding()]
param(
    [ValidateSet('all', 'silence', 'speech_nearby', 'whisper_1m')][string]$Phase = 'all',
    [string]$Python,
    [string]$Output,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Python) {
    $cursor = [IO.DirectoryInfo]$root
    while ($cursor) {
        $possible = Join-Path $cursor.FullName 'verify-candidate\proctoring\.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $possible -PathType Leaf) { $Python = $possible; break }
        $cursor = $cursor.Parent
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Existing Python venv not found. Pass -Python with its python.exe path. Nothing is installed.'
}
if (-not $Output) { $Output = Join-Path $PSScriptRoot ('yamnet-live-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json') }
$modelRoot = $env:QORGAU_MODELS_DIR
if (-not $modelRoot) { $modelRoot = Join-Path $env:LOCALAPPDATA 'QorgauExam\models' }
$changes = @{ PYTHONPATH = (Join-Path $root 'backend') + ';' + (Join-Path $root 'contracts\python');
    PYTHONIOENCODING = 'utf-8'; QORGAU_MODELS_DIR = $modelRoot }
$previous = @{}
foreach ($name in $changes.Keys) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    foreach ($name in $changes.Keys) { [Environment]::SetEnvironmentVariable($name, $changes[$name], 'Process') }
    & $Python -m proctor.audio.prepare --check
    if ($LASTEXITCODE -ne 0) { throw 'Model verification failed; microphone was not opened.' }
    if ($CheckOnly) { Write-Host 'CHECK OK. No microphone or capture was started.'; return }
    Write-Host 'Adal / A14 LIVE: default microphone. Stop with Ctrl+C.' -ForegroundColor Yellow
    Write-Host 'Finish the current Adal test exam first so its audio monitor releases the microphone.'
    Write-Host 'Keep quiet for calibration; then follow the SILENCE / SPEECH at 1m / WHISPER at 1m prompts.'
    Write-Host 'Only scalar diagnostics are saved. No sound is recorded or transmitted.'
    Write-Host "Output: $Output"
    $arguments = @('-m', 'proctor.audio.live', '--output', $Output, '--countdown', '10')
    if ($Phase -ne 'all') { $arguments += @('--phase', $Phase) }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "LIVE check exited with code $LASTEXITCODE" }
} finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
