from datetime import datetime, timezone
import threading
import time

import numpy as np
import pytest

from proctor.audio.combined import AudioConfig, CombinedAudioDetector
from proctor.audio.monitor import AudioMonitor, MicrophoneLease
from proctor.audio.vad import BLOCK, RATE, Summary
from proctor.audio.yamnet import SpeechScore, WINDOW_SAMPLES, speech_score
from proctor.audio.yamnet_worker import WindowScore, YamnetWorker
from proctor_contracts.v1 import AudioObservation


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.005)
    raise AssertionError("worker did not reach expected state")


class Primary:
    def __init__(self, probability=0.1, has_silero=True, unknown=False):
        self.vad = object() if has_silero else None
        self.probability = probability
        self.unknown = unknown
        self.resets = 0

    def feed(self, block):
        reasons = [] if self.vad else ["energy_fallback"]
        return Summary("unknown" if self.unknown else "present" if self.probability >= 0.5 else "absent",
                       None if self.unknown else self.probability, -30.0, -50.0, reasons)

    def reset(self):
        self.resets += 1


class Worker:
    def __init__(self, scores=None, state="ready"):
        self.scores = speech_score(scores or {})
        self.state = state
        self.submissions = []
        self.resets = 0
        self.closed = False

    def submit(self, waveform, end):
        assert waveform.shape == (15600,)
        self.submissions.append(end)

    def latest(self, now, max_age):
        return WindowScore(now, 0, self.scores, 3.0) if self.state == "ready" else None

    def reset(self):
        self.resets += 1

    def close(self):
        self.closed = True
        return True


def detector(primary, worker, config=None):
    return CombinedAudioDetector(config=config or AudioConfig(), primary_factory=lambda _: primary,
                                 worker_factory=lambda _: worker)


@pytest.mark.parametrize("silero,scores,present,reasons", [
    (0.1, {"Whispering": 0.4}, True, ["yamnet.whispering"]),
    (0.8, {"Speech": 0.1}, True, ["silero"]),
    (0.8, {"Conversation": 0.7}, True, ["silero", "yamnet.conversation"]),
    (0.49, {"Speech": 0.29}, False, []),
    (0.5, {}, True, ["silero"]),
    (0.1, {"Speech": 0.3}, True, ["yamnet.speech"]),
    (0.1, {"Speech": 0.16, "Whispering": 0.2}, True, ["yamnet.whispering"]),
    (0.1, {"Child speech, kid speaking": 0.4}, True, ["yamnet.child_speech"]),
    (0.1, {"Narration, monologue": 0.4}, True, ["yamnet.narration"]),
    (0.1, {"Babbling": 0.4}, True, ["yamnet.babbling"]),
])
def test_or_and_contract_reasons(silero, scores, present, reasons):
    result = detector(Primary(silero), Worker(scores)).feed(np.zeros(BLOCK))
    assert result.voice_like == ("present" if present else "absent")
    assert result.reasons == reasons
    assert result.probability == max(silero, speech_score(scores).speech_sum)
    assert not result.observation_degraded
    wire = AudioObservation(observation_id="test", session_id="test", frame_id=None, t_session_ms=1000,
        wall_time=datetime.now(timezone.utc), source_mode="live", producer={"module": "audio", "version": "test"},
        status="ok", voice_like=result.voice_like, voice_probability=result.probability, reasons=result.reasons)
    assert wire.reasons == reasons  # validates the unchanged Code pattern, no ':' workaround


@pytest.mark.parametrize("name", ["Music", "Typing", "Computer keyboard", "Mouse click", "Silence",
    "Writing", "Rustle", "Air conditioning", "Mechanical fan", "Vehicle"])
def test_noise_never_raises_the_yamnet_vote(name):
    result = detector(Primary(0.1), Worker({name: 0.99})).feed(np.zeros(BLOCK))
    assert result.voice_like == "absent" and result.reasons == []


def test_missing_yamnet_preserves_valid_silero():
    result = detector(Primary(0.8), Worker(state="unavailable")).feed(np.zeros(BLOCK))
    assert result.health_code == "yamnet_unavailable"
    assert result.voice_like == "present" and result.reasons == ["silero"]
    assert not result.observation_degraded  # A05 must not lose valid primary observations


