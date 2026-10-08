"""SYNTHETIC test clips for tests, the DEV harness and the client example (never student recordings).

Every frame carries the burned-in caption "TEST CLIP - SYNTHETIC - NOT A STUDENT RECORDING", the frame
number and a moving bar, so a clip played in the teacher UI cannot be mistaken for a real recording and
seeking is visible. Needs OpenCV (part of the `cv` extra); the class server itself does not.

    python -m classreview.testclips out.mp4 [--seconds 6] [--fps 10]
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

TEST_CAPTION = "TEST CLIP - SYNTHETIC - NOT A STUDENT RECORDING"


def make_test_clip(seconds: float = 6.0, fps: int = 10, size: tuple[int, int] = (320, 240), fourcc: str = "vp09") -> bytes:
    """MP4 bytes (VP9 by default: plays in Chromium/Edge/Chrome; H.264 needs an encoder OpenCV wheels lack)."""
    import cv2  # local import: only tests/dev need it
    import numpy as np

    w, h = size
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "clip.mp4")
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
        if not writer.isOpened():
            raise RuntimeError(f"OpenCV cannot write {fourcc} MP4 here")
        total = max(1, int(seconds * fps))
        for i in range(total):
            img = np.full((h, w, 3), (40, 40, 40), np.uint8)
            x = int((i / max(1, total - 1)) * (w - 20))
            img[h - 30 : h - 10, x : x + 20] = (0, 200, 255)
            img[:28, :] = (0, 0, 160)
            cv2.putText(img, "TEST CLIP - SYNTHETIC", (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(img, "NOT A STUDENT RECORDING", (8, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(img, f"frame {i:03d}  t={i / fps:4.1f}s", (8, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 255, 200), 2, cv2.LINE_AA)
            writer.write(img)
        writer.release()
        return Path(path).read_bytes()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--fps", type=int, default=10)
    args = ap.parse_args(argv)
    args.out.write_bytes(make_test_clip(args.seconds, args.fps))
    print(f"wrote {args.out} ({TEST_CAPTION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
