"""ISOLATED REPRO (not an integration run): A01r2 create_app + real A02 capture (replay) + real A05 fusion +
real A08 evidence store; A03/A04 analyzers are the REAL classes with stubbed model internals (no weights).
Question: does the frame timeline produced by A02 for replay (t_session_ms = replay_start_t_ms + media_pts_ms)
stay comparable with the SessionClock times A01 stamps on exam start / pause / finish / health / environment?

usage: r2_replay_timeline_app.py <workdir> <pacing realtime|lockstep> <speed> <loop 0|1> <seconds_before_start> <run_seconds> [n_frames@10fps=100]
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.settings import Settings
from proctor.phone.analyzer import PhoneAnalyzer
from proctor.phone.detector import RawDetection
from proctor.attention.analyzer import MediaPipeAttentionAnalyzer
from proctor.attention.config import AttentionConfig
from proctor.attention.testing import FakeFaceBackend, synthetic_face

TOKEN = "q" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}


class StubPhoneDetector:
    """Phone visible in media frames 20..59 of each 10 fps pass (2.0 s .. 6.0 s of media time)."""

    def detect(self, image_bgr):
        # the frame index is burned into pixel (0,0) blue channel by make_replay()
        idx = int(image_bgr[0, 0, 0])
        if 20 <= idx < 60:
            return [RawDetection(0.40, 0.30, 0.55, 0.55, 0.8, 67, "cell phone")], {"infer_ms": 1.0}
        return [], {"infer_ms": 1.0}


def make_replay(root: Path, n: int, fps: float, loop: bool, pacing: str, speed: float) -> str:
    media = root / "media" / "seq"
    media.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    for i in range(n):
        img = np.clip(rng.normal(120, 25, (240, 320, 3)), 0, 255).astype(np.uint8)
        img[0:2, 0:2, :] = i  # frame index marker (lossless PNG)
        cv2.imwrite(str(media / f"{i:05d}.png"), img)
    rid = "r2demo"
    (root / f"{rid}.json").write_text(json.dumps({
        "format": "qorgau.replay.v1", "replay_id": rid,
        "media": {"kind": "image_sequence", "path": "media/seq", "fps": fps, "timestamps": "fps"},
        "pacing": pacing, "loop": loop, "speed": speed,
    }))
    return rid


def main():
    work = Path(sys.argv[1])
    pacing, speed, loop = sys.argv[2], float(sys.argv[3]), sys.argv[4] == "1"
    before, run_s = float(sys.argv[5]), float(sys.argv[6])
    tmp = Path(tempfile.mkdtemp(prefix="a11r2-", dir=work))
    rd = tmp / "replay"
    rd.mkdir()
    nframes = int(sys.argv[7]) if len(sys.argv) > 7 else 100
    rid = make_replay(rd, nframes, 10.0, loop, pacing, speed)
    settings = Settings(data_dir=tmp / "data", models_dir=tmp / "models", replay_dir=rd, exam_path=tmp / "missing.json")
    face = synthetic_face()
    overrides = {
        "phone": lambda s: PhoneAnalyzer(s, detector=StubPhoneDetector()),
        "attention": lambda s: MediaPipeAttentionAnalyzer(s, AttentionConfig(), backend_factory=lambda cfg: FakeFaceBackend(lambda ts: [face])),
    }
    app = create_app(settings, TOKEN, module_overrides=overrides)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        r = c.post("/v1/sessions", json={"source": {"mode": "replay", "replay_id": rid}, "exam_id": "demo-exam-1", "consent": CONSENT})
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        pf = c.post(f"/v1/sessions/{sid}/preflight").json()
        print("preflight ready:", pf["ready"], [(x["check_id"], x["status"]) for x in pf["checks"] if x["required"]])
        time.sleep(before)  # operator reads the consent / calibration screens
        print("skip:", c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "replay"}).status_code)
        st = c.post(f"/v1/sessions/{sid}/start").json()
        rt = app.state.proctor["manager"].runtime(sid)
        cap = app.state.registry.loaded["capture"].impl
        print(f"start: exam_started_t_ms={st['exam_started_t_ms']:.0f} clock_now={rt.clock.now_ms():.0f} "
              f"capture_last_frame={cap.health().details.get('last_frame_id')} replay_start_t_ms={cap.replay_start_t_ms()}")
        time.sleep(run_s / 2)
        c.post(f"/v1/sessions/{sid}/pause", json={"reason": "check"})
        print(f"paused at clock {rt.clock.now_ms():.0f}; fusion timeline max {rt._timeline_max_ms:.0f}")
        time.sleep(0.5)
        c.post(f"/v1/sessions/{sid}/resume")
        time.sleep(run_s / 2)
        print(f"before finish: clock {rt.clock.now_ms():.0f}; fusion timeline max {rt._timeline_max_ms:.0f}; capture health {cap.health().code}")
        fin = c.post(f"/v1/sessions/{sid}/finish")
        print("finish:", fin.status_code, fin.json().get("state"), "last_error:", (fin.json().get("last_error") or {}).get("message"))
        info = c.get(f"/v1/sessions/{sid}").json()
        print("session:", {k: info.get(k) for k in ("exam_started_t_ms", "paused_total_ms", "started_at", "finished_at")})
        inc = c.get(f"/v1/sessions/{sid}/incidents").json()
        print("incidents:", len(inc) if isinstance(inc, list) else inc)
        if isinstance(inc, list):
            for i in inc:
                print("  ", i["rule_id"], i["state"], round(i["t_start_ms"]), None if i["t_end_ms"] is None else round(i["t_end_ms"]),
                      i.get("end_reason"), "dur", i.get("duration_ms"))
        summ = c.get(f"/v1/sessions/{sid}/summary")
        print("summary:", summ.status_code)
        if summ.status_code == 200:
            s = summ.json()
            print("   summary keys:", sorted(s))
            print("   observed_ms:", s.get("observed_ms"), "incident_count:", s.get("incident_count"))
            print("   incidents_total:", s.get("incidents_total"), "paused_ms:", s.get("paused_ms"))
            print("   gaps:", [(g.get("component"), g.get("reason"), round(g.get("t_start_ms")), round(g.get("t_end_ms"))) for g in (s.get("gaps") or [])][:8])
        print("counters:", rt.counters)


if __name__ == "__main__":
    main()
