"""One classifier thread, one pending PCM window, scalar results. No microphone or file I/O."""
from dataclasses import dataclass
import threading
import time

import numpy as np

from .yamnet import SpeechScore, WINDOW_SAMPLES


@dataclass(frozen=True)
class WindowScore:
    end_sample: int
    generation: int
    score: SpeechScore
    elapsed_ms: float


class YamnetWorker:
    def __init__(self, classifier_factory):
        self._factory = classifier_factory
        self._condition = threading.Condition()
        self._pending = self._result = None
        self._generation = 0
        self._stop = False
        self._state = "starting"
        self.replaced_windows = 0
        self._thread = threading.Thread(target=self._run, name="audio-yamnet", daemon=True)
        self._thread.start()

    @property
    def state(self):
        with self._condition:
            return self._state

    def submit(self, waveform: np.ndarray, end_sample: int):
        if waveform.shape != (WINDOW_SAMPLES,):
            raise ValueError("invalid YAMNet window")
        # This is called by the monitor worker, never by the microphone callback.
        copied = waveform.copy()
        with self._condition:
            if self._stop or self._state == "unavailable":
                return
            if self._pending is not None:
                self.replaced_windows += 1
            self._pending = (copied, end_sample, self._generation)
            self._condition.notify()

    def latest(self, now_sample: int, max_age_samples: int) -> WindowScore | None:
        with self._condition:
            result = self._result
            if (self._stop or result is None or result.generation != self._generation
                    or not 0 <= now_sample - result.end_sample <= max_age_samples):
                return None
            return result

    def reset(self):
        with self._condition:
            self._generation += 1
            self._pending = self._result = None

    def close(self):
        with self._condition:
            self._stop = True
            self._pending = self._result = None
            self._condition.notify_all()
        self._thread.join(2)
        return not self._thread.is_alive()

    def _run(self):
        classifier = None
        try:
            classifier = self._factory()
            with self._condition:
                self._state = "ready"
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stop or self._pending is not None)
                    if self._stop:
                        return
                    waveform, end_sample, generation = self._pending
                    self._pending = None
                start = time.perf_counter()
                score = classifier.classify(waveform)
                elapsed = (time.perf_counter() - start) * 1000
                waveform = None  # retain no completed PCM window
                with self._condition:
                    if not self._stop and generation == self._generation:
                        self._result = WindowScore(end_sample, generation, score, elapsed)
        except Exception:
            with self._condition:
                self._state = "unavailable"
                self._pending = self._result = None
        finally:
            if classifier is not None:
                try:
                    classifier.close()
                except Exception:
                    pass
            with self._condition:
                if self._stop:
                    self._state = "stopped"
                self._pending = None