def test_no_silero_keeps_yamnet_and_original_energy_fallback():
    yam = detector(Primary(0.1, has_silero=False), Worker({"Whispering": 0.8})).feed(np.zeros(BLOCK))
    assert yam.voice_like == "present" and yam.reasons == ["yamnet.whispering", "energy_fallback"]
    assert not yam.observation_degraded
    energy = detector(Primary(0.8, has_silero=False), Worker(state="unavailable")).feed(np.zeros(BLOCK))
    assert energy.voice_like == "present" and energy.reasons == ["energy_fallback"]
    assert energy.observation_degraded  # accepted unchanged by A14's existing fusion adapter


def test_calibration_is_unknown_even_when_models_would_vote_present():
    result = detector(Primary(0.9, unknown=True), Worker({"Speech": 0.9})).feed(np.zeros(BLOCK))
    assert result.voice_like == "unknown" and result.probability is None
    assert result.reasons == ["noise_calibration"] and result.observation_degraded
    assert result.diagnostics["final_present"] is None


@pytest.mark.parametrize("primary,worker", [
    (Primary(0.1), Worker({"Whispering": 0.8})),
    (Primary(0.8), Worker(state="unavailable")),
    (Primary(0.8, has_silero=False), Worker(state="unavailable")),
])
def test_combined_evidence_reaches_unchanged_background_speech_rule(primary, worker):
    from proctor.audio.fusion import AudioFusion
    fusion = AudioFusion("test", "live", lambda _: datetime.now(timezone.utc))
    combined = detector(primary, worker)
    changes = []
    for index in range(13):
        result = combined.feed(np.zeros(BLOCK))
        observation = AudioObservation(observation_id=f"audio-{index}", session_id="test", frame_id=None,
            t_session_ms=index * 500, wall_time=datetime.now(timezone.utc), source_mode="live",
            producer={"module": "audio", "version": "test"},
            status="degraded" if result.observation_degraded else "ok", voice_like=result.voice_like,
            voice_probability=result.probability, reasons=result.reasons)
        changes.extend(fusion.feed(observation))
    assert fusion.active is not None and fusion.active.rule_id == "background_speech"
    assert changes


def test_windows_bounded_cadence_and_reset():
    primary, worker = Primary(), Worker()
    combined = detector(primary, worker)
    for _ in range(125):
        combined.feed(np.zeros(BLOCK))
        assert combined.waveform.size <= WINDOW_SAMPLES
    assert worker.submissions[0] == 15872
    assert all(b - a == 8192 for a, b in zip(worker.submissions, worker.submissions[1:]))
    combined.reset()
    assert combined.waveform.size == 0 and combined.samples == 0
    assert primary.resets == worker.resets == 1
    combined.close()
    assert worker.closed


def test_threshold_configuration(monkeypatch):
    monkeypatch.setenv("QORGAU_AUDIO_YAMNET_THRESHOLD", "0.6")
    config = AudioConfig.from_env()
    assert detector(Primary(0.1), Worker({"Speech": 0.4}), config).feed(np.zeros(BLOCK)).voice_like == "absent"
    for value in (0, -1, 1.1, float("nan")):
        with pytest.raises(ValueError):
            AudioConfig(yamnet_threshold=value)


def test_worker_latest_only_stale_results_and_cleanup():
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    calls = []
    class Classifier:
        def classify(self, waveform):
            calls.append((threading.get_ident(), float(waveform[0])))
            if len(calls) == 1:
                entered.set()
                assert release.wait(3)
            return SpeechScore(float(waveform[0]), "Speech", float(waveform[0]))
        def close(self):
            closed.set()
    worker = YamnetWorker(Classifier)
    try:
        worker.submit(np.full(WINDOW_SAMPLES, 0.1), 15600)
        assert entered.wait(2)
        # Both submissions complete while inference is blocked: no unbounded queue or waiting caller.
        worker.submit(np.full(WINDOW_SAMPLES, 0.2), 23792)
        worker.submit(np.full(WINDOW_SAMPLES, 0.3), 31984)
        assert worker.replaced_windows == 1
        release.set()
        result = until(lambda: worker.latest(31984, 1))
        assert result.score.speech_sum == pytest.approx(0.3)
        assert len(calls) == 2 and all(tid != threading.get_ident() for tid, _ in calls)
        assert worker.latest(31986, 1) is None
    finally:
        release.set()
        assert worker.close()
    assert closed.is_set() and worker.latest(31984, RATE) is None


