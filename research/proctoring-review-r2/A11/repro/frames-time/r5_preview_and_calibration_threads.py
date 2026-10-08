"""ISOLATED REPRO (negative evidence): (a) A02 preview meta for a 1280x720 synthetic source (downscale, frame_id in
ring, mirrored=False); (b) A04 calibration_* called from 'API' threads while A02's consumer thread runs process()."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

from proctor.settings import Settings
from proctor_contracts.interfaces import SessionClock
from proctor_contracts.v1 import CalibrationTarget as T, SourceConfig, SourceMode
from proctor.capture.service import FrameCaptureService
from proctor.attention.analyzer import MediaPipeAttentionAnalyzer
from proctor.attention.config import AttentionConfig
from proctor.attention.testing import FakeFaceBackend, synthetic_face

tmp = Path(tempfile.mkdtemp(prefix="a11r5-", dir=sys.argv[1]))
settings = Settings(data_dir=tmp / "d", models_dir=tmp / "m", replay_dir=tmp / "r")
cap = FrameCaptureService(settings)
face = synthetic_face()
att = MediaPipeAttentionAnalyzer(settings, AttentionConfig(), backend_factory=lambda cfg: FakeFaceBackend(lambda ts: [face]))
att.load()
att.start_session("ses-r5", SourceMode.SYNTHETIC)
errs = []


def cb(frame):
    att.process(frame)


cap.add_consumer("attention", cb, max_fps=15)
cap.open("ses-r5", SourceConfig(mode=SourceMode.SYNTHETIC, width=1280, height=720), SessionClock())
time.sleep(0.5)
meta, jpg = cap.latest_preview()
fr = cap.get_frame(meta.frame_id)
print("(a) preview meta:", meta.width, "x", meta.height, "mirrored", meta.mirrored, "frame in ring:", fr is not None,
      "frame size:", None if fr is None else (fr.meta.width, fr.meta.height), "image writeable:", None if fr is None else fr.image.flags.writeable)
try:
    fr.image.flags.writeable = True
    print("    image could be made writeable!")
except ValueError as e:
    print("    image.flags.writeable=True ->", type(e).__name__)


def api_thread(n):
    try:
        for i in range(n):
            att.calibration_start()
            for t in (T.CENTER, T.LEFT, T.RIGHT, T.UP, T.DOWN):
                att.calibration_target(t)
                att.calibration_state()
            att.calibration_finish()
            att.calibration_cancel()
            att.calibration_skip("x")
    except Exception as exc:  # noqa: BLE001
        errs.append(repr(exc))


ths = [threading.Thread(target=api_thread, args=(200,)) for _ in range(3)]
t0 = time.monotonic()
for t in ths:
    t.start()
for t in ths:
    t.join(30)
m = cap.metrics()
cap.close()
cap.remove_consumer("attention")
att.end_session()
print("(b) api threads alive:", [t.is_alive() for t in ths], "secs:", round(time.monotonic() - t0, 2), "api errors:", errs[:3], len(errs))
print("    consumer metrics:", [(c.name, c.frames_processed, c.errors) for c in m.consumers])
