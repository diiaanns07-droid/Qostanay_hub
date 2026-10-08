"""Event/clip-level evaluation of the full phone analyzer on labelled frame sequences.

    python -m proctor.phone.eval.clips --manifest <clips.json> --out <report.json> [--split test]

* A clip is a directory of frame IMAGES (sorted by file name) + fps, extracted beforehand (A02 replay
  tooling or ffmpeg). This tool never opens a camera or a video container.
* Each clip runs through the real PhoneAnalyzer (verified local model, detector + tracker + signals) as a
  REPLAY session; per-frame signal states become predicted intervals (runs of "present", gaps <=
  merge_gap_ms merged, runs shorter than min_event_ms dropped).
* Matching (metrics.match_events): same signal, temporal overlap >= min_overlap_ms after widening the
  labelled event by tolerance_ms. Reported per split and signal: TP/FP/FN, event precision/recall,
  clip-level hit rate on positive clips and false-alarm rate on negative clips — always with
  denominators and the list of misses.
* Splits: "tune" (threshold selection) and "test" (final numbers). A subject_group (person/recording
  session) present in both splits, or a clip_id listed twice, is reported as a leak and fails the run.

Manifest format: see eval/clip_manifest.example.json and EVAL_PLAN.md. Media stays outside Git.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from proctor.settings import Settings
from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import FramePacketMeta, PhoneSignalName, SignalState, SourceMode

from ..analyzer import PhoneAnalyzer
from ..config import PhoneConfig
from .metrics import Interval, match_events, ratio, runs_to_intervals

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
SIGNALS = [s.value for s in PhoneSignalName]
DEFAULTS = {"merge_gap_ms": 500.0, "min_event_ms": 250.0, "min_overlap_ms": 200.0, "tolerance_ms": 500.0}


class ManifestError(ValueError):
    pass


def load_clip_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    clips = data.get("clips")
    if not isinstance(clips, list):
        raise ManifestError("manifest needs a 'clips' list")
    seen: set[str] = set()
    groups: dict[str, set[str]] = {}
    for clip in clips:
        for key in ("clip_id", "split", "frames_dir", "fps", "events"):
            if key not in clip:
                raise ManifestError(f"clip {clip.get('clip_id', '?')}: missing '{key}'")
        if clip["split"] not in ("tune", "test"):
            raise ManifestError(f"clip {clip['clip_id']}: split must be 'tune' or 'test'")
        if clip["clip_id"] in seen:
            raise ManifestError(f"clip_id {clip['clip_id']} listed twice")
        seen.add(clip["clip_id"])
        if not (0 < float(clip["fps"]) <= 120):
            raise ManifestError(f"clip {clip['clip_id']}: fps out of range")
        for ev in clip["events"]:
            if ev.get("signal") not in SIGNALS or float(ev["t_end_ms"]) <= float(ev["t_start_ms"]):
                raise ManifestError(f"clip {clip['clip_id']}: bad event {ev}")
        group = clip.get("subject_group")
        if group:
            groups.setdefault(group, set()).add(clip["split"])
    leaks = sorted(g for g, splits in groups.items() if len(splits) > 1)
    if leaks:
        raise ManifestError(f"subject_group(s) in both tune and test (split leak): {leaks}")
    return data


def run_clip(analyzer: PhoneAnalyzer, clip: dict, base_dir: Path, cv2) -> tuple[dict[str, list[tuple[float, bool]]], dict]:
    frames_dir = Path(clip["frames_dir"])
    if not frames_dir.is_absolute():
        frames_dir = base_dir / frames_dir
    files = sorted(p for p in frames_dir.iterdir() if p.suffix.lower() in IMAGE_EXT)
    frame_ms = 1000.0 / float(clip["fps"])
    session = f"eval-{clip['clip_id']}"[:128]
    analyzer.start_session(session, SourceMode.REPLAY)
    samples: dict[str, list[tuple[float, bool]]] = {s: [] for s in SIGNALS}
    stats = {"frames": 0, "unreadable": 0, "error_observations": 0, "unknown": {s: 0 for s in SIGNALS}}
    wall0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, path in enumerate(files):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            stats["unreadable"] += 1
            continue
        image.flags.writeable = False
        t = i * frame_ms
        meta = FramePacketMeta(
            session_id=session,
            frame_id=i,
            t_session_ms=t,
            wall_time=wall0 + timedelta(milliseconds=t),
            t_capture_mono_ns=time.monotonic_ns(),
            width=image.shape[1],
            height=image.shape[0],
            source_mode=SourceMode.REPLAY,
            source_id=f"replay:{clip['clip_id']}"[:128],
        )
        for obs in analyzer.process(FramePacket(meta=meta, image=image)):
            stats["frames"] += 1
            stats["error_observations"] += int(obs.status.value == "error")
            for s in obs.signals:
                samples[s.name.value].append((t, s.state == SignalState.PRESENT))
                if s.state in (SignalState.UNKNOWN, SignalState.INSUFFICIENT_EVIDENCE):
                    stats["unknown"][s.name.value] += 1
    analyzer.end_session()
    return samples, stats


def evaluate(manifest: dict, base_dir: Path, analyzer: PhoneAnalyzer, cv2, split: str | None) -> dict:
    params = {**DEFAULTS, **manifest.get("matching", {})}
    clips = [c for c in manifest["clips"] if split is None or c["split"] == split]
    per = {(sp, s): {"tp": 0, "fp": 0, "fn": 0, "pred": 0, "labelled": 0, "pos_clips": 0, "pos_clips_hit": 0, "neg_clips": 0, "neg_clips_alarm": 0, "misses": [], "false_alarms": []} for sp in ("tune", "test") for s in SIGNALS}
    clip_stats = {}
    for clip in clips:
        samples, stats = run_clip(analyzer, clip, base_dir, cv2)
        clip_stats[clip["clip_id"]] = stats
        frame_ms = 1000.0 / float(clip["fps"])
        for s in SIGNALS:
            pred = runs_to_intervals(samples[s], params["merge_gap_ms"], params["min_event_ms"], frame_ms)
            lab = [Interval(float(e["t_start_ms"]), float(e["t_end_ms"])) for e in clip["events"] if e["signal"] == s]
            pm, lm = match_events(pred, lab, params["min_overlap_ms"], params["tolerance_ms"])
            row = per[(clip["split"], s)]
            row["pred"] += len(pred)
            row["labelled"] += len(lab)
            row["tp"] += sum(lm)
            row["fn"] += len(lab) - sum(lm)
            row["fp"] += len(pred) - sum(pm)
            row["misses"] += [f"{clip['clip_id']}@{iv.start_ms:.0f}-{iv.end_ms:.0f}" for iv, m in zip(lab, lm) if not m]
            row["false_alarms"] += [f"{clip['clip_id']}@{iv.start_ms:.0f}-{iv.end_ms:.0f}" for iv, m in zip(pred, pm) if not m]
            if lab:
                row["pos_clips"] += 1
                row["pos_clips_hit"] += int(any(lm))
            else:
                row["neg_clips"] += 1
                row["neg_clips_alarm"] += int(bool(pred))
    results = []
    for (sp, s), row in per.items():
        if row["labelled"] == 0 and row["neg_clips"] == 0:
            continue
        matched_pred = row["pred"] - row["fp"]
        results.append(
            {
                "split": sp,
                "signal": s,
                "event_precision": ratio(matched_pred, row["pred"]),
                "event_precision_frac": f"{matched_pred}/{row['pred']}",
                "event_recall": ratio(row["tp"], row["labelled"]),
                "event_recall_frac": f"{row['tp']}/{row['labelled']}",
                "positive_clips_hit": f"{row['pos_clips_hit']}/{row['pos_clips']}",
                "negative_clips_with_false_alarm": f"{row['neg_clips_alarm']}/{row['neg_clips']}",
                "misses": row["misses"][:50],
                "false_alarms": row["false_alarms"][:50],
            }
        )
    return {
        "tool": "proctor.phone.eval.clips",
        "dataset_id": manifest.get("dataset_id", "unnamed"),
        "split_filter": split,
        "clips_evaluated": len(clips),
        "matching": params,
        "config_version": analyzer.config.config_version,
        "results": results,
        "clip_stats": clip_stats,
        "caveats": [
            "Small, self-recorded samples: no production accuracy claim.",
            "possible_screen_capture can be at most a pattern match; the phone camera direction is not observable.",
            "Thresholds must be chosen on the 'tune' split only; report 'test' numbers.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proctor.phone.eval.clips", description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--split", choices=("tune", "test"), default=None)
    ap.add_argument("--models-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    try:
        manifest = load_clip_manifest(args.manifest)
    except (ManifestError, ValueError, KeyError) as exc:
        print(f"[manifest_invalid] {exc}")
        return 2
    import cv2  # noqa: PLC0415

    settings = Settings.from_env()
    if args.models_dir is not None:
        settings = replace(settings, models_dir=args.models_dir)
    analyzer = PhoneAnalyzer(settings, PhoneConfig.from_env())
    health = analyzer.load()
    if health.status.value != "ok":
        print(f"[{health.code}] {health.message}")
        return 1
    report = evaluate(manifest, args.manifest.parent, analyzer, cv2, args.split)
    analyzer.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for row in report["results"]:
        print(
            f"[{row['split']}] {row['signal']}: event P={row['event_precision_frac']} R={row['event_recall_frac']} "
            f"pos clips hit {row['positive_clips_hit']}  neg clips w/ alarm {row['negative_clips_with_false_alarm']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
