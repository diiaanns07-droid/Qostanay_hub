"""32 ms local VAD and half-second scalar summaries. No audio I/O."""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .assets import check

RATE = 16000
BLOCK = 512


class SileroVad:
    def __init__(self, path: Path):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, block: np.ndarray) -> float:
        value = np.concatenate((self.context, block.reshape(1, BLOCK)), axis=1)
        output, state = self.session.run(["output", "stateN"],
                                        {"input": value, "state": self.state, "sr": np.array(RATE, dtype=np.int64)})
        probability = float(output[0, 0])
        if not np.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("invalid VAD output")
        self.state = state
        self.context = value[:, -64:].copy()
        return probability


def dbfs(power: float) -> float:
    return float(np.clip(10 * np.log10(max(power, 1e-20)), -200, 0))


def energy_probability(block: np.ndarray, floor: float) -> float:
    power = float(np.mean(block.astype(np.float64) ** 2))
    spectrum = np.abs(np.fft.rfft((block - block.mean()) * np.hanning(BLOCK))) ** 2
    frequencies = np.fft.rfftfreq(BLOCK, 1 / RATE)
    band = float(spectrum[(frequencies >= 300) & (frequencies <= 3400)].sum() / max(spectrum.sum(), 1e-20))
    # Conservative broad-band gate: a pure speech-band tone can still trigger this fallback.
    above = float(np.clip((dbfs(power) - max(floor + 6, -65)) / 12, 0, 1))
    return above * float(np.clip((band - 0.45) / 0.35, 0, 1))


@dataclass(frozen=True)
class Summary:
    voice_like: str
    probability: float | None
    rms_dbfs: float
    noise_floor_dbfs: float
    reasons: list[str]


class AudioDetector:
    def __init__(self, directory: Path | None = None):
        self.vad = None
        try:
            self.vad = SileroVad(check(directory))
        except Exception:
            # Missing/corrupt model or unavailable ONNX runtime: never download here.
            pass
        self.reset()

    def reset(self):
        self.samples = self.next_emit = 0
        self.next_emit = RATE // 2
        self.calibration: list[float] = []
        self.floor = -200.0
        self.window: list[tuple[float | None, float]] = []
        if self.vad is not None:
            self.vad.reset()

    def feed(self, block: np.ndarray) -> Summary | None:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size != BLOCK or not np.all(np.isfinite(block)):
            raise ValueError("expected 512 finite mono samples")
        block = np.clip(block, -1, 1)
        power = float(np.mean(block.astype(np.float64) ** 2))
        calibrating = self.samples < 3 * RATE
        self.samples += BLOCK
        if calibrating:
            self.calibration.append(dbfs(power))
            self.floor = float(np.percentile(self.calibration, 20))
        probability = None
        if not calibrating:
            if self.vad is not None:
                try:
                    probability = self.vad.probability(block)
                except Exception:
                    self.vad = None
            if self.vad is None:
                probability = energy_probability(block, self.floor)
        self.window.append((probability, power))
        if self.samples < self.next_emit:
            return None
        self.next_emit += RATE // 2
        probabilities = [p for p, _ in self.window if p is not None]
        unknown = len(probabilities) != len(self.window)
        present = sum(p >= 0.5 for p in probabilities) > len(self.window) / 2
        reasons = ["energy_fallback"] if self.vad is None else []
        if unknown:
            reasons.append("noise_calibration")
        result = Summary("unknown" if unknown else "present" if present else "absent",
                         None if unknown else float(np.mean(probabilities)),
                         dbfs(float(np.mean([p for _, p in self.window]))), self.floor, reasons)
        self.window.clear()
        return result
