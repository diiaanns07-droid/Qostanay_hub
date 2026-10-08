"""FAKE STUDENT for the T03 DEV harness: a labelled SYNTHETIC client, never a real student.

Joins with the 6-digit code, sends status + test episodes (explanation starts with "ТЕСТ:"), answers
`request_clip` with ack ok=true and uploads a burned-in "TEST CLIP - SYNTHETIC" MP4 (X-Qorgau-Clip-Source: test).
One episode is announced with clip_available=false (shows "недоступен"), one request is refused with
ack ok=false when --refuse is given.

    cd proctoring && python -m classreview.examples.fake_student --server http://127.0.0.1:8765 --code 123456
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from websockets.sync.client import connect

from ..testclips import make_test_clip
from .upload_clip import upload_clip

EPISODES = [
    # (incident_id suffix, rule_id, category, priority, offset_s, duration_ms, explanation, clip_available)
    ("1", "phone_visible", "phone", "medium", 41, 7000, "ТЕСТ: телефон в кадре 7 с (синтетические данные)", True),
    ("2", "gaze_prolonged_down", "attention", "low", 95, 9000, "ТЕСТ: долгий взгляд вниз 9 с (приблизительно, синтетика)", True),
    ("3", "multiple_faces", "presence", "high", 150, 4000, "ТЕСТ: второе лицо в кадре 4 с (синтетика)", False),
]


def now() -> datetime:
    return datetime.now(timezone.utc)


def env(msg_type: str, **fields) -> str:
    return json.dumps({"type": msg_type, "v": 1, "msg_id": str(uuid.uuid4()), "sent_at": now().isoformat(), **fields}, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", default="http://127.0.0.1:8765")
    ap.add_argument("--code", required=True)
    ap.add_argument("--label", default="ТЕСТ-студент-1")
    ap.add_argument("--refuse", default="", help="incident suffix whose clip request is refused with ack ok=false")
    ap.add_argument("--run-seconds", type=float, default=600.0)
    args = ap.parse_args(argv)
    ws_url = args.server.replace("http", "ws", 1) + "/ws/student"
    with connect(ws_url) as ws:
        ws.send(env("hello", protocol="qorgau.class.v1", join_code=args.code, computer_name="TEST-PC", student_label=args.label,
                    app_version="fake-student"))
        welcome = json.loads(ws.recv(timeout=10))
        if welcome.get("type") != "welcome":
            print("rejected:", welcome)
            return 1
        sid, token = welcome["student_id"], welcome["resume_token"]
        print(f"joined as {sid} (SYNTHETIC test student)")
        prefix = f"inc-{sid}"
        start = now() - timedelta(seconds=180)
        ws.send(env("status", exam_state="running", camera="ok", monitoring="ok", zone="red",
                    zone_reasons_ru=["ТЕСТ"], incidents_total=len(EPISODES), incidents_by_priority={"low": 1, "medium": 1, "high": 1},
                    locked=False, mic_active=False))
        seq = 0
        for suffix, rule, cat, prio, off, dur, text, avail in EPISODES:
            for state, d in (("open", 1000), ("closed", dur)):
                seq += 1
                ws.send(env("incident", seq=seq, incident_id=f"{prefix}-{suffix}", rule_id=rule, category=cat, priority=prio,
                            state=state, t_start_wall=(start + timedelta(seconds=off)).isoformat(), duration_ms=d,
                            explanation_ru=text, clip_available=avail))
        clips = {}
        deadline = time.monotonic() + args.run_seconds
        while time.monotonic() < deadline:
            try:
                msg = json.loads(ws.recv(timeout=1.0))
            except TimeoutError:
                continue
            if msg.get("type") == "ping":
                ws.send(env("pong"))
            elif msg.get("type") == "command" and msg.get("kind") == "request_clip":
                iid = msg["payload"]["incident_id"]
                if args.refuse and iid.endswith(f"-{args.refuse}"):
                    ws.send(env("ack", command_id=msg["command_id"], ok=False, error_ru="ТЕСТ: клип не сохранён на компьютере студента"))
                    continue
                ws.send(env("ack", command_id=msg["command_id"], ok=True))
                data = clips.setdefault(iid, make_test_clip(seconds=10.0, fps=10))  # same bytes for every retry

                def send(iid=iid, data=data):
                    try:
                        print("upload", iid, upload_clip(args.server, token, iid, data, source="test")["status"])
                    except Exception as exc:  # noqa: BLE001 — example output
                        print("upload failed", iid, exc)

                threading.Thread(target=send, daemon=True).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
