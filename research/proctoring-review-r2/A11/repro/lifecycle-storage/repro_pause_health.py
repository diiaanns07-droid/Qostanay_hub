"""A11 repro: capture health recovery delivered while PAUSED never reaches A08 -> stale gap until session end.

ISOLATED REPRO (combo tree A01r2 29cadde + A08 5509950), synthetic bootstrap pipeline.
The synthetic capture does not emit health, so the A02 listener call is reproduced by invoking the exact callback
A01 registers with capture.set_health_listener() (SessionRuntime._on_capture_health, session.py:447/610).
A02 @7769f13 emits health only on change (service.py _set_health_locked -> `if changed`), so there is no re-send.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from proctor_contracts.v1 import Component, Health, HealthStatus, ObservationMsg

from repro_common import client, evidence_store, make_app, record_stream, running_synthetic

base = Path(sys.argv[1])
data = base / "data"
shutil.rmtree(data, ignore_errors=True)
app = make_app(data)
seen = record_stream(app)
with client(app) as c:
    sid = running_synthetic(c)
    rt = app.state.proctor["manager"].runtime(sid)
    time.sleep(1.0)
    t_disc = rt.clock.now_ms()
    rt._on_capture_health(Health(component=Component.CAPTURE, status=HealthStatus.UNAVAILABLE, code="camera_disconnected",
                                 message="camera lost (simulated A02 listener call)"))
    time.sleep(1.0)
    c.post(f"/v1/sessions/{sid}/pause", json={"reason": "teacher pause"})
    time.sleep(0.5)
    t_ok = rt.clock.now_ms()
    rt._on_capture_health(Health(component=Component.CAPTURE, status=HealthStatus.OK, code="running",
                                 message="camera back (simulated A02 listener call)"))
    ev = {"action": "exam_mode_released", "enforcement": "allowed", "mechanism": "electron.kiosk", "scope": "window",
          "client_seq": 7, "client_wall_time": "2026-10-08T09:00:05Z",
          "detail": {"shortcut": "session_paused", "process_name": None, "duration_ms": None}}
    ack = c.post(f"/v1/sessions/{sid}/environment/events", json={"session_id": sid, "events": [ev]}).json()
    time.sleep(0.5)
    c.post(f"/v1/sessions/{sid}/resume")
    time.sleep(4.0)
    fin = c.post(f"/v1/sessions/{sid}/finish").json()
    rep = c.get(f"/v1/sessions/{sid}/report.json").json()
    stream_health = [(m.observation.health.status.value, m.observation.health.code) for m in seen
                     if isinstance(m, ObservationMsg) and m.observation.kind == "health"]
    stored_health = [(o["health"]["status"], o["health"]["code"], round(o["t_session_ms"])) for o in rep["observations"]
                     if o["kind"] == "health"]
    out = {
        "t_disconnect_ms": round(t_disc), "t_camera_ok_ms (during pause)": round(t_ok),
        "window": rep["coverage"]["window"],
        "pauses": rep["coverage"]["pauses"],
        "stream_health_observations": stream_health,
        "A08_stored_health_observations": stored_health,
        "env_event_during_pause_ack": ack,
        "env_event_during_pause_in_A08_report": any(o["observation_id"] in ack["observation_ids"] for o in rep["observations"]),
        "A08_capture_gaps": [g for g in rep["coverage"]["gaps"] if g["component"] == "capture"],
    }
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
