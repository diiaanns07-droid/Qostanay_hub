"""ISOLATED REPRO: A04 calibration target while A02 delivers no frames (replay ended / camera stalled).
A04 measures the per-target timeout on FRAME time, so with no frames the target never fails or completes."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from proctor.settings import Settings
from proctor_contracts.interfaces import SessionClock
from proctor_contracts.v1 import CalibrationTarget as T, SourceConfig, SourceMode
from proctor.capture.service import FrameCaptureService
from proctor.attention.analyzer import MediaPipeAttentionAnalyzer
from proctor.attention.config import AttentionConfig
from proctor.attention.testing import FakeFaceBackend, synthetic_face

tmp = Path(tempfile.mkdtemp(prefix="a11r6-", dir=sys.argv[1]))
rd = tmp / "replay"
(rd / "media" / "seq").mkdir(parents=True)
rng = np.random.default_rng(0)
for i in range(10):
    cv2.imwrite(str(rd / "media" / "seq" / f"{i:03d}.png"), np.clip(rng.normal(120, 25, (240, 320, 3)), 0, 255).astype(np.uint8))
(rd / "r6.json").write_text(json.dumps({"format": "qorgau.replay.v1", "replay_id": "r6",
                                        "media": {"kind": "image_sequence", "path": "media/seq", "fps": 10, "timestamps": "fps"}}))
settings = Settings(data_dir=tmp / "d", models_dir=tmp / "m", replay_dir=rd)
cap = FrameCaptureService(settings)
face = synthetic_face()
att = MediaPipeAttentionAnalyzer(settings, AttentionConfig(), backend_factory=lambda cfg: FakeFaceBackend(lambda ts: [face]))
att.load()
att.start_session("ses-r6", SourceMode.REPLAY)
cap.add_consumer("attention", lambda f: att.process(f), max_fps=15)
cap.open("ses-r6", SourceConfig(mode=SourceMode.REPLAY, replay_id="r6"), SessionClock())
time.sleep(1.5)
print("capture:", cap.health().status.value, cap.health().code)
att.calibration_start()
st = att.calibration_target(T.CENTER)
t0 = time.monotonic()
time.sleep(15.0)  # > calibration_target_timeout_ms (12 s)
st = att.calibration_state()
c = next(x for x in st.targets if x.target == T.CENTER)
print(f"after {time.monotonic() - t0:.1f} s wall: phase={st.phase.value} center.state={c.state.value} samples={c.samples} "
      f"message_code={c.message_code} current_target={st.current_target}")
cap.close()
cap.remove_consumer("attention")
att.end_session()
