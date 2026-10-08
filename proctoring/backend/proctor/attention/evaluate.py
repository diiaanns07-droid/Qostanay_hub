"""Offline evaluation of the attention analyzer on labelled frame folders (no camera, no video I/O).

    python -m proctor.attention.evaluate --frames DIR --labels labels.json [--fps 15] [--out result.json]

DIR holds the frames of ONE recording as image files (sorted by name; e.g. exported by A02 replay
tooling - this module never opens video). labels.json:
    {"person": "p01", "session": "s1", "notes": "glasses, evening light",
     "calibration": {"center": [0, 40], "left": [45, 80], "right": [85, 120], "up": [125, 160], "down": [165, 200]},
     "segments": [{"start": 210, "end": 400, "gaze": "center", "faces": 1, "label": "reading long question"},
                  {"start": 401, "end": 430, "gaze": "down", "label": "brief keyboard glance"}]}
Frame ranges are inclusive frame indices. "calibration" is optional (omitted = uncalibrated run).
The output counts frames per (label, predicted) pair, unknown rates, face-count agreement and
non-center runs inside segments labelled "center" (candidate false alarms, with durations).
Results describe THIS recording only; keep tuning and evaluation recordings/people separate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2

from proctor.settings import Settings
from proctor_contracts.v1 import CalibrationTarget, SourceMode

from . import create_attention_analyzer
from .testing import make_frame

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def _frames(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT)


def evaluate(frames_dir: Path, labels: dict, fps: float = 15.0, settings: Settings | None = None) -> dict:
    files = _frames(frames_dir)
    if not files:
        raise SystemExit(f"no image frames in {frames_dir}")
    analyzer = create_attention_analyzer(settings or Settings.from_env())
    health = analyzer.load()
    if health.status.value != "ok":
        raise SystemExit(f"attention model unavailable: {health.code} {health.message}")
    dt = 1000.0 / fps
    session = "eval-" + str(labels.get("session", "s"))[:40]
    cal_ranges = {CalibrationTarget(k): tuple(v) for k, v in (labels.get("calibration") or {}).items()}
    target_at = {i: t for t, (a, b) in cal_ranges.items() for i in range(a, b + 1)}
    cal_end = max((b for _, b in cal_ranges.values()), default=-1)
    calibration_result = None
    observations = []
    analyzer.start_session(session, SourceMode.REPLAY)
    try:
        if cal_ranges:
            analyzer.calibration_start()
        current = None
        for i, path in enumerate(files):
            if cal_ranges and calibration_result is None:
                t = target_at.get(i)
                if t is not None and t != current:
                    analyzer.calibration_target(t)
                    current = t
                if i > cal_end:
                    calibration_result = analyzer.calibration_finish().model_dump(mode="json")
            img = cv2.imread(str(path))
            if img is None:
                continue
            observations.extend((i, o) for o in analyzer.process(make_frame(session, i, i * dt, img)))
        if cal_ranges and calibration_result is None:  # calibration ran until the last frame
            calibration_result = analyzer.calibration_finish().model_dump(mode="json")
    finally:
        analyzer.end_session()
        analyzer.close()
    by_frame = dict(observations)

    report: dict = {
        "person": labels.get("person"), "session": labels.get("session"), "notes": labels.get("notes"),
        "frames": len(files), "fps": fps, "config_version": None, "calibration": None,
        "segments": [],
        "caveat": "single recording; not an accuracy claim for other people, cameras or rooms",
    }
    if calibration_result is not None:
        report["calibration"] = {"phase": calibration_result["phase"], "message_code": calibration_result["message_code"],
                                 "targets": {t["target"]: [t["state"], t["message_code"]] for t in calibration_result["targets"]}}
    if by_frame:
        report["config_version"] = next(iter(by_frame.values())).producer.config_version
    totals: dict[str, Counter] = defaultdict(Counter)
    for seg in labels.get("segments", []):
        a, b = int(seg["start"]), int(seg["end"])
        obs = [by_frame[i] for i in range(a, b + 1) if i in by_frame]
        pred = Counter(o.gaze.direction.value if o.gaze else "unknown" for o in obs)
        head = Counter(o.head_direction.value for o in obs)
        faces = Counter(str(o.face_count) for o in obs)
        entry = {"label": seg.get("label"), "start": a, "end": b, "expected_gaze": seg.get("gaze"),
                 "expected_faces": seg.get("faces"), "gaze_pred": dict(pred), "head_pred": dict(head), "face_count": dict(faces)}
        if seg.get("gaze"):
            totals[seg["gaze"]].update(pred)
        if seg.get("gaze") == "center":  # candidate false alarms: non-center runs inside a "center" segment
            runs, run_start, run_dir = [], None, None
            for i in range(a, b + 2):
                o = by_frame.get(i)
                d = o.gaze.direction.value if (o is not None and o.gaze) else "unknown"
                active = d not in ("center", "unknown")
                if active and run_start is None:
                    run_start, run_dir = i, d
                elif (not active or d != run_dir) and run_start is not None:
                    runs.append({"direction": run_dir, "frames": i - run_start, "ms": round((i - run_start) * dt)})
                    run_start, run_dir = (i, d) if active else (None, None)
            entry["non_center_runs"] = runs
        report["segments"].append(entry)
    report["confusion_gaze"] = {k: dict(v) for k, v in totals.items()}
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frames", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    report = evaluate(args.frames, labels, args.fps)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
