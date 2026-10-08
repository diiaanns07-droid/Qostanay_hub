from datetime import datetime, timedelta, timezone
import json
import time

import numpy as np
import pytest

from proctor.audio.monitor import AudioMonitor, MicrophoneLease
from proctor.audio.vad import AudioDetector, BLOCK, RATE, SileroVad


def run_signal(tmp_path, signal):
    detector = AudioDetector(tmp_path)
    results = []
    for block in np.concatenate((np.zeros(RATE * 4), signal)).reshape(-1, BLOCK):
        result = detector.feed(block)
        if result is not None:
            results.append(result)
    return results


@pytest.mark.parametrize("kind,expected", [("silence", "absent"), ("noise", "absent"),
    ("sine_outside_band", "absent"), ("sine_in_band", "present"), ("speech_like_am", "present")])
def test_energy_synthetic(tmp_path, kind, expected):
    t = np.arange(RATE * 4) / RATE
    rng = np.random.default_rng(14)
    signals = {
        "silence": np.zeros(t.size), "noise": rng.normal(0, 0.1, t.size),
        "sine_outside_band": 0.2 * np.sin(2 * np.pi * 100 * t),
        "sine_in_band": 0.2 * np.sin(2 * np.pi * 1000 * t),
        "speech_like_am": 0.08 * (1 + 0.7 * np.sin(2 * np.pi * 4 * t)) *
            sum(np.sin(2 * np.pi * f * t) for f in (400, 800, 1200, 1600)),
    }
    results = run_signal(tmp_path, signals[kind])
    assert all(r.voice_like == "unknown" for r in results[:6])
    assert all(r.voice_like == expected for r in results[-6:])
    assert all(r.reasons == ["energy_fallback"] for r in results[-6:])
    assert all(-200 <= r.rms_dbfs <= 0 for r in results)


def test_majority_and_cadence(tmp_path):
    detector = AudioDetector(tmp_path)
    class FakeVad:
        def probability(self, block):
            return float(block[0])
    detector.vad = FakeVad()
    results = []
    for i in range(500):
        result = detector.feed(np.full(BLOCK, 0.8 if i % 4 else 0.1))
        if result:
            results.append((i, result))
    assert len(results) == 32  # 16 seconds / 0.5; intervals 15 or 16 blocks
    assert {b[0] - a[0] for a, b in zip(results, results[1:])} <= {15, 16}
    assert all(r.voice_like == "present" for _, r in results[7:])


class Clock:
    def now_ms(self):
        return 0.0
    def wall_at(self, t):
        return datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(milliseconds=t)


@pytest.mark.parametrize("error", [RuntimeError("no device"), PermissionError("denied")])
def test_no_device_is_unknown(tmp_path, error):
    observations = []
    def missing(**kwargs):
        assert kwargs["samplerate"] == 16000 and kwargs["blocksize"] == 512 and kwargs["channels"] == 1
        raise error
    monitor = AudioMonitor("test", "live", Clock(), observations.append,
                           stream_factory=missing, detector_factory=lambda: AudioDetector(tmp_path))
    monitor.start()
    monitor._thread.join(2)
    monitor.stop()
    assert [(o.kind, o.status.value) for o in observations] == [("health", "degraded"), ("audio", "degraded")]
    assert observations[-1].voice_like == "unknown"
    assert observations[-1].rms_dbfs is None


def test_single_owner():
    with MicrophoneLease():
        with pytest.raises(RuntimeError, match="microphone_owned"):
            with MicrophoneLease():
                pass
    with MicrophoneLease():
        pass


def test_model_missing_and_invalid_input(tmp_path):
    detector = AudioDetector(tmp_path)
    assert detector.vad is None
    with pytest.raises(ValueError):
        detector.feed(np.zeros(100))
    with pytest.raises(ValueError):
        detector.feed(np.full(BLOCK, np.nan))


def test_manifest_rejects_tampering(tmp_path):
    from proctor.audio.assets import check, REVISION, MODEL_SHA256
    (tmp_path / "manifest.json").write_text(json.dumps(dict(revision=REVISION, sha256=MODEL_SHA256)))
    (tmp_path / "silero_vad.onnx").write_bytes(b"wrong weights")
    with pytest.raises(ValueError, match="SHA256"):
        check(tmp_path)
    assert AudioDetector(tmp_path).vad is None


def test_onnx_failure_selects_fallback(tmp_path):
    detector = AudioDetector(tmp_path)
    class BrokenVad:
        def probability(self, block):
            raise RuntimeError("inference failed")
    detector.vad = BrokenVad()
    results = [detector.feed(np.zeros(BLOCK)) for _ in range(150)]
    determined = [r for r in results if r is not None and r.voice_like != "unknown"]
    assert determined and all(r.reasons == ["energy_fallback"] for r in determined)
    assert all(r.voice_like == "absent" for r in determined)


def test_monitor_stop_restart_releases_owner(tmp_path):
    opened = []
    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
        def __enter__(self):
            opened.append("open")
            self.callback(np.zeros((BLOCK, 1), dtype=np.float32), BLOCK, None, False)
            return self
        def __exit__(self, *args):
            opened.append("closed")
    monitor = AudioMonitor("test", "live", Clock(), lambda o: None,
                           stream_factory=Stream, detector_factory=lambda: AudioDetector(tmp_path))
    for cycle in range(2):
        monitor.start()
        deadline = time.monotonic() + 2
        while opened.count("open") < cycle + 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        monitor.start()  # idempotent
        monitor.stop()
        assert monitor._thread is None
    assert opened == ["open", "closed", "open", "closed"]
    with MicrophoneLease():
        pass


def test_installed_official_model_synthetic():
    from proctor.audio.assets import check
    try:
        path = check()
    except (OSError, ValueError, RuntimeError):
        pytest.skip("explicit prepare --download required for real-model test")
    vad = SileroVad(path)
    rng = np.random.default_rng(14)
    t = np.arange(BLOCK * 125) / RATE
    signals = [np.zeros(t.size), rng.normal(0, 0.1, t.size), 0.2 * np.sin(2 * np.pi * 1000 * t),
               0.1 * (1 + 0.7 * np.sin(2 * np.pi * 4 * t)) * np.sin(2 * np.pi * 800 * t)]
    for signal in signals:
        vad.reset()
        values = [vad.probability(b.astype(np.float32)) for b in signal.reshape(-1, BLOCK)]
        assert all(0 <= p <= 1 for p in values)
        assert vad.state.shape == (2, 1, 128)
        # Synthetic AM is not human speech; no sensitivity claim is made from this fixture.
    vad.reset()
    assert vad.probability(np.zeros(BLOCK, dtype=np.float32)) < 0.5
