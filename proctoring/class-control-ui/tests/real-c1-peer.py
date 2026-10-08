"""Bounded browser fixture: real C1 + explicitly synthetic WS peer, no hardware.

READY credentials travel only over the parent process pipe and must never be logged.
"""
import json
from pathlib import Path
import queue
import sys
import threading
import time

from classroom.server.tests.harness import ServerProcess


def main():
    srv = ServerProcess(Path(sys.argv[1]), {"QORGAU_CLASS_FEATURES": "proctor_classctl.classroom_feature:create", "QORGAU_CLASS_UI": "class-panel"})
    teacher = srv.teacher()
    peer = srv.student()
    inputs = queue.Queue()

    def read():
        for line in sys.stdin:
            inputs.put(line.strip())
        inputs.put("stop")

    threading.Thread(target=read, daemon=True).start()
    try:
        session = teacher.post("/api/teacher/session", json={"title": "Контрольный класс", "mode": "url", "allowed_urls": ["https://exam.example/*"]}).json()
        sid = peer.hello(join_code=session["join_code"], student_label="Тестовый студент", source_mode="synthetic")["student_id"]
        state = dict(exam_state="preflight", source_mode="synthetic", locked=False)
        peer.status(**state)
        print(json.dumps({"base": srv.base, "pin": srv.pin, "student_id": sid}), flush=True)
        seen, pending = set(), []
        mode, connected, fresh = "normal", True, True
        while True:
            try:
                action = inputs.get_nowait()
                if action == "stop":
                    break
                if action in ("normal", "legacy", "fail"):
                    mode = action
                if action == "stale":
                    fresh = False
                if action == "fresh":
                    fresh = True
                if action == "disconnect":
                    peer.close()
                    connected = False
            except queue.Empty:
                pass
            if connected:
                for cmd in peer.of_type("command"):
                    if cmd["command_id"] in seen:
                        continue
                    seen.add(cmd["command_id"])
                    pending.append((time.monotonic() + 1.5, cmd, mode))
                    if cmd["kind"] in ("lock", "unlock"):
                        state.update(lock_state="requested", lock_requested=cmd["kind"] == "lock", lock_confirmed=False, lock_scope="app_overlay")
                for item in list(pending):
                    due, cmd, response = item
                    if time.monotonic() < due:
                        continue
                    pending.remove(item)
                    kind = cmd["kind"]
                    if response == "fail":
                        state.update(lock_state="failed", lock_confirmed=False)
                        peer.send("ack", command_id=cmd["command_id"], ok=False, code="failed", error_ru="Экран не подтвердил команду")
                    else:
                        if kind in ("lock", "unlock"):
                            state["locked"] = kind == "lock"
                            if response == "legacy":
                                state = {k: v for k, v in state.items() if not k.startswith("lock_")}
                            else:
                                state.update(lock_state="applied", lock_confirmed=True, lock_requested=state["locked"], lock_scope="app_overlay")
                        elif kind == "start_exam":
                            state["exam_state"] = "running"
                        elif kind == "finish_exam":
                            state["exam_state"] = "finished"
                        result = {"locked": state["locked"], "exam_state": state["exam_state"]}
                        if response != "legacy" and kind in ("lock", "unlock"):
                            result.update(lock_state="applied", lock_scope="app_overlay", class_session_id=session["session_id"], backend_instance_id="synthetic-browser-peer")
                        peer.send("ack", command_id=cmd["command_id"], ok=True, result=result)
                if fresh:
                    peer.status(**state)
            time.sleep(0.1)
    finally:
        peer.close()
        teacher.close()
        srv.stop()


if __name__ == "__main__":
    main()
