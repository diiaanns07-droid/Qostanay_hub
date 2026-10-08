# A14 — возможная речь или разговор рядом

Base: `6263aef9aabdee678b96d44b11cfec9e565370f7`; branch `codex/proctor-A14`.
Contract 1.1 unchanged. Ownership: `backend/proctor/audio/`, `handoffs/A14/`.

## Step 1 (2026-10-08)

Implemented local sounddevice InputStream: mono float32, 16000 Hz, 512 samples (32 ms).
One process lock + Windows named mutex; bounded 16-block memory queue. PCM is never
written, logged, sent to storage or network. Only contract scalar observations leave the monitor.
Pause/stop clears the queue; each start recalibrates the noise floor for 3 seconds.
Summaries are emitted every 500 ms of captured samples (quantized to 32 ms, 480/512 ms intervals).
Strict majority of block probabilities >=0.5 determines `present`; calibration is `unknown`.
Missing/denied device, stream gaps and overflow produce degraded health + unknown, never silence.

Silero v6.2, pinned official commit `be95df9152c0d7618fa1edfeb296fc3dae32376f`:

- [Model selection](https://github.com/snakers4/silero-vad/blob/be95df9152c0d7618fa1edfeb296fc3dae32376f/src/silero_vad/model.py)
- [Official wrapper](https://github.com/snakers4/silero-vad/blob/be95df9152c0d7618fa1edfeb296fc3dae32376f/src/silero_vad/utils_vad.py)
- [MIT license](https://github.com/snakers4/silero-vad/blob/be95df9152c0d7618fa1edfeb296fc3dae32376f/LICENSE)
- Artifact `src/silero_vad/data/silero_vad.onnx`, opset 16 selection.
- SHA256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` verified after download.
- Actual ORT inputs: `input` float32 [batch, samples], `state` float32 [2,batch,128], `sr` int64 scalar.
  At 16 kHz we send [1,576] = 64 context + 512 new samples and retain state.
  Outputs: `output` float32 [batch,1], `stateN` recurrent state (actual [2,1,128]).

From `proctoring`, after `uv sync --locked --extra cv --extra dev`:

```powershell
.venv/Scripts/python.exe -m proctor.audio.prepare --download
.venv/Scripts/python.exe -m proctor.audio.prepare --check
.venv/Scripts/python.exe -m pytest backend/proctor/audio/tests -q
```

Preparation writes model, MIT license and SHA256 manifest to
`%LOCALAPPDATA%\QorgauExam\models\audio\`. Runtime never downloads.
Missing/corrupt model or ONNX failure selects energy fallback: RMS over calibrated floor
and 300–3400 Hz spectral power ratio; determined summaries have `reasons=["energy_fallback"]`.
Fallback score is a heuristic, not a calibrated speech probability. In-band tones can trigger;
white noise and out-of-band tones are rejected in synthetic fixtures. Whisper can be missed.

## Step 2: integration and validation

Minimal shared-file edits:

- `backend/proctor/session.py`: construct one AudioMonitor lazily for LIVE only; start after RUNNING,
  stop/join before pause and finish/abort; restart/recalibrate on resume; publish exclusively via
  `SessionRuntime.publish_observation`. Synthetic and replay do not access the microphone.
- `backend/proctor/fusion/engine.py`: AudioObservation dispatch to `proctor.audio.fusion.AudioFusion`,
  expiry hook and close hook. The adapter is entirely in A14 ownership, so the engine patch is small
  and can be manually applied if A05 changed the same file. No app.py, contract or lockfile changes.

The adapter calls A05's unchanged `audio_rules.background_speech`, caps sample support at observed
time (no future extrapolation), retains a bounded 12 s scalar window and 5 min episode starts,
emits contract IncidentChange / Incident(rule_id=background_speech, category=audio), closes on
unknown/source loss/pause/finish, preserves low -> third episode medium. Runtime fallback is accepted
as limited evidence; unavailable audio is not evidence. Incident text: «Возможная речь или разговор рядом».

Verified on Windows:

```powershell
.venv/Scripts/python.exe -m pytest backend/proctor/audio/tests backend/proctor/fusion/tests backend/tests/test_lifecycle_api.py -q
```

157 passed (20 A14 tests, 121 existing fusion tests, 16 A01 lifecycle tests).
Includes real pinned ONNX inference, recurrent state shape, synthetic silence/noise/tones/AM,
missing and corrupt model, inference failure fallback, missing/denied device, exclusive ownership,
stop/restart, session hooks, 4-of-6 s threshold, third episode medium and expiry of repeat window.
`prepare --download`, `--check`, and `git diff --check` passed.

Additional app/capture + QA run: 9 passed, 1 skipped (camera-present conditional), 1 failed.
Failure is the existing `qa/tests/test_e2e_synthetic.py::test_full_synthetic_flow`:
`qa/qorgau_qa/scenario.py:66` hardcodes contract `1.0.0`; old QA summary schema rejects
`review_zone`, `review_zone_reasons_ru`, `review_zone_rule_version` already present in base 1.1.
No out-of-ownership QA/schema files were changed. This broader QA run is **not green**.

## LIVE: hardware run completed, acoustic conditions NOT confirmed

The real default Realtek microphone opened successfully at 16000 Hz mono, 512 samples.
Silero was active (`noise_calibration` -> `audio_ok`); no energy fallback or device errors.
Calibrated noise floor approximately -50.57 dBFS. `live-results.json` contains only scalar
contract observations and health, never PCM/audio. Operator reported background noise afterwards;
therefore the first window cannot be called verified silence. Speech/whisper timing is unconfirmed.

| Planned 20 s window | present / total | Mean probability | Max probability |
| --- | ---: | ---: | ---: |
| silence (uncontrolled background) | 0 / 40 | 0.0043 | 0.0471 |
| normal nearby speech (unconfirmed) | 2 / 40 | 0.1580 | 0.9471 |
| whisper at 1 m (unconfirmed) | 3 / 40 | 0.1128 | 0.7582 |

These values demonstrate feature capture, **not validated detection sensitivity**. Whisper may be
missed. Controlled 20/20/20 acoustic acceptance remains a plan unless operator confirms a repeat.

```powershell
# All stages with 5 s transitions; or select one stage to avoid chat timing ambiguity:
.venv/Scripts/python.exe -m proctor.audio.live --output handoffs/A14/live-repeat.json
.venv/Scripts/python.exe -m proctor.audio.live --phase silence --output handoffs/A14/live-silence.json
# --phase speech_nearby / --phase whisper_1m: countdown 10 s, quiet calibration 5 s, measurement 20 s.
```

Remaining: controlled LIVE acceptance; end-to-end real camera + audio laptop session is not tested.
No acoustic accuracy tuning is justified by this uncontrolled run. Build is not gated on LIVE.
