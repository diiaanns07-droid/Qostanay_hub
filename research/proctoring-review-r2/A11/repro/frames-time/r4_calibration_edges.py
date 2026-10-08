"""ISOLATED REPRO (no weights): real A04 MediaPipeAttentionAnalyzer + CalibrationController with the A04 FakeFaceBackend
(proctor.attention.testing.synthetic_face = a face with KNOWN head pose). The student looks at the five calibration
dots where A07 @3fef6fb actually draws them (measured with Playwright, r3_calibration_stage_geometry.mjs) vs where
A04 INTERFACE.md asks for them (screen center + ~5 % from each edge). Then the student reads ordinary on-screen
exam content; we print gaze.direction.
Geometry assumption (stated, not measured): 15.6" 16:9 laptop screen 34.5 x 19.4 cm, eyes 60 cm away, webcam centred
1 cm above the top edge; the student turns the head to the point (eye-in-head rotation 0)."""
from __future__ import annotations

import math
import sys

from proctor.settings import Settings
from proctor_contracts.v1 import CalibrationTarget as T, SourceMode
from proctor.attention.analyzer import MediaPipeAttentionAnalyzer
from proctor.attention.config import AttentionConfig
from proctor.attention.testing import FakeFaceBackend, make_frame, synthetic_face

D_CM, CAM_ABOVE_CM = 60.0, 1.0
SCREEN = {"1920x1080": (34.5, 19.4), "1366x768": (31.0, 17.4)}  # 15.6" and 14" 16:9 panels (assumed)
W_CM, H_CM = SCREEN["1920x1080"]


def angles(x: float, y: float) -> tuple[float, float]:
    """Screen point (fraction from the student's left / from the top) -> (yaw +right, pitch +up) in degrees."""
    yaw = math.degrees(math.atan2((x - 0.5) * W_CM, D_CM))
    pitch = -math.degrees(math.atan2(y * H_CM + CAM_ABOVE_CM, D_CM))
    return yaw, pitch


A07_1920 = {T.CENTER: (0.32, 0.438), T.LEFT: (0.112, 0.438), T.RIGHT: (0.527, 0.438), T.UP: (0.32, 0.236), T.DOWN: (0.32, 0.64)}
A07_1366 = {T.CENTER: (0.293, 0.546), T.LEFT: (0.057, 0.546), T.RIGHT: (0.53, 0.546), T.UP: (0.293, 0.315), T.DOWN: (0.293, 0.776)}
A04_SPEC = {T.CENTER: (0.5, 0.5), T.LEFT: (0.05, 0.5), T.RIGHT: (0.95, 0.5), T.UP: (0.5, 0.05), T.DOWN: (0.5, 0.95)}

# A07 exam screen elements measured with Playwright (r3 part 2), centre points as screen fractions
READING = {
    "1920x1080": {
        "question prompt, middle": (0.50, 0.271),
        "question prompt, right end": (0.72, 0.271),
        "'Далее' button": (0.716, 0.515),
        "timer (top right)": (0.711, 0.12),
        "footer (exit link row)": (0.50, 0.96),
    },
    "1366x768": {
        "question prompt, middle": (0.50, 0.363),
        "question prompt, right end": (0.81, 0.363),
        "'Далее' button": (0.803, 0.67),
        "timer (top right)": (0.797, 0.164),
        "footer (exit link row)": (0.50, 0.94),
    },
}


class Run:
    def __init__(self):
        self.face = synthetic_face()
        self.att = MediaPipeAttentionAnalyzer(Settings(), AttentionConfig(), backend_factory=lambda cfg: FakeFaceBackend(lambda ts: [self.face]))
        assert self.att.load().code == "ok"
        self.att.start_session("ses-r4", SourceMode.LIVE)
        self.t = 0.0
        self.fid = 0

    def look(self, x: float, y: float, ms: float):
        yaw, pitch = angles(x, y)
        self.face = synthetic_face(yaw=yaw, pitch=pitch)
        out = None
        end = self.t + ms
        while self.t < end:
            out = self.att.process(make_frame("ses-r4", self.fid, self.t, source_mode=SourceMode.LIVE))[0]
            self.fid += 1
            self.t += 1000.0 / 15
        return out


def calibrate(r: Run, dots) -> str:
    r.att.calibration_start()
    for target in (T.CENTER, T.LEFT, T.RIGHT, T.UP, T.DOWN):
        r.att.calibration_target(target)
        r.look(*dots[target], 2500)
    st = r.att.calibration_finish()
    return f"{st.phase.value} " + ",".join(f"{t.target.value}:{t.state.value}" for t in st.targets)


def main():
    global W_CM, H_CM
    cases = []
    for res, a07 in (("1920x1080", A07_1920), ("1366x768", A07_1366)):
        cases += [(res, f"A07 layout @{res}", a07), (res, "A04 spec (centre + 5% from edges)", A04_SPEC), (res, "skipped (generic edges)", None)]
    for res, label, dots in cases:
        W_CM, H_CM = SCREEN[res]
        r = Run()
        if dots is None:
            r.att.calibration_skip("r4")
            state = "skipped"
        else:
            state = calibrate(r, dots)
        print(f"== [{res} screen {W_CM}x{H_CM} cm] calibration on {label}: {state}")
        for name, (x, y) in READING[res].items():
            o = r.look(x, y, 1200)
            print(f"   {name:45s} gaze={o.gaze.direction.value:7s} calibrated={o.gaze.calibrated} reasons={[x for x in o.reasons if x.startswith('gaze')]}")


if __name__ == "__main__":
    main()
