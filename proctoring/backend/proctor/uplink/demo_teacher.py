"""Teacher commands from a terminal for the class demo (owner: C2). Uses ONLY the T01 teacher API (qorgau.class.v1 §5).

Needed today because the T02 class panel has no command buttons yet (the T04/T03 UI modules are not plugged
into the T01 server). Runs on the teacher PC (the teacher API answers on 127.0.0.1 only).

    python -m proctor.uplink.demo_teacher --pin 825050 session "10А, физика"     -> join code
    python -m proctor.uplink.demo_teacher --pin 825050 students
    python -m proctor.uplink.demo_teacher --pin 825050 start            [--student <id or label part>]
    python -m proctor.uplink.demo_teacher --pin 825050 lock "Телефон в руках — подождите преподавателя"
    python -m proctor.uplink.demo_teacher --pin 825050 unlock
    python -m proctor.uplink.demo_teacher --pin 825050 clip             (request the clip of the latest episode with a clip)
    python -m proctor.uplink.demo_teacher --pin 825050 finish
PIN: --pin or QORGAU_CLASS_TEACHER_PIN; server: --server (default 127.0.0.1:8765).
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


class Teacher:
    def __init__(self, server: str, pin: str):
        self.base = f"http://{server}"
        self.jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.call("POST", "/api/teacher/login", {"pin": pin})

    def call(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with self.http.open(req, timeout=15) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise SystemExit(f"{method} {path}: HTTP {exc.code} {detail}") from None

    def student(self, which: str | None) -> dict[str, Any]:
        cards = self.call("GET", "/api/teacher/students") or []
        online = [c for c in cards if c.get("connected")] or cards
        if which:
            online = [c for c in cards if which in (c.get("student_id"), ) or which.lower() in (c.get("student_label") or "").lower()]
        if not online:
            raise SystemExit("no student found (is the student app connected?)")
        return online[0]

    def command(self, sid: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        cmd = self.call("POST", f"/api/teacher/students/{sid}/commands", {"kind": kind, "payload": payload})
        for _ in range(40):  # wait for the student's ack (protocol: 10 s)
            state = self.call("GET", f"/api/teacher/commands/{cmd['command_id']}")
            if state.get("status") not in ("queued", "sent", "executing"):
                return state
            time.sleep(0.5)
        return state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proctor.uplink.demo_teacher", description=__doc__.splitlines()[0])
    ap.add_argument("--server", default="127.0.0.1:8765")
    ap.add_argument("--pin", default=os.environ.get("QORGAU_CLASS_TEACHER_PIN"))
    ap.add_argument("--student", help="student id or part of the label (default: first connected)")
    ap.add_argument("action", choices=["session", "students", "start", "finish", "lock", "unlock", "clip"])
    ap.add_argument("text", nargs="?", default="")
    a = ap.parse_args(argv)
    if not a.pin:
        raise SystemExit("teacher PIN required: --pin or QORGAU_CLASS_TEACHER_PIN (printed by the class server)")
    t = Teacher(a.server, a.pin)
    if a.action == "session":
        s = t.call("POST", "/api/teacher/session", {"title": a.text or "Демо-экзамен", "mode": "url", "allowed_urls": []})
        print(f"join code: {s['join_code']}   session: {s['session_id']}")
        return 0
    if a.action == "students":
        for c in t.call("GET", "/api/teacher/students") or []:
            print(f"{c['student_id']}  {c.get('student_label')!s:24} {c.get('computer_name')!s:16} {c.get('connection')!s:8} zone={c.get('zone')} locked={c.get('locked')} episodes={c.get('incidents_total')}")
        return 0
    st = t.student(a.student)
    sid = st["student_id"]
    if a.action == "clip":
        incs = [i for i in (t.call("GET", f"/api/teacher/students/{sid}/incidents") or []) if i.get("clip_available")]
        if not incs:
            raise SystemExit("no episode with a clip yet")
        inc = incs[-1]
        state = t.command(sid, "request_clip", {"incident_id": inc["incident_id"]})
        print(f"request_clip {inc['incident_id']}: {state.get('status')} {json.dumps(state.get('ack'), ensure_ascii=False)}")
        print(f"teacher clip URL (panel/browser): {t.base}/api/teacher/clips/{inc['incident_id']}")
        return 0
    kind = {"start": "start_exam", "finish": "finish_exam", "lock": "lock", "unlock": "unlock"}[a.action]
    payload = {"reason_ru": a.text or "Проверка преподавателем"} if kind == "lock" else {}
    state = t.command(sid, kind, payload)
    print(f"{kind} -> {st.get('student_label')}: {state.get('status')} {json.dumps(state.get('ack'), ensure_ascii=False)}")
    return 0 if state.get("status") == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
