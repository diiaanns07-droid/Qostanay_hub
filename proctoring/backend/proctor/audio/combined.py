"""Silero (or its existing energy fallback) OR asynchronous YAMNet. No audio I/O."""
from dataclasses import dataclass
import math
import os
from pathlib import Path

import numpy as np

from .vad import AudioDetector, Summary, BLOCK, RATE
from .yamnet import YamnetClassifier, WINDOW_SAMPLES
from .yamnet_assets import SPEECH_CLASSES
from .yamnet_worker import YamnetWorker


@dataclass(frozen=True)
class AudioConfig:
    silero_threshold: float = 0.5
    yamnet_threshold: float = 0.3
    yamnet_hop_s: float = 0.512
    yamnet_max_age_s: float = 1.25

    def __post_init__(self):
        for value in (self.silero_threshold, self.yamnet_threshold):
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError("audio thresholds must be finite and in (0,1]")
        if not 0.5 <= self.yamnet_hop_s <= 1.0:
            raise ValueError("YAMNet hop must be 0.5..1 seconds")
        if not self.yamnet_hop_s <= self.yamnet_max_age_s <= 2.0:
            raise ValueError("invalid YAMNet result age")

    @classmethod
    def from_env(cls):
        return cls(yamnet_threshold=float(os.environ.get("QORGAU_AUDIO_YAMNET_THRESHOLD", "0.3")))


@dataclass(frozen=True)
class CombinedSummary(Summary):
    health_code: str
    observation_degraded: bool
    diagnostics: dict


class CombinedAudioDetector:
    def __init__(self, directory: Path | None = None, *, config: AudioConfig | None = None,
                 primary_factory=AudioDetector, worker_factory=YamnetWorker):
        self.config = config if config is not None else AudioConfig.from_env()
        self.primary = primary_factory(directory)
        self.worker = worker_factory(lambda: YamnetClassifier(directory))
        self._clear_window()

    @property
    def vad(self):
        return self.primary.vad

    def _clear_window(self):
        self.samples = 0
        self.next_submit = WINDOW_SAMPLES
        self.waveform = np.empty(0, dtype=np.float32)

    def reset(self):
        self.primary.reset()
        self.worker.reset()  # discard pending and in-flight results from before the discontinuity
        self._clear_window()

    def close(self):
        self._clear_window()
        return self.worker.close()

    def feed(self, block: np.ndarray) -> CombinedSummary | None:
        base = self.primary.feed(block)  # validates the 512 finite samples and preserves energy fallback
        self.samples += BLOCK
        block = np.clip(np.asarray(block, dtype=np.float32).reshape(-1), -1, 1)
        self.waveform = np.concatenate((self.waveform, block))[-WINDOW_SAMPLES:].copy()
        if self.samples >= self.next_submit:
            self.worker.submit(self.waveform, self.samples)
            self.next_submit += round(self.config.yamnet_hop_s * RATE)
        if base is None:
            return None
        result = self.worker.latest(self.samples, round(self.config.yamnet_max_age_s * RATE))
        silero = base.probability if self.vad is not None else None
        yamnet = result.score.speech_sum if result is not None else None
        calibrated = base.voice_like != "unknown"
        silero_present = calibrated and silero is not None and silero >= self.config.silero_threshold
        energy_present = calibrated and self.vad is None and base.voice_like == "present"
        yamnet_present = calibrated and yamnet is not None and yamnet >= self.config.yamnet_threshold
        present = silero_present or energy_present or yamnet_present
        reasons = []
        if silero_present:
            reasons.append("silero")
        if yamnet_present:
            reasons.append("yamnet." + SPEECH_CLASSES[result.score.top_class])
        if self.vad is None:
            reasons.append("energy_fallback")
        if not calibrated:
            reasons.append("noise_calibration")
        if not calibrated:
            health = "noise_calibration"
        elif self.worker.state in ("unavailable", "stopped"):
            health = "yamnet_unavailable"
        elif result is None:
            health = "yamnet_starting" if self.worker.state == "starting" else "yamnet_stale"
        else:
            health = "audio_ok" if self.vad is not None else "energy_fallback"
        # A missing auxiliary model is a health limitation, not invalid Silero evidence.
        # When only energy supports a window, preserve the old degraded + energy_fallback contract
        # accepted by the existing A14 fusion adapter. No A05 rule changes are needed.
        degraded = not calibrated or (self.vad is None and not yamnet_present)
        diagnostics = dict(silero_probability=silero, yamnet_speech_sum=yamnet,
            yamnet_class=result.score.top_class if result is not None else None,
            silero_present=bool(silero_present), yamnet_present=bool(yamnet_present),
            energy_present=bool(energy_present), final_present=bool(present) if calibrated else None,
            yamnet_age_ms=(self.samples - result.end_sample) / RATE * 1000 if result else None,
            yamnet_inference_ms=result.elapsed_ms if result else None)
        return CombinedSummary("unknown" if not calibrated else "present" if present else "absent",
            max(base.probability or 0.0, yamnet or 0.0) if calibrated else None,
            base.rms_dbfs, base.noise_floor_dbfs, reasons, health, degraded, diagnostics)
