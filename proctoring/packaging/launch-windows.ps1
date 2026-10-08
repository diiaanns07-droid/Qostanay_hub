#Requires -Version 5.1
# Compatibility entry point. Process ownership, environment and validation live in Start-Student.
[CmdletBinding()]
param(
    [string]$Python,
    [string]$DataDir,
    [string]$ModelsDir,
    [switch]$CheckOnly,
    [switch]$Enforce,
    [switch]$DemoOperator,
    [switch]$BackendOnly,
    [ValidateRange(1,120)][int]$ReadyTimeoutSeconds = 60,
    [ValidateRange(0,3600)][int]$StopAfterSeconds = 0
)
$options = @{} + $PSBoundParameters
$options.Standalone = $true
& (Join-Path $PSScriptRoot '..\acceptance\classroom\Start-Student.ps1') @options
exit $LASTEXITCODE
