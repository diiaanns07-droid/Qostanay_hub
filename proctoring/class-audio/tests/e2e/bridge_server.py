"""Test-only C1 + real C2 fixture. Synthetic snapshot, no CV, device or guard startup."""
import json
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from classroom.server.tests.harness import ServerProcess
from proctor.uplink.client import Uplink
from proctor.uplink.config import UplinkConfig
from proctor.uplink.backend_view import Snapshot
from proctor.uplink.audio import install_audio_routes

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
server = ServerProcess(root / "class", {"QORGAU_CLASS_PING_INTERVAL_S": "5", "QORGAU_CLASS_PONG_TIMEOUT_S": "15"})
teacher = server.teacher()
room = teacher.post("/api/teacher/session", json={"title": "Synthetic audio fixture", "mode": "app", "allowed_apps": ["fixture.exe"]}).json()


class View:
    snapshot_value = Snapshot(session_id="synthetic-local", source_mode="synthetic", exam_state="running")
    def snapshot(self): return self.snapshot_value
    def preview_jpeg(self): return None


events = []
def publish(message):
    events.append({"contract": "qorgau.v1", "seq": len(events) + 1, "message": message.model_dump(mode="json")})


view = View()
uplink = Uplink(UplinkConfig(server=f"127.0.0.1:{server.port}", join_code=room["join_code"], student_label="Synthetic audio student",
                             computer_name="AUDIO-FIXTURE", state_dir=root / "uplink", poll_interval_s=0.1), view, publish)
uplink.start()
token = secrets.token_hex(32)
app = FastAPI()
def authorized(request: Request):
    if request.headers.get("authorization") != f"Bearer {token}":
        raise HTTPException(401)
api = APIRouter(prefix="/v1", dependencies=[Depends(authorized)])
install_audio_routes(api, lambda: uplink)


@api.get("/test/events")
def get_events(after: int = 0):
    return {"events": events[after:], "student_id": uplink.student_id, "connection": uplink.connection}


@api.post("/test/action")
def action(body: dict):
    if body.get("action") == "finish":
        view.snapshot_value = Snapshot(session_id="synthetic-local", source_mode="synthetic", exam_state="finished")
    elif body.get("action") == "resume":
        view.snapshot_value = Snapshot(session_id="synthetic-local", source_mode="synthetic", exam_state="running")
    elif body.get("action") == "disconnect":
        uplink.stop()
    return {"ok": True}


app.include_router(api)
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
runtime = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", log_level="error"))
def stdin_closed():
    sys.stdin.read()
    runtime.should_exit = True
threading.Thread(target=stdin_closed, daemon=True).start()
print(json.dumps({"port": port, "token": token, "class_port": server.port, "pin": server.pin}), flush=True)
try:
    runtime.run(sockets=[sock])
finally:
    if uplink.connection != "stopped": uplink.stop()
    teacher.close()
    server.stop()
