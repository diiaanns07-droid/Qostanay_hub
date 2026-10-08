# Performance protocol (prompt item 5) — draft, thresholds to be agreed with A01

Owner: A09. Numbers below are **engineering targets, not results**. A result exists only with the record below
filled from a measurement on the integration candidate.

## Record (per run)

* Tested SHA, date, tester; hardware (CPU model/cores, RAM, GPU none/used), power plan (plugged in, "Balanced"/"Best
  performance"), Windows build/edition, Python/Electron/onnxruntime/mediapipe versions.
* Source: live camera model + resolution + requested/actual FPS, or replay clip id (+ pacing); lighting.
* Duration (cold start, 5 min, 30–60 min soak).

## Metrics and how they are taken

| Metric | Source | Target (to agree) |
|---|---|---|
| capture FPS | `GET /v1/sessions/{sid}/metrics` → `capture_fps` (A02) | ≥ camera FPS − 10 % |
| processed FPS per analyzer | `RuntimeMetrics.consumers[*]` (phone, attention) | phone ≥ 5, attention ≥ 10 (settings: 8 / 15 max) |
| UI preview FPS | count of `/v1/preview` frames per second at the client | ≥ 15 |
| capture→observation latency p50/p95 | `RuntimeMetrics` latency fields (A02 measures with `t_capture_mono_ns`) | p95 ≤ 500 ms |
| observation→episode latency | stream: `incident.opened` `sent_at` − first evidence observation `sent_at` minus rule duration | reported, no target yet |
| skipped/dropped frames | `frames_dropped`, consumer `skipped` | reported with the FPS above |
| memory | backend RSS + Electron main/renderer every 30 s (Task Manager CSV / `psutil` sampler outside the product) | no monotonic growth > 10 % after warm-up over 30–60 min |
| CPU | same sampler | reported |
| cold start | process start → READY line → preflight `ready` | reported (seconds) |

Rules: report p50/p95 with the number of samples; never average across machines; a run with thermal throttling or
on battery is labelled as such; synthetic-source numbers are labelled "synthetic, not camera" and are only a
harness check.

## Harness check available now

`qa/tests/test_stream_preview.py` and `test_e2e_synthetic.py` verify that metrics/preview/stream are wired and
contract-valid on the synthetic source (not performance). A sampler script will be added to `qa/` once A02 delivers
the latency fields on a real source.
