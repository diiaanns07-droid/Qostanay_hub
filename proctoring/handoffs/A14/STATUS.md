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

Verified: 10 synthetic/unit tests passed; `prepare --download` and `--check` passed on Windows.
Not yet verified: real-model synthetic inference, lifecycle/fusion integration, LIVE.
LIVE remains a plan until actual 20 s silence / 20 s nearby speech / 20 s whisper at 1 m
are observed under operator-confirmed conditions. No recording is needed or allowed.
