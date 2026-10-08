#Requires -Version 5.1
<#
.SYNOPSIS
Запуск настоящего сервера класса C1. По умолчанию доступен только на этом ПК.
.EXAMPLE
.\Start-Teacher.ps1 -CheckOnly
.EXAMPLE
.\Start-Teacher.ps1 -Lan -Port 8765
#>
[CmdletBinding()]
param(
    [string]$Python,
    [ValidateRange(0, 65535)][int]$Port = 8765,
    [string]$DataDir,
    [switch]$Lan,
    [switch]$CheckOnly,
    [ValidateRange(1, 120)][int]$ReadyTimeoutSeconds = 30,
    [ValidateRange(0, 3600)][int]$StopAfterSeconds = 0,
    [switch]$Library
)

# Shared private launcher functions; Start-Student dot-sources this file with -Library.
function Get-QorgauRoot {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
}

function Get-QorgauPython([string]$Executable, [string]$Root) {
    if (-not $Executable) { $Executable = Join-Path $Root '.venv\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Python 3.12 не найден. Укажите -Python 'C:\путь\python.exe' или подготовьте proctoring\.venv по README.md."
    }
    (Resolve-Path -LiteralPath $Executable).Path
}

function Get-QorgauEnvironment([string]$Root) {
    # No parent PYTHONPATH or editable-install fallback: use this checkout first.
    @{
        PYTHONPATH = (@($Root, (Join-Path $Root 'backend'), (Join-Path $Root 'contracts\python')) -join ';')
        PYTHONUNBUFFERED = '1'
        PYTHONIOENCODING = 'utf-8'
        PYTHONDONTWRITEBYTECODE = '1'
        QORGAU_PROCTORING_ROOT = $Root
    }
}

function Initialize-QorgauJob {
    if ('Qorgau.LaunchJob' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace Qorgau {
    // A child waits on stdin until attached. Descendants inherit this job.
    // Closing PowerShell, even abruptly, closes the last job handle.
    public sealed class LaunchJob : IDisposable {
        [StructLayout(LayoutKind.Sequential)] struct Limits {
            public long ProcessTime, JobTime;
            public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct Counters {
            public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
        }
        [StructLayout(LayoutKind.Sequential)] struct Extended {
            public Limits Basic;
            public Counters IO;
            public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool SetInformationJobObject(IntPtr job, int info, ref Extended limits, uint size);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        IntPtr handle;
        public LaunchJob() {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) throw new Win32Exception();
            var limits = new Extended();
            limits.Basic.Flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(limits))) {
                int error = Marshal.GetLastWin32Error(); Dispose(); throw new Win32Exception(error);
            }
        }
        public void Attach(IntPtr process) {
            if (!AssignProcessToJobObject(handle, process)) throw new Win32Exception();
        }
        public void Dispose() {
            if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; }
        }
    }
}
'@
}

