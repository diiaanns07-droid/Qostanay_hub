"""One student driven by the REAL C2 uplink code (proctor.uplink from codex/class-C2) with a SYNTHETIC data source.

Not a camera and not the CV pipeline: the BackendView is replaced by a labelled fake. The point is the wire path
C2 client -> class server -> teacher panel. Owner: T01.

    PYTHONPATH=<C2 checkout>/proctoring/backend python c2_student.py --server 127.0.0.1:8765 --code 123456 \
        --state-dir /tmp/c2 [--duration 20] [--incident-after 2]
Prints JSON lines on stdout: {"event": ...}.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LABEL = "C2-ЦЕПОЧКА (тест, синтетика)"
EXPLANATION = "ТЕСТ: синтетический эпизод, не реальный студент"


def say(event: str, **fields: Any) -> None:
    sys.stdout.write(json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def test_jpeg() -> bytes:
    import cv2
    import numpy as np

    img = np.full((240, 320, 3), 60, np.uint8)
    cv2.putText(img, "SYNTHETIC C2 TEST", (12, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
    assert ok
    return buf.tobytes()


class SyntheticView:
    """BackendView-like: snapshot(), preview_jpeg(), export_clip(), start_exam(), finish_exam()."""

    def __init__(self, tmp: Path):
        from proctor.uplink.backend_view import Snapshot

        self._snapshot_cls = Snapshot
        self.tmp = tmp
        self.exam_state = "running"
        self.incidents: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.jpeg = test_jpeg()

    def add_incident(self, iid: str, state: str = "open") -> None:
        with self.lock:
            self.incidents = [i for i in self.incidents if i["incident_id"] != iid] + [{
                "incident_id": iid, "rule_id": "phone_visible", "category": "phone", "priority": "medium", "state": state,
                "t_start_ms": 12_000.0, "t_end_ms": None if state == "open" else 15_000.0,
                "t_start_wall": datetime.now(timezone.utc).isoformat(), "duration_ms": 3000.0, "explanation_ru": EXPLANATION,
            }]

    def snapshot(self):  # noqa: ANN201
        with self.lock:
            inc = list(self.incidents)
        by = {"low": 0, "medium": 0, "high": 0}
        for i in inc:
            by[i["priority"]] += 1
        return self._snapshot_cls(session_id="c2-chain", exam_state=self.exam_state, camera="ok", monitoring="ok",
                                  zone="yellow" if inc else "green", zone_reasons_ru=["ТЕСТ: телефон в кадре"] if inc else [],
                                  incidents=inc, incidents_total=len(inc), incidents_by_priority=by)

    def preview_jpeg(self) -> bytes:
        return self.jpeg

    def export_clip(self, t_start_ms: float, before_s: float, after_s: float) -> Path:
        raise RuntimeError("synthetic view has no clips")

    def start_exam(self):  # noqa: ANN201
        self.exam_state = "running"
        return True, None

    def finish_exam(self):  # noqa: ANN201
        self.exam_state = "finished"
        return True, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--incident-after", type=float, default=2.0)
    args = ap.parse_args()
    from proctor.uplink.client import Uplink
    from proctor.uplink.config import UplinkConfig

    cfg = UplinkConfig(server=args.server, join_code=args.code, student_label=LABEL, computer_name="C2-TEST-PC", state_dir=args.state_dir,
                       status_interval_s=1.0, preview_interval_s=1.0, poll_interval_s=0.2, backoff_max_s=2.0, welcome_timeout_s=5.0)
    view = SyntheticView(args.state_dir)
    states: list[str] = []
    uplink = Uplink(cfg, view, publish=lambda msg: states.append(getattr(msg, "connection", "?")))
    uplink.start()
    t0 = time.monotonic()
    added = False
    try:
        while time.monotonic() - t0 < args.duration:
            if not added and uplink.connection == "connected" and time.monotonic() - t0 > args.incident_after:
                view.add_incident("c2-chain-inc-1")
                added = True
                say("incident_added", student_id=uplink.student_id)
            time.sleep(0.1)
    finally:
        student_id = uplink.student_id  # read before stop(): stop() closes the outbox
        uplink.stop()
    sent = [m.get("type") for m in uplink.sent]
    say("summary", student_id=student_id, connection_states=sorted(set(states)), connects=uplink.connects,
        sent={t: sent.count(t) for t in sorted(set(sent))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
