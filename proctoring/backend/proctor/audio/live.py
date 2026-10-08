"""Operator-assisted 20/20/20 s LIVE check. Output contains features, never PCM."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time

from .monitor import AudioMonitor


class LiveClock:
    def __init__(self):
        self.start = time.monotonic()
        self.wall = datetime.now(timezone.utc)

    def now_ms(self):
        return (time.monotonic() - self.start) * 1000

    def wall_at(self, t):
        return self.wall + timedelta(milliseconds=t)


def main():
    parser = argparse.ArgumentParser(description="LIVE audio features; no recording")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--countdown", type=int, default=10)
    parser.add_argument("--phase", choices=["silence", "speech_nearby", "whisper_1m"])
    args = parser.parse_args()
    print(f"START IN {args.countdown}s: stay quiet for calibration and silence phase", flush=True)
    time.sleep(max(0, args.countdown))
    clock = LiveClock()
    observations = []
    guard = threading.Lock()

    def publish(obs):
        with guard:
            observations.append(obs.model_dump(mode="json"))

    monitor = AudioMonitor("a14-live", "live", clock, publish)
    phases = []
    monitor.start()
    try:
        sequence = (("calibration", 5), (args.phase, 20)) if args.phase else (
            ("calibration", 5), ("silence", 20), ("prepare_speech", 5),
            ("speech_nearby", 20), ("prepare_whisper_1m", 5), ("whisper_1m", 20))
        for name, duration in sequence:
            start = clock.now_ms()
            print(f"PHASE {name}: {duration}s", flush=True)
            time.sleep(duration)
            end = clock.now_ms()
            with guard:
                rows = [o for o in observations if o["kind"] == "audio" and start <= o["t_session_ms"] < end]
            probabilities = [o["voice_probability"] for o in rows if o["voice_probability"] is not None]
            phase = dict(phase=name, start_ms=start, end_ms=end, observations=len(rows),
                         states=dict(Counter(o["voice_like"] for o in rows)),
                         probability_mean=sum(probabilities) / len(probabilities) if probabilities else None,
                         probability_max=max(probabilities) if probabilities else None)
            phases.append(phase)
            print(json.dumps(phase), flush=True)
    finally:
        monitor.stop()
    report = dict(created_at=clock.wall.isoformat(), conditions="operator confirmation pending",
                  audio_recorded=False, phases=phases, observations=observations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE: features only -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
