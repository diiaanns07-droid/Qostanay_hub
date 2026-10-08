"""A02 capture tools: ``python -m proctor.capture <command>`` (run from proctoring/).

    soak          long-run measurement (synthetic | replay:<id> | live[:index]) -> JSON report
    verify-live   LIVE camera checklist for the target machine (Windows), optional --interactive steps
    record        record a consented clip into the replay format (media outside Git)
    replay-check  validate a replay manifest + decode all frames, print a summary
    replay-hash   sha256 of a media file (or the digest of an image-sequence directory)

Settings come from QORGAU_* environment variables (proctor.settings.Settings) plus the flags.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from proctor.settings import Settings


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings.from_env()
    if getattr(args, "replay_dir", None):
        s = replace(s, replay_dir=Path(args.replay_dir).expanduser())
    return s


def _cmd_soak(args: argparse.Namespace) -> int:
    from .measure import SimulatedConsumer, parse_source, run_soak, summarize, write_report

    settings = _settings(args)
    source = parse_source(args.source, args.width, args.height, args.fps)
    consumers = None
    if args.consumers:
        consumers = []
        for spec in args.consumers.split(","):
            name, fps, work = spec.split(":")
            consumers.append(SimulatedConsumer(name, float(fps) or None, float(work), mode=args.work))
    seconds = args.minutes * 60.0 if args.minutes else args.seconds

    def progress(sample: dict) -> None:
        if not args.quiet and int(sample["t_s"]) % 10 == 0:
            print(f"t={sample['t_s']:7.1f}s fps={sample['capture_fps']:5.1f} frames={sample['frames_captured']} rss={sample['rss_mb']} MB health={sample['health']}", flush=True)

    report = run_soak(settings, source, seconds, consumers=consumers, progress=progress)
    print(summarize(report))
    out = Path(args.out) if args.out else None
    if write_report(report, out):
        print(f"report: {out}")
    return 0 if "FAIL" not in report["evaluation"].values() else 1


def _cmd_verify_live(args: argparse.Namespace) -> int:
    from .verify_live import verify_live, write

    settings = _settings(args)
    if args.camera is None:
        args.camera = settings.camera_index
    report = verify_live(settings, args)
    out = Path(args.out) if args.out else Path(settings.data_dir) / "a02-live" / "verify_live_report.json"
    print(f"report: {write(report, out)}")
    return 0 if report.get("overall") == "PASS" else 1


def _cmd_record(args: argparse.Namespace) -> int:
    from .measure import parse_source
    from .record import record

    settings = _settings(args)
    source = parse_source(args.source, args.width, args.height, args.fps)
    result = record(
        settings,
        source,
        args.replay_id,
        args.seconds,
        consent=args.consent,
        title=args.title,
        overwrite=args.overwrite,
        countdown_s=args.countdown,
        tick=lambda msg: print(msg, flush=True),
    )
    print(json.dumps(result, indent=2))
    print("Media stays outside Git. Add labels to the manifest before using it for evaluation.")
    return 0


def _cmd_replay_check(args: argparse.Namespace) -> int:
    from .replay import ReplaySource, load_replay
    from .sources import SourceEnded

    settings = _settings(args)
    resolved = load_replay(settings.replay_dir, args.replay_id, verify_sha256=not args.no_sha)
    src = ReplaySource(settings.replay_dir, args.replay_id, max_width=7680, max_height=4320, pacing="lockstep")
    import threading
    import time

    src.open(threading.Event(), time.monotonic() + 10)
    pts, indices, size = [], [], None
    try:
        while True:
            raw = src.read()
            if raw is None:
                continue
            pts.append(raw.media_pts_ms)
            indices.append(raw.media_index)
            size = raw.image.shape[1::-1]
            if src.manifest.loop and src.loops >= 1:
                break
    except SourceEnded:
        pass
    finally:
        src.close()
    m = resolved.manifest
    gaps = [b - a for a, b in zip(pts, pts[1:])]
    summary = {
        "replay_id": m.replay_id,
        "title": m.title,
        "media": m.media.model_dump(),
        "pacing": m.pacing,
        "loop": m.loop,
        "frames": len(pts),
        "undecodable_dropped": src.dropped,
        "timestamp_repairs": src.timestamp_repairs,
        "duration_ms": round(pts[-1], 3) if pts else None,
        "frame_interval_ms": {"min": round(min(gaps), 3), "max": round(max(gaps), 3)} if gaps else None,
        "frame_size": list(size) if size else None,
        "labels": [l.model_dump() for l in m.labels],
        "sha256_verified": bool(m.media.sha256) and not args.no_sha,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if pts else 1


def _cmd_replay_hash(args: argparse.Namespace) -> int:
    from .replay import list_sequence, sha256_file, sha256_sequence

    p = Path(args.path)
    print(sha256_sequence(list_sequence(p)) if p.is_dir() else sha256_file(p))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proctor.capture", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def size_args(p: argparse.ArgumentParser, fps: int = 30) -> None:
        p.add_argument("--width", type=int, default=640)
        p.add_argument("--height", type=int, default=480)
        p.add_argument("--fps", type=int, default=fps)
        p.add_argument("--replay-dir", help="override QORGAU_REPLAY_DIR")

    p = sub.add_parser("soak", help="long-run measurement")
    p.add_argument("--source", default="synthetic", help="synthetic | replay:<id> | live | live:<index>")
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--minutes", type=float, default=None)
    p.add_argument("--consumers", help="name:max_fps:work_ms[,...] (max_fps 0 = unlimited); default phone_sim:8:60,attention_sim:15:25")
    p.add_argument("--work", choices=["cpu", "sleep"], default="cpu")
    p.add_argument("--out", help="JSON report path (keep outside the repo)")
    p.add_argument("--quiet", action="store_true")
    size_args(p)
    p.set_defaults(func=_cmd_soak)

    p = sub.add_parser("verify-live", help="LIVE camera checklist (target machine)")
    p.add_argument("--camera", type=int, default=None)
    p.add_argument("--backend", choices=sorted(__import__("proctor.capture.verify_live", fromlist=["BACKENDS"]).BACKENDS), default="auto")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--max-index", type=int, default=3)
    p.add_argument("--interactive", action="store_true", help="mirror / unplug / busy / privacy steps with prompts")
    p.add_argument("--snapshot", action="store_true", help="save one camera frame next to the report (may show a face; keep local)")
    p.add_argument("--out")
    size_args(p)
    p.set_defaults(func=_cmd_verify_live)

    p = sub.add_parser("record", help="record a consented replay clip")
    p.add_argument("--replay-id", required=True)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--consent", required=True, help="who is in the clip and how they agreed")
    p.add_argument("--title", default="")
    p.add_argument("--source", default="live", help="live | live:<index> | synthetic (tests)")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--countdown", type=int, default=3, help="seconds between camera open and REC t = 0 (0 = none)")
    size_args(p)
    p.set_defaults(func=_cmd_record)

    p = sub.add_parser("replay-check", help="validate and decode a replay")
    p.add_argument("replay_id")
    p.add_argument("--replay-dir")
    p.add_argument("--no-sha", action="store_true")
    p.set_defaults(func=_cmd_replay_check)

    p = sub.add_parser("replay-hash", help="sha256 for a media file or image directory")
    p.add_argument("path")
    p.set_defaults(func=_cmd_replay_hash)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
