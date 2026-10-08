"""Real C1 + second real proctor/C2 backend. No fabricated status or ACK messages.

The second backend has a CREATED synthetic session and deliberately no renderer.
Credentials are printed only to the parent pipe; the runner never saves that pipe.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading

import httpx

from classroom.server.tests.harness import ServerProcess

PROCTORING = Path(__file__).resolve().parents[3]


def main():
    work = Path(sys.argv[1])
    srv = ServerProcess(work / "class", {"QORGAU_CLASS_FEATURES": "classroom.server.audio_feature:create_classroom_feature,proctor_classctl.classroom_feature:create", "QORGAU_CLASS_UI": "class-panel",
                                            "QORGAU_CLASS_STATUS_STALE_S": "6", "QORGAU_CLASS_ACK_WINDOW_S": "8"})
    teacher = srv.teacher()
    second = None
    try:
        session = teacher.post("/api/teacher/session", json={"title": "Синтетическая проверка цепочки", "mode": "url", "allowed_urls": ["https://exam.example/*"]}).json()
        env = {k: v for k, v in os.environ.items() if not k.startswith("QORGAU_")}
        env.update(PYTHONPATH=os.pathsep.join(map(str, [PROCTORING, PROCTORING / "backend", PROCTORING / "contracts/python"])), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                   QORGAU_DATA_DIR=str(work / "student-b"), QORGAU_MODELS_DIR=str(work / "no-models"), QORGAU_REPLAY_DIR=str(work / "no-replays"),
                   QORGAU_CLASS_SERVER=f"127.0.0.1:{srv.port}", QORGAU_CLASS_CODE=session["join_code"], QORGAU_CLASS_LABEL="Backend B — без интерфейса")
        token = secrets.token_urlsafe(32)
        second = subprocess.Popen([sys.executable, "-m", "proctor", "serve", "--port", "0", "--token-stdin", "--exit-on-stdin-eof"],
                                  cwd=PROCTORING, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ready = queue.Queue()
        def read_stdout():
            for line in second.stdout:
                if line.startswith("QORGAU_READY "):
                    ready.put(json.loads(line.split(" ", 1)[1]))
        def discard_stderr():
            for _line in second.stderr:
                pass  # never forward or retain raw process logs
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=discard_stderr, daemon=True).start()
        second.stdin.write(token + "\n")
        second.stdin.flush()
        backend = ready.get(timeout=30)
        with httpx.Client(base_url=f"http://127.0.0.1:{backend['port']}", headers={"Authorization": f"Bearer {token}"}, timeout=10) as api:
            created = api.post("/v1/sessions", json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "student_label": "Backend B",
                               "consent": {"accepted": True, "text_version": "consent-ru-1", "accepted_at": datetime.now(timezone.utc).isoformat()}})
            assert created.status_code == 201, "second synthetic session was not created"
            assert created.json()["state"] == "created"
        print(json.dumps({"base": srv.base, "port": srv.port, "pin": srv.pin, "join_code": session["join_code"]}), flush=True)
        for line in sys.stdin:
            if line.strip() == "stop-b" and second.poll() is None:
                second.stdin.close()
                second.wait(timeout=12)
            if line.strip() == "stop":
                break
    finally:
        if second and second.poll() is None:
            if not second.stdin.closed:
                second.stdin.close()
            try:
                second.wait(timeout=12)
            except subprocess.TimeoutExpired:
                second.kill()
                second.wait(timeout=5)
        teacher.close()
        srv.stop()


if __name__ == "__main__":
    main()
