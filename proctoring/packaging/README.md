# Windows preparation and launch

These scripts are for the integrated candidate supplied by A01. They do not merge branches,
change registry/firewall settings, capture the camera, or enable the native keyboard hook.
This is a source checkout launch flow, not a standalone installer or portable executable.

From `proctoring/` in PowerShell, with uv and Node >=22.12 available:

```powershell
.\packaging\prepare-windows.ps1 -FetchModels
.\packaging\launch-windows.ps1 -CheckOnly
.\packaging\launch-windows.ps1
```

Preparation uses the unchanged A01 lockfiles, explicitly downloads models only with `-FetchModels`,
builds A06/A07, then checks model size and SHA256 against their manifests. Launch does no installation
or download. It refuses incomplete assets. The operator PIN must be configured with A06's
`desktop/main/tools/hash-pin.mjs` instructions before the rehearsal; no shared/default PIN is provided.
Native enforcement is disabled by this convenience launcher. A06's controlled Windows verification
procedure is the authority for enabling/testing it, including emergency exit.

For a bootstrap-only API check:

```powershell
.\packaging\prepare-windows.ps1 -BackendOnly
.\.venv\Scripts\python.exe packaging\preflight.py --profile backend
.\.venv\Scripts\python.exe qa\run_qa.py --with-baseline --label windows_candidate --expected-sha FULL_SHA_FROM_A01
```

`ready_for_launch` checks files/imports/checksums only. It never asserts CV accuracy, camera readiness,
protection of Windows shortcuts, or successful Electron rendering. Those are measured separately in
`qa/scenarios/WINDOWS.md`. Do not describe the source checkout as an offline installer: uv/npm and model
preparation need a network; runtime offline behaviour is tested after this preparation.

PowerShell scripts do not alter the machine execution policy. If the host blocks scripts, use the
organization's approved invocation policy or run the listed commands manually.
