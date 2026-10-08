# ASTRA-CHAIN — Windows full-backend classroom acceptance

Branch: `codex/classroom-chain-check`.
Origin verified: `https://github.com/diiaanns07-droid/Qostanay_hub.git`.
Product baseline: `23c6c3b1ce4dd28ed07f01aa9dbb72237ec578fd`.
Scope: only `proctoring/acceptance/classroom/check_chain.py`,
`proctoring/acceptance/classroom/chain/`, and this handoff.

## Implemented and observed

Reusable CLI starts local C1 and two separate full `python -m proctor serve`
processes with C2, each using its own API credential and data directory. Genuine
session/preflight/calibration/start/finish APIs select SYNTHETIC sources only.
No fake server, backend monkeypatch, simulator client, webcam, microphone,
Electron or native enforcement is used.

Windows 11 build 26200, Python 3.12.14, real process run on 2026-10-08:
**14 checks passed; 1 failed (synthetic provenance)**.

Passed: source checkout resolution despite an older editable install; three
processes; distinct students on one computer; synthetic preflight; addressed
start/finish isolation; both students' genuine C2 incident messages and JPEG
previews; local/server incident IDs, per-priority counts and closed states;
contiguous teacher stream and student class_state messages; hard C1 process
termination and reconnect with unchanged student IDs; persisted events and four
acknowledged commands; backend restart with persisted uplink identity; cleanup.

The current C2 hello carries no `simulated` provenance. C1 reports
`origin: real` on both synthetic students, incidents, events and preview headers.
The harness correctly fails this condition. Reported to coordinator; C1/C2
source files were not edited here. Label text alone does not fix provenance.
Future source propagation must consider that C2 joins before a session exists.

## Reproduce

```powershell
& '<venv>/Scripts/python.exe' proctoring/acceptance/classroom/check_chain.py --python '<venv>/Scripts/python.exe' --output proctoring/acceptance/classroom/chain/windows-result.json
```

The result JSON includes source SHA, OS/runtime, each check and observed evidence.
The README describes scope, process ownership and cleanup. Initial harness
iterations exposed Windows venv launcher child PIDs; ancestry is verified and
owned descendants are terminated for the crash case. No remote or user process
is killed. A second iteration replaced ineffective `taskkill` with direct,
ancestry-checked process termination; cleanup of those early runs succeeded.
The final run also routes child TEMP/TMP into its private directory, covering
A02 clip exports as well as configured backend/C1 data. Persistence compares
every pre-crash immutable event; a late closed `clip_available` update is allowed
and reported separately because clip encoding finishes asynchronously.

## Next

Coordinator routes/fixes synthetic origin propagation, integrates selected files,
then reruns this CLI on the final integration revision. No main merge requested.
This checkpoint's SHA and actual push outcome are sent out-of-band after commit.

## Git delivery

Harness checkpoint: `d182923e11ae8bc758024e481c738f47519daa6e`.
Push has **not succeeded**. The default attempt could not reach GitHub; the
escalated attempt was rejected by automatic approval review because the inherited
AGENTS instructions expect GOV_DIPLOME while this assigned worktree uses
Qostanay_hub. No alternate push route was attempted. The coordinator has the local
SHA and can integrate locally; a push requires trusted authorization resolving the
origin discrepancy. This handoff update is a separate local checkpoint.
