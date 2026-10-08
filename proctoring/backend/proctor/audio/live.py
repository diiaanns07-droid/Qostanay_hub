"""Operator-assisted 20/20/20 s LIVE check. Output contains features, never PCM."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import threading
import time

from .monitor import AudioMonitor
from .combined import AudioConfig


def phase_report(name, start, end, rows, diagnostic_rows):
    states = dict(Counter(o["voice_like"] for o in rows))
    probabilities = [o["voice_probability"] for o in rows if o["voice_probability"] is not None]
    fractions = {}
    for detector in ("silero", "yamnet", "energy", "final"):
        key = f"{detector}_present"
        count = sum(d.get(key) is True for d in diagnostic_rows)
        fractions[detector] = dict(present=count, windows=len(diagnostic_rows),
                                  fraction=count / len(diagnostic_rows) if diagnostic_rows else None)
    present_fraction = states.get("present", 0) / len(rows) if rows else None
    # Thresholds alone never prove the acoustic conditions: operator confirmation remains separate.
    criterion = None
    enough = len(rows) >= 36 and states.get("unknown", 0) == 0
    if name == "silence" and present_fraction is not None:
        criterion = enough and present_fraction <= 0.1
    elif name == "speech_nearby" and present_fraction is not None:
        criterion = enough and present_fraction >= 0.7
    return dict(phase=name, start_ms=start, end_ms=end, observations=len(rows), states=states,
        present_fraction=present_fraction, numeric_criterion_met=criterion,
        acoustic_conditions="operator confirmation pending", detectors=fractions,
        yamnet_only_present=sum(d.get("yamnet_present") is True and d.get("silero_present") is False for d in diagnostic_rows),
        yamnet_classes=dict(Counter(d["yamnet_class"] for d in diagnostic_rows if d.get("yamnet_present"))),
        probability_mean=sum(probabilities) / len(probabilities) if probabilities else None,
        probability_max=max(probabilities) if probabilities else None)


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
    diagnostics = []
    guard = threading.Lock()

    def publish(obs):
        with guard:
            observations.append(obs.model_dump(mode="json"))

    def diagnostic(row):
        with guard:
            diagnostics.append(row)

    monitor = AudioMonitor("a14-live", "live", clock, publish, diagnostics_callback=diagnostic)
    phases = []
    monitor.start()
    try:
        sequence = (("calibration", 5), (args.phase, 20)) if args.phase else (
            ("calibration", 5), ("silence", 20), ("prepare_speech", 5),
            ("speech_nearby", 20), ("prepare_whisper_1m", 5), ("whisper_1m", 20))
        for name, duration in sequence:
            start = clock.now_ms()
            instructions = {
                "calibration": "Тишина: идёт калибровка шума",
                "silence": "ТИШИНА: не говорите и не нажимайте клавиши",
                "prepare_speech": "Приготовьтесь говорить обычным голосом с расстояния 1 м",
                "speech_nearby": "ГОВОРИТЕ обычным голосом с расстояния 1 м",
                "prepare_whisper_1m": "Приготовьтесь шептать с расстояния 1 м",
                "whisper_1m": "ШЕПЧИТЕ с расстояния 1 м",
            }
            print(f"PHASE {name}: {duration}s — {instructions[name]}", flush=True)
            time.sleep(duration)
            end = clock.now_ms()
            with guard:
                rows = [o for o in observations if o["kind"] == "audio" and start <= o["t_session_ms"] < end]
                diagnostic_rows = [d for d in diagnostics if start <= d["t_session_ms"] < end]
            phase = phase_report(name, start, end, rows, diagnostic_rows)
            phases.append(phase)
            print(json.dumps(phase), flush=True)
    finally:
        monitor.stop()
    config = AudioConfig.from_env()
    report = dict(created_at=clock.wall.isoformat(), conditions="operator confirmation pending",
                  platform=platform.system(), os_release=platform.release(), os_version=platform.version(),
                  thresholds=dict(silero=config.silero_threshold, yamnet=config.yamnet_threshold),
                  audio_recorded=False, phases=phases, observations=observations, diagnostics=diagnostics)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE: features only -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