def test_worker_reset_discards_inflight_generation():
    entered, release = threading.Event(), threading.Event()
    class Classifier:
        def classify(self, waveform):
            if waveform[0] > 0.8:
                entered.set()
                assert release.wait(3)
            return SpeechScore(float(waveform[0]), "Speech", float(waveform[0]))
        def close(self):
            pass
    worker = YamnetWorker(Classifier)
    try:
        worker.submit(np.full(WINDOW_SAMPLES, 0.9), 15600)
        assert entered.wait(2)
        worker.reset()
        assert worker.latest(15600, RATE) is None
        worker.submit(np.full(WINDOW_SAMPLES, 0.1), 15600)
        release.set()
        result = until(lambda: worker.latest(15600, RATE))
        assert result.generation == 1 and result.score.speech_sum == pytest.approx(0.1)
    finally:
        release.set()
        worker.close()


@pytest.mark.parametrize("failure", ["load", "inference"])
def test_worker_failure_is_unavailable_and_releases_classifier(failure):
    closed = threading.Event()
    class Classifier:
        def __init__(self):
            if failure == "load":
                raise FileNotFoundError("model absent")
        def classify(self, waveform):
            raise RuntimeError("inference failed")
        def close(self):
            closed.set()
    worker = YamnetWorker(Classifier)
    worker.submit(np.zeros(WINDOW_SAMPLES), 15600)
    until(lambda: worker.state == "unavailable")
    assert worker.latest(15600, RATE) is None
    assert worker.close()
    assert closed.is_set() == (failure == "inference")


def test_monitor_reports_auxiliary_health_without_invalidating_primary_or_leaking_worker():
    observations = []
    done = threading.Event()
    primary, worker = Primary(0.8), Worker(state="unavailable")
    combined = detector(primary, worker)
    class Clock:
        def now_ms(self): return 3500.0
        def wall_at(self, t): return datetime.now(timezone.utc)
    class Stream:
        def __init__(self, **kwargs): self.callback = kwargs["callback"]
        def __enter__(self):
            self.callback(np.zeros((BLOCK, 1)), BLOCK, None, False)
            return self
        def __exit__(self, *args): pass
    def publish(obs):
        observations.append(obs)
        if obs.kind == "audio": done.set()
    monitor = AudioMonitor("test", "live", Clock(), publish, stream_factory=Stream,
                           detector_factory=lambda: combined)
    monitor.start()
    try:
        assert done.wait(2)
    finally:
        monitor.stop()
    assert worker.closed
    assert any(o.kind == "health" and o.health.code == "yamnet_unavailable" for o in observations)
    assert observations[-1].kind == "audio" and observations[-1].status == "ok"
    assert observations[-1].reasons == ["silero"]
    with MicrophoneLease():
        pass


def test_installed_models_together_on_paced_silence_without_microphone():
    from proctor.audio.assets import check as check_silero
    from proctor.audio.yamnet_assets import check as check_yamnet
    try:
        check_silero()
        check_yamnet()
    except (OSError, ValueError, RuntimeError):
        pytest.skip("explicit prepare --download required")
    combined = CombinedAudioDetector(config=AudioConfig())
    rows = []
    try:
        start = time.monotonic()
        for i in range(165):
            row = combined.feed(np.zeros(BLOCK, dtype=np.float32))
            if row is not None:
                rows.append(row)
            time.sleep(max(0, start + (i + 1) * BLOCK / RATE - time.monotonic()))
        determined = [row for row in rows if row.voice_like != "unknown"]
        assert determined and all(row.voice_like == "absent" for row in determined)
        assert all(row.health_code == "audio_ok" for row in determined)
        assert all(row.diagnostics["yamnet_speech_sum"] is not None for row in determined)
    finally:
        assert combined.close()
    assert not combined.worker._thread.is_alive()
