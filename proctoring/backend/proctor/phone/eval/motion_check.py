"""SEMI-SYNTHETIC pipeline check: real detector + tracker + signals on a moving crop of ONE real photo.

    python -m proctor.phone.eval.motion_check --image <photo.jpg> [--crop 0.6] [--fps 8] [--rise-s 1.0] [--hold-s 2.0]

A window of ``crop`` x image height slides DOWN over the photo for ``rise-s`` seconds (so everything in it,
including the phone, moves UP in the produced frames), then stays still for ``hold-s`` seconds. Every frame
is resized to 640x480 and fed to the real PhoneAnalyzer as a REPLAY session; per-frame states are printed.

What it shows: the real model's boxes drive track ids, phone_raised and possible_screen_capture end to end.
What it does NOT show: accuracy, real hand motion, motion blur, or anything about the exam webcam view.
The movement is synthetic (camera-tilt-like), only the pixels are real.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from proctor.settings import Settings
from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import FramePacketMeta, SourceMode

from ..analyzer import PhoneAnalyzer
from ..config import PhoneConfig


def frames(image, crop: float, n_rise: int, n_hold: int, cv2):
    h, w = image.shape[:2]
    win_h = int(round(h * crop))
    win_w = min(w, int(round(win_h * 4 / 3)))
    left = (w - win_w) // 2
    span = h - win_h
    for i in range(n_rise + n_hold):
        k = min(i, n_rise - 1) / max(1, n_rise - 1)
        top = int(round(span * k))
        out = cv2.resize(image[top : top + win_h, left : left + win_w], (640, 480), interpolation=cv2.INTER_AREA)
        out.flags.writeable = False
        yield out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proctor.phone.eval.motion_check", description=__doc__.splitlines()[0])
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--crop", type=float, default=0.6)
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--rise-s", type=float, default=1.0)
    ap.add_argument("--hold-s", type=float, default=2.0)
    ap.add_argument("--json", action="store_true", help="print one JSON line per frame")
    args = ap.parse_args(argv)
    import cv2  # noqa: PLC0415

    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        print(f"[error] cannot read {args.image}")
        return 1
    analyzer = PhoneAnalyzer(Settings.from_env(), PhoneConfig.from_env())
    health = analyzer.load()
    if health.code != "model_loaded":
        print(f"[{health.code}] {health.message}")
        return 1
    analyzer.start_session("motion-check", SourceMode.REPLAY)
    frame_ms = 1000.0 / args.fps
    wall0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    n_rise, n_hold = max(2, int(args.rise_s * args.fps)), int(args.hold_s * args.fps)
    print("SEMI-SYNTHETIC motion of a real photo — pipeline check, not accuracy")
    for i, img in enumerate(frames(image, args.crop, n_rise, n_hold, cv2)):
        t = i * frame_ms
        meta = FramePacketMeta(
            session_id="motion-check", frame_id=i, t_session_ms=t, wall_time=wall0 + timedelta(milliseconds=t),
            t_capture_mono_ns=time.monotonic_ns(), width=640, height=480, source_mode=SourceMode.REPLAY, source_id="replay:motion-check",
        )
        for obs in analyzer.process(FramePacket(meta=meta, image=img)):
            if args.json:
                print(obs.model_dump_json())
                continue
            dets = ", ".join(f"{d.track_id}:{d.confidence:.2f}@y{(d.bbox.y_min + d.bbox.y_max) / 2:.2f}" for d in obs.detections) or "-"
            states = "  ".join(f"{s.name.value}={s.state.value}({s.reason})" for s in obs.signals)
            print(f"t={t:6.0f}ms {'RISE' if i < n_rise else 'HOLD'} det[{dets}]  {states}")
    print(json.dumps(analyzer.runtime_stats()))
    analyzer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