function Start-QorgauProcess([string]$PythonExe, [string]$Root, [hashtable]$Environment, [hashtable]$Payload) {
    Initialize-QorgauJob
    # Payload and backend token travel via stdin, never through process arguments.
    $worker = @'
import json, os, runpy, subprocess, sys
cfg = json.loads(sys.stdin.readline())
if cfg['kind'] == 'probe':
    import importlib, importlib.util
    from pathlib import Path
    if sys.version_info[:2] != (3, 12):
        print('Python 3.12 required; found ' + sys.version.split()[0]); sys.exit(2)
    root = Path(cfg['root']).resolve()
    for name, location in cfg['sources'].items():
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin or Path(spec.origin).resolve() != root / location:
            print('Wrong source: ' + name); sys.exit(2)
    for name in cfg['imports']:
        try:
            importlib.import_module(name)
        except Exception:
            print('Missing or broken dependency: ' + name); sys.exit(2)
    print('QORGAU_CHECK_OK ' + str(root))
elif cfg['kind'] == 'desktop':
    sys.exit(subprocess.call([cfg['electron'], cfg['desktop']], cwd=cfg['desktop']))
else:
    sys.argv = [cfg['module']] + cfg['args']
    runpy.run_module(cfg['module'], run_name='__main__')
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($worker))
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $PythonExe
    $info.Arguments = "-u -c `"import base64;exec(base64.b64decode('$encoded'))`""
    $info.WorkingDirectory = $Root
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    foreach ($key in $Environment.Keys) {
        if ($null -eq $Environment[$key]) { $info.EnvironmentVariables.Remove($key) }
        else { $info.EnvironmentVariables[$key] = [string]$Environment[$key] }
    }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $job = New-Object Qorgau.LaunchJob
    try {
        if (-not $process.Start()) { throw 'Не удалось создать процесс.' }
        $job.Attach($process.Handle)
        $process.StandardInput.WriteLine(($Payload | ConvertTo-Json -Compress -Depth 5))
        $process.StandardInput.Flush()
        return @{ Process = $process; Job = $job }
    } catch {
        if ($process.Id -and -not $process.HasExited) { $process.Kill() }
        $job.Dispose()
        $process.Dispose()
        throw 'Не удалось безопасно запустить процесс Windows. Проверьте ограничения запуска приложений.'
    }
}

function Stop-QorgauProcess($Child) {
    if ($null -eq $Child) { return }
    try {
        if (-not $Child.Process.HasExited) {
            $Child.Process.StandardInput.Close()
            # C1 and proctor stop on EOF. Electron is normally closed through its window.
            [void]$Child.Process.WaitForExit(8000)
        }
    } finally {
        $Child.Job.Dispose()
        $Child.Process.Dispose()
    }
}

function Test-QorgauPython([string]$PythonExe, [string]$Root, [hashtable]$Environment, [switch]$Student) {
    $sources = @{ classroom = 'classroom/__init__.py' }
    $imports = @('fastapi', 'uvicorn', 'websockets', 'pydantic')
    if ($Student) {
        $sources.proctor = 'backend/proctor/__init__.py'
        $sources.proctor_contracts = 'contracts/python/proctor_contracts/__init__.py'
        $imports += 'numpy'
    }
    $child = $null
    try {
        $child = Start-QorgauProcess $PythonExe $Root $Environment @{kind='probe'; root=$Root; sources=$sources; imports=$imports}
        $out = $child.Process.StandardOutput.ReadToEndAsync()
        $err = $child.Process.StandardError.ReadToEndAsync()
        if (-not $child.Process.WaitForExit(30000)) { throw 'Проверка Python не завершилась за 30 секунд.' }
        if ($child.Process.ExitCode -ne 0) {
            $detail = $out.GetAwaiter().GetResult().Trim()
            throw "Python или зависимости не готовы. $detail Подготовка: README.md, раздел «Подготовка»."
        }
    } finally { Stop-QorgauProcess $child }
}

function Wait-QorgauProcess($Child, [string]$Mode, [int]$Timeout, [int]$StopAfter, [bool]$AllowLan = $false, [string[]]$Secrets = @()) {
    $process = $Child.Process
    $stdout = $process.StandardOutput.ReadLineAsync()
    $stderr = $process.StandardError.ReadLineAsync()
    $deadline = [DateTime]::UtcNow.AddSeconds($Timeout)
    $readyAt = $null
    $readyPort = $null
    $isDesktop = $Mode -eq 'desktop'
    if ($isDesktop) { $readyAt = [DateTime]::UtcNow }
    while ($true) {
        foreach ($stream in @('stdout', 'stderr')) {
            $task = if ($stream -eq 'stdout') { $stdout } else { $stderr }
            if ($null -eq $task -or -not $task.IsCompleted) { continue }
            $line = $task.GetAwaiter().GetResult()
            if ($null -eq $line) {
                if ($stream -eq 'stdout') { $stdout = $null } else { $stderr = $null }
                continue
            }
            if ($stream -eq 'stdout') { $stdout = $process.StandardOutput.ReadLineAsync() }
            else { $stderr = $process.StandardError.ReadLineAsync() }
            foreach ($secret in $Secrets) { if ($secret) { $line = $line.Replace($secret, '[скрыто]') } }
            if ($line -match '^QORGAU_(CLASS_)?READY (.+)$') {
                $ready = $Matches[2] | ConvertFrom-Json
                if ($ready.port -lt 1 -or $ready.port -gt 65535) { throw 'Сервер сообщил неверный порт.' }
                $readyAt = [DateTime]::UtcNow
                $readyPort = [int]$ready.port
                Write-Host "QORGAU_LAUNCH_READY $Mode port=$readyPort pid=$($ready.pid)"
                if ($Mode -eq 'teacher') {
                    Write-Host "Готово. Панель преподавателя: http://127.0.0.1:$readyPort/"
                    if ($AllowLan) {
                        $addresses = @([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
                            Where-Object { $_.OperationalStatus -eq 'Up' } |
                            ForEach-Object { $_.GetIPProperties().UnicastAddresses } |
                            Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' -and -not [Net.IPAddress]::IsLoopback($_.Address) } |
                            ForEach-Object { $_.Address.IPAddressToString } | Select-Object -Unique)
                        foreach ($address in $addresses) { Write-Host "Адрес PC1 для -Server: ${address}:$readyPort" }
                        if (-not $addresses.Count) { Write-Host 'Адрес локальной сети не найден. Проверьте подключение PC1.' }
                        Write-Host 'Для PC2/PC3 выберите адрес той же сети; доступ через брандмауэр проверьте вручную.'
                    } else { Write-Host 'Режим этого ПК. Для PC2/PC3 перезапустите с -Lan.' }
                } else { Write-Host "Backend готов: http://127.0.0.1:$readyPort/ (API требует внутренний токен)." }
            } elseif ($line -match '^QORGAU_CLASS_PIN (\d+)$') {
                Write-Host "PIN входа преподавателя (только для PC1): $($Matches[1])"
            } elseif ($Mode -eq 'teacher' -and $line -match 'Панель преподавателя|Адрес для студентов') {
                # C1 prints LAN addresses even on loopback. Render the actual bind mode above instead.
            } elseif ($line) { Write-Host $line }
        }
        if ($process.HasExited -and $null -eq $stdout -and $null -eq $stderr) { break }
        if ($null -eq $readyAt -and [DateTime]::UtcNow -gt $deadline) {
            throw "Нет ответа о готовности за $Timeout секунд. Проверьте занятость порта и доступ к каталогу данных."
        }
        if ($readyAt -and $StopAfter -gt 0 -and ([DateTime]::UtcNow - $readyAt).TotalSeconds -ge $StopAfter) {
            $process.StandardInput.Close()
            if (-not $process.WaitForExit(8000)) { throw 'Процесс не завершился по EOF; будет остановлен вместе с дочерними процессами.' }
        }
        Start-Sleep -Milliseconds 50
    }
    if ($process.ExitCode -ne 0) { throw "Процесс завершился с кодом $($process.ExitCode). Проверьте сообщение выше." }
    if (-not $isDesktop -and $null -eq $readyAt) { throw 'Процесс завершился до готовности.' }
    Write-Host 'Qorgau остановлен.'
}

if ($Library) { return }
$ErrorActionPreference = 'Stop'
$child = $null
try {
    $root = Get-QorgauRoot
    $pythonExe = Get-QorgauPython $Python $root
    $environment = Get-QorgauEnvironment $root
    Test-QorgauPython $pythonExe $root $environment
    if (-not (Test-Path -LiteralPath (Join-Path $root 'class-panel\index.html'))) { throw 'Панель преподавателя отсутствует в этой копии проекта.' }
    Write-Host "Исходники C1: $root"
    if ($CheckOnly) {
        Write-Host 'Проверка готова: Python 3.12, зависимости C1 и панель найдены. Сервер и сеть не запускались.'
        exit 0
    }
    $bind = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
    $serverArgs = @('--host', $bind, '--port', [string]$Port, '--ui', 'class-panel', '--exit-on-stdin-eof')
    if ($DataDir) { $serverArgs += @('--data-dir', [IO.Path]::GetFullPath($DataDir)) }
    Write-Host "Запуск C1 ($bind). Остановить: Ctrl+C в этом окне. PIN появится после готовности."
    $child = Start-QorgauProcess $pythonExe $root $environment @{kind='teacher'; module='classroom.server'; args=$serverArgs}
    Wait-QorgauProcess $child 'teacher' $ReadyTimeoutSeconds $StopAfterSeconds ([bool]$Lan)
} catch {
    Write-Host "Не готово: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally { Stop-QorgauProcess $child }
