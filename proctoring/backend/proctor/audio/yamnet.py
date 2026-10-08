"""Local MediaPipe classifier. Only scalar scores and class names leave this boundary."""
from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np

from .yamnet_assets import SPEECH_CLASSES, check

RATE = 16000
WINDOW_SAMPLES = 15600


@dataclass(frozen=True)
class SpeechScore:
    speech_sum: float
    top_class: str
    top_score: float


def speech_score(scores: dict[str, float]) -> SpeechScore:
    """Sum only the six explicitly allowed labels; never rank music/noise as speech."""
    speech = {name: float(scores.get(name, 0.0)) for name in SPEECH_CLASSES}
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in speech.values()):
        raise ValueError("invalid YAMNet speech score")
    top_class = max(speech, key=speech.get)
    # Overlapping labels can sum above one. The unchanged contract's Unit is in [0,1].
    return SpeechScore(min(1.0, sum(speech.values())), top_class, speech[top_class])


class YamnetClassifier:
    def __init__(self, directory: Path | None = None):
        path = check(directory)
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.audio import AudioClassifier, AudioClassifierOptions, RunningMode
        from mediapipe.tasks.python.components.containers import AudioData
        self._audio_data = AudioData
        self._classifier = AudioClassifier.create_from_options(AudioClassifierOptions(
            base_options=BaseOptions(model_asset_path=str(path)), running_mode=RunningMode.AUDIO_CLIPS,
            max_results=-1, score_threshold=0.0))

    def classify(self, waveform: np.ndarray) -> SpeechScore:
        waveform = np.asarray(waveform, dtype=np.float32).reshape(-1)
        if waveform.size != WINDOW_SAMPLES or not np.all(np.isfinite(waveform)):
            raise ValueError("expected 15600 finite mono samples")
        results = self._classifier.classify(self._audio_data.create_from_array(np.clip(waveform, -1, 1), RATE))
        if len(results) != 1 or len(results[0].classifications) != 1:
            raise ValueError("unexpected YAMNet output windows/heads")
        scores = {category.category_name: category.score for category in results[0].classifications[0].categories}
        if len(scores) != 521 or not SPEECH_CLASSES.keys() <= scores.keys():
            raise ValueError("YAMNet class map mismatch")
        return speech_score(scores)

    def close(self):
        self._classifier.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
