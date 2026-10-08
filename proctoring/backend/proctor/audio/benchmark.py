"""Opt-in CPU latency check on generated waveforms, without a microphone or audio files."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import time

import numpy as np

from .yamnet import YamnetClassifier, WINDOW_SAMPLES
from .yamnet_assets import MODEL_SHA256, MODEL_URL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    if args.iterations < 20:
        parser.error("use at least 20 measured windows")
    rng = np.random.default_rng(14)
    signals = [np.zeros(WINDOW_SAMPLES, dtype=np.float32),
               rng.normal(0, 0.02, WINDOW_SAMPLES).astype(np.float32),
               (0.05 * np.sin(2 * np.pi * 1000 * np.arange(WINDOW_SAMPLES) / 16000)).astype(np.float32)]
    start = time.perf_counter()
    with YamnetClassifier(args.model_dir) as classifier:
        load_ms = (time.perf_counter() - start) * 1000
        timings = []
        for i in range(args.iterations + 5):
            start = time.perf_counter()
            classifier.classify(signals[i % len(signals)])
            timings.append((time.perf_counter() - start) * 1000)
    warm = np.array(timings[5:])
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), platform=platform.system(),
        os_release=platform.release(), os_version=platform.version(), processor=platform.processor(),
        python=platform.python_version(), mediapipe=importlib.metadata.version("mediapipe"),
        model_url=MODEL_URL, model_sha256=MODEL_SHA256, window_samples=WINDOW_SAMPLES,
        microphone_opened=False, audio_recorded=False, signal="generated silence/noise/tone (not accuracy fixtures)",
        scope="AudioData conversion + MediaPipe classify + class validation + speech sum, single caller thread",
        load_ms=load_ms, cold_ms=timings[0], warmup_windows=5, measured_windows=args.iterations,
        mean_ms=float(warm.mean()), p50_ms=float(np.percentile(warm, 50)), p95_ms=float(np.percentile(warm, 95)),
        max_ms=float(warm.max()), criterion="all measured warm windows <= 30 ms",
        passed=bool(warm.max() <= 30), measured_ms=warm.tolist())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "measured_ms"}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
