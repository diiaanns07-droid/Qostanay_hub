# Consented replay through A02

Requires A02 in A01's integration candidate. The published A02 format is `qorgau.replay.v1`.
No recording is included here, and no fake hash or placeholder manifest is presented as a runnable recording.
Real media and manifests live in a local directory outside the source repository.

From `proctoring/`, after participants have explicitly agreed to recording:

```powershell
$replayFolder = Join-Path $env:LOCALAPPDATA 'QorgauExamDemo\replay'
.\.venv\Scripts\python.exe -m proctor.capture record --source live --replay-id demo-consented-01 --seconds 90 --title 'Consented rehearsal' --consent 'All volunteers agreed to this local rehearsal recording' --replay-dir $replayFolder
.\.venv\Scripts\python.exe -m proctor.capture replay-check demo-consented-01 --replay-dir $replayFolder
```

The recorder creates the real media, timestamp sidecar, manifest and computed SHA256. Do not use the
example consent statement unless it is true. The command intentionally requires manual invocation;
the A10 automated rehearsal never opens a camera.

Record in sequence: normal reading; brief keyboard glance; phone visible; phone raised while looking
down; long side glance; second person; leaving frame; normal reading again. Annotate intervals after
watching the recording. Each label has `label`, `t_start_ms`, `t_end_ms`, optional `note`. The labels are
ground truth for evaluation; A02 does not feed them into detectors. Keep labels and notes free of names.

For API replay on the same prepared candidate:

```powershell
.\.venv\Scripts\python.exe demo\rehearse.py --mode replay --expected-sha FULL_SHA_FROM_A01 --replay-id demo-consented-01 --replay-dir $replayFolder --wait-seconds 120 --out demo\results\replay_rehearsal.json
```

In the Electron UI, choose REPLAY explicitly and use the same replay id/directory. For a long live
presentation of a short recording, A02 supports `loop=true`; state that it loops. For evaluation,
use a non-looping recording with complete labelled intervals. Reaching the end of replay during
an active exam may correctly report a monitoring gap.

The replay exercises camera decoding and real CV when the modules and models are integrated.
It does not reproduce interactive five-point gaze calibration, real keyboard enforcement or a
live student. Those checks remain separate. A recording of a laptop screen is backup video, not
input media for the camera CV pipeline.
