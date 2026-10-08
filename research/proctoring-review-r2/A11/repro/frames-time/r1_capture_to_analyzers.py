"""ISOLATED REPRO (not an integration run): real A02 FrameCaptureService (synthetic + replay image_sequence)
feeding real A03 PhoneAnalyzer and real A04 MediaPipeAttentionAnalyzer whose model internals are stubbed
(A03: FakeDetector-like object; A04: proctor.attention.testing.FakeFaceBackend). No weights, no camera.
Checks: exceptions in callbacks, observation ids/times/frame ids vs FramePacket, ordering across replay loops."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from proctor.settings import Settings
from proctor_contracts.interfaces import SessionClock
from proctor_contracts.v1 import SourceConfig, SourceMode

from proctor.capture.service import FrameCaptureService
from proctor.phone.analyzer import PhoneAnalyzer
from proctor.phone.detector import RawDetection
from proctor.attention.analyzer import MediaPipeAttentionAnalyzer
from proctor.attention.config import AttentionConfig
from proctor.attention.testing import FakeFaceBackend, synthetic_face


class StubPhoneDetector:
    def __init__(self):
        self.calls = 0
        self.writeable_seen = set()

    def detect(self, image_bgr):
        self.calls += 1
        self.writeable_seen.add(bool(image_bgr.flags.writeable))
        return [RawDetection(0.40, 0.30, 0.55, 0.55, 0.8, 67, "cell phone")], {"infer_ms": 1.0}


def make_replay(root: Path, n: int, fps: float, loop: bool, pacing: str) -> str:
    media = root / "media" / "seq"
    media.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    for i in range(n):
        img = np.clip(rng.normal(120, 25, (240, 320, 3)), 0, 255).astype(np.uint8)
        cv2.putText(img, str(i), (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        cv2.imwrite(str(media / f"{i:05d}.png"), img)
    rid = f"r1_{pacing}_{'loop' if loop else 'once'}"
    (root / f"{rid}.json").write_text(json.dumps({
        "format": "qorgau.replay.v1", "replay_id": rid,
        "media": {"kind": "image_sequence", "path": "media/seq", "fps": fps, "timestamps": "fps"},
        "pacing": pacing, "loop": loop,
    }))
    return rid


def run(mode: SourceMode, settings: Settings, source: SourceConfig, seconds: float, label: str):
    cap = FrameCaptureService(settings)
    phone = PhoneAnalyzer(settings, detector=StubPhoneDetector())
    print(label, "phone.load:", phone.load().code)
    face = synthetic_face()
    att = MediaPipeAttentionAnalyzer(settings, AttentionConfig(), backend_factory=lambda cfg: FakeFaceBackend(lambda ts: [face]))
    print(label, "attention.load:", att.load().code)
    sid = f"ses-{label}"
    obs = {"phone": [], "attention": []}
    frames = {"phone": [], "attention": []}
    errors = []

    def consumer(name, analyzer):
        def cb(frame):
            frames[name].append(frame.meta)
            try:
                out = analyzer.process(frame)
            except Exception as exc:  # report and re-raise like A01r2
                errors.append((name, frame.frame_id, repr(exc)))
                raise
            obs[name].extend(out)
        return cb

    clock = SessionClock()
    for a in (phone, att):
        a.start_session(sid, mode)
    cap.add_consumer("phone", consumer("phone", phone), max_fps=settings.phone_max_fps)
    cap.add_consumer("attention", consumer("attention", att), max_fps=settings.attention_max_fps)
    cap.open(sid, source, clock)
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        time.sleep(0.05)
    m = cap.metrics()
    h = cap.health()
    cap.close()
    for n in ("phone", "attention"):
        cap.remove_consumer(n)
    for a in (phone, att):
        a.end_session()
    print(label, "capture health:", h.status.value, h.code, {k: h.details.get(k) for k in ("loops", "frames", "pacing", "last_frame_id")})
    print(label, "frames_captured", m.frames_captured, "dropped", m.frames_dropped,
          [(c.name, c.frames_processed, c.frames_skipped, c.errors) for c in m.consumers])
    print(label, "callback errors:", errors[:3], "count", len(errors))
    for n in ("phone", "attention"):
        os_ = obs[n]
        fm = frames[n]
        ids = [o.observation_id for o in os_]
        dup = len(ids) - len(set(ids))
        mism = 0
        meta_by_fid = {f.frame_id: f for f in fm}
        for o in os_:
            f = meta_by_fid.get(o.frame_id)
            if f is None or f.t_session_ms != o.t_session_ms or f.wall_time != o.wall_time or f.source_mode != o.source_mode:
                mism += 1
        ts = [f.t_session_ms for f in fm]
        fids = [f.frame_id for f in fm]
        backwards = sum(1 for a, b in zip(ts, ts[1:]) if b < a)
        fid_back = sum(1 for a, b in zip(fids, fids[1:]) if b <= a)
        print(f"{label} {n}: frames={len(fm)} obs={len(os_)} dup_ids={dup} meta_mismatch={mism} t_backwards={backwards} fid_nonincreasing={fid_back}"
              f" first_fids={fids[:5]} statuses={sorted({o.status.value for o in os_})}")
    print(label, "phone image writeable seen by detector:", phone._detector.writeable_seen)
    return obs, frames


def main():
    tmp = Path(tempfile.mkdtemp(prefix="a11r1-", dir=sys.argv[1] if len(sys.argv) > 1 else None))
    rd = tmp / "replay"
    rd.mkdir()
    settings = Settings(data_dir=tmp / "data", models_dir=tmp / "models", replay_dir=rd)
    run(SourceMode.SYNTHETIC, settings, SourceConfig(mode=SourceMode.SYNTHETIC), 2.0, "synthetic")
    for pacing in ("realtime", "lockstep"):
        rid = make_replay(rd, 12, 10.0, True, pacing)
        run(SourceMode.REPLAY, settings, SourceConfig(mode=SourceMode.REPLAY, replay_id=rid), 3.0, f"replay-{pacing}-loop")


if __name__ == "__main__":
    main()
